"""Bounded, non-playing probes for server-managed Hisoutensoku listings.

Never follow addresses from game replies. AutoPunch mappings come only from
the server-configured relay, must refer to the requested public IPv4, and are
used on the same socket as the lookup (required by NAT filtering).
"""
from __future__ import annotations

import ipaddress
import socket
import struct
import time
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class Result:
    alive: bool = False
    state: str = "unknown"
    giuroll: bool = False  # positive detection only, never infer absence
    direct: bool = False
    autopunch: bool = False
    inconclusive: bool = False  # local/relay outage is not host death


def public_address(addr: str) -> tuple[str, int]:
    try:
        host, raw_port = addr.rsplit(":", 1)
        ip = ipaddress.IPv4Address(host)
        port = int(raw_port)
        if (
            not ip.is_global or ip.is_multicast or ip.is_reserved
            or not raw_port.isascii() or not raw_port.isdecimal()
            or not 1 <= port <= 65535
        ):
            raise ValueError()
        return str(ip), port
    except (ValueError, TypeError):
        raise ValueError("addr must be a public IPv4:port") from None


def same_address(left: str, right: str) -> bool:
    """Compare endpoints, including noncanonical ports from older clients."""
    try:
        return public_address(left) == public_address(right)
    except ValueError:
        return False


def game_state(reply: bytes) -> str | None:
    # INIT_ERROR's reason is a little-endian u32, not just an opcode.
    if len(reply) == 5 and reply[0] == 0x07:
        reason = int.from_bytes(reply[1:5], "little")
        if reason == 1:  # spectating is invalid in the current game state
            return "waiting"
        if reason == 0:  # spectating disabled: cannot infer available/busy
            return "unknown"
    if len(reply) >= 21 and reply[0] == 0x08:  # REDIRECT + sockaddr_in
        port = int.from_bytes(reply[7:9], "big")
        if port and reply[5:7] == b"\x02\x00":
            return "connecting"
    if len(reply) >= 81 and reply[0] == 0x06:  # INIT_SUCCESS (spectator)
        return "connecting"
    return None


def exchange(sock, target, payload, accept: Callable[[bytes], bool], timeout=0.35):
    sock.sendto(payload, target)
    deadline = time.monotonic() + timeout
    # Also bound the packet count: invalid traffic cannot monopolize a worker.
    for _ in range(64):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        sock.settimeout(remaining)
        try:
            data, sender = sock.recvfrom(4096)
        except (socket.timeout, ConnectionResetError, ConnectionRefusedError):
            break
        if sender[:2] == target and accept(data):
            return data
    return None


def check(
    addr: str, *, echo: bytes, lock, bind_host: str | None, bind_port: int,
    relay: str, prefer_autopunch=False, detect_giuroll=True,
) -> Result:
    host, port = public_address(addr)  # revalidate restored data before sending

    def host_check(sock, target, *, direct=False, autopunch=False):
        reply = exchange(sock, target, echo, lambda data: game_state(data) is not None)
        # A host may enable Giuroll between periodic capability checks and
        # stop answering the ordinary spectator probe. Before counting that
        # silence as a failure, try its pong on this same verified endpoint.
        # Healthy ordinary hosts still keep the slower discovery cadence.
        giuroll = bool((detect_giuroll or reply is None) and exchange(
            sock, target, b"\x6c\x00", lambda data: data == b"\x6d\x61",
        ))
        return Result(
            alive=reply is not None or giuroll,
            state=game_state(reply) if reply is not None else "unknown",
            giuroll=giuroll, direct=direct, autopunch=autopunch,
        )

    # Shared with existing game probes: the fixed Fly UDP port must not have
    # competing readers. A cancelled async caller cannot release this lock
    # while its thread is still sending/receiving.
    with lock:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                if bind_host:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    sock.bind((bind_host, bind_port))
                else:
                    sock.bind(("0.0.0.0", 0))
                # Do this BEFORE relay lookup, which opens a hole to us and
                # could make an AP-only host subsequently appear direct.
                if not prefer_autopunch:
                    result = host_check(sock, (host, port), direct=True)
                    if result.alive:
                        return result

                relay_host, relay_port = relay.rsplit(":", 1)
                relay_addr = (socket.gethostbyname(relay_host), int(relay_port))
                if not exchange(sock, relay_addr, b"\x00", lambda data: data == b"\x00", 0.7):
                    return Result(inconclusive=True)
                lookup = struct.pack("!H4sH", sock.getsockname()[1], socket.inet_aton(host), port)

                def valid_mapping(data):
                    return (
                        len(data) == 8 and data[4:8] == socket.inet_aton(host)
                        and int.from_bytes(data[0:2], "big") > 0
                        and int.from_bytes(data[2:4], "big") > 0
                        and port in (
                            int.from_bytes(data[0:2], "big"), int.from_bytes(data[2:4], "big"),
                        )
                    )

                mapping = exchange(sock, relay_addr, lookup, valid_mapping, 0.7)
                if mapping is None:
                    return Result()
                target = (host, int.from_bytes(mapping[2:4], "big"))
                # Allow the host's hole-punch loop to run; never establish a
                # playing connection or send rollback input packets.
                for _ in range(2):
                    result = host_check(sock, target, autopunch=True)
                    if result.alive:
                        return result
                return Result()
        except (OSError, ValueError):
            return Result(inconclusive=True)
