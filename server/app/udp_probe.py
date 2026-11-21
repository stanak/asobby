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
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable

from probe_transport import get_transport

GIUROLL_PING = b"\x6c\x00"
GIUROLL_PONG = b"\x6d\x61"


@dataclass(frozen=True)
class Result:
    alive: bool = False
    state: str = "unknown"
    giuroll: bool = False  # positive detection only, never infer absence
    direct: bool = False
    autopunch: bool = False
    inconclusive: bool = False  # local/relay outage is not host death
    timed_out: bool = False  # admission deadline, not proof the host is dead
    state_checked: bool = True  # False: pong only; preserve the last state sample


class ProbeDeadline(Exception):
    pass


def remaining_time(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ProbeDeadline
    return remaining


@contextmanager
def probe_lock(lock, deadline):
    if deadline is None:
        with lock:
            yield
    else:
        if not lock.acquire(timeout=remaining_time(deadline)):
            raise ProbeDeadline
        try:
            remaining_time(deadline)
            yield
        finally:
            lock.release()


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
    return sock.exchange(target, payload, accept, timeout)


def probe_reply(sock, target, echo, *, try_giuroll=True, known_giuroll=False, query_state=False,
                timeout=0.35, deadline=None):
    """Pong is liveness only. Ask for spectator state only when it is needed."""
    def attempt(payload, accept):
        budget = timeout if deadline is None else min(timeout, remaining_time(deadline))
        return exchange(sock, target, payload, accept, budget)

    pong = attempt(GIUROLL_PING, lambda data: data == GIUROLL_PONG) if try_giuroll else None
    if pong is not None and not query_state:
        return pong, True, False
    if known_giuroll and not query_state:
        return None, False, False  # Packet loss is not a reason to try a session request.
    try:
        reply = attempt(echo, lambda data: game_state(data) is not None)
    except ProbeDeadline:
        if pong is None:
            raise
        return pong, True, True
    if reply is None and not try_giuroll:
        pong = attempt(GIUROLL_PING, lambda data: data == GIUROLL_PONG)
    return reply if reply is not None else pong, pong is not None, True


def check(
    addr: str, *, echo: bytes, lock, bind_host: str | None, bind_port: int,
    relay: str, prefer_autopunch=False, detect_giuroll=True, query_state=False, known_giuroll=False,
    deadline: float | None = None,
) -> Result:
    host, port = public_address(addr)  # revalidate restored data before sending

    def attempt(sock, target, payload, accept, timeout=0.35):
        if deadline is not None:
            timeout = min(timeout, remaining_time(deadline))
        return exchange(sock, target, payload, accept, timeout)

    def host_check(sock, target, *, direct=False, autopunch=False):
        reply, giuroll, state_checked = probe_reply(
            sock, target, echo, try_giuroll=detect_giuroll or known_giuroll, known_giuroll=known_giuroll,
            query_state=query_state, deadline=deadline,
        )
        return Result(
            alive=reply is not None,
            state=(game_state(reply) or "unknown") if reply is not None else "unknown",
            giuroll=giuroll, direct=direct, autopunch=autopunch,
            state_checked=state_checked,
        )

    # Shared with existing game probes: the fixed Fly UDP port must not have
    # competing readers. A cancelled async caller cannot release this lock
    # while its thread is still sending/receiving.
    try:
        with probe_lock(lock, deadline):
            sock = get_transport(bind_host, bind_port)
            # Check direct BEFORE relay lookup, which opens a hole to us.
            if not prefer_autopunch:
                result = host_check(sock, (host, port), direct=True)
                if result.alive:
                    return result

            relay_host, relay_port = relay.rsplit(":", 1)
            relay_addr = (socket.gethostbyname(relay_host), int(relay_port))
            if not attempt(sock, relay_addr, b"\x00", lambda data: data == b"\x00", 0.7):
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

            mapping = attempt(sock, relay_addr, lookup, valid_mapping, 0.7)
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
    except ProbeDeadline:
        return Result(timed_out=True)
    except (OSError, ValueError):
        return Result(inconclusive=True)
