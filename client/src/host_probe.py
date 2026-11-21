"""Non-playing RTT probes using a persistent, continuously drained UDP socket."""
from __future__ import annotations

import ipaddress
import os
import socket
import struct
import time
from typing import Optional

from probe_transport import ProbeCache, get_transport

AUTOPUNCH_RELAY = os.environ.get("ASOBBY_AUTOPUNCH_RELAY", "delthas.fr:14763")
GIUROLL_PING = b"\x6c\x00"
GIUROLL_PONG = b"\x6d\x61"
_rtt_cache = ProbeCache(ttl=5.0)


def soku_echo_packet(
    should_match: bool = False,
    profile_name: str = "asobby",
) -> bytes:
    profile_name_bytes = str.encode(profile_name, "shift-jis")
    return bytes.fromhex(
        "05"
        "6e7365d9" "ffc46e48" "8d7ca192" "31347295"
        "00000000" "28000000"
        f"{int(should_match):02}"
        f"{len(profile_name_bytes).to_bytes(1, 'big').hex()}"
        f"{profile_name_bytes.hex():0<48}"
        "00000000" "00000000" "00000000" "0000"
    )


def is_valid_reply(data: bytes) -> bool:
    if len(data) == 5 and data[0] == 0x07:
        return int.from_bytes(data[1:], "little") in (0, 1)
    if len(data) >= 21 and data[0] == 0x08:
        return data[5:7] == b"\x02\x00" and int.from_bytes(data[7:9], "big") > 0
    return len(data) >= 81 and data[0] == 0x06


def _rtt(sock, target, *, giuroll=False, timeout_sec=1.0, attempts=2):
    # Known Giuroll hosts never need spectator requests for RTT, even on loss.
    for _ in range(attempts if giuroll else 1):
        started = time.monotonic()
        reply = sock.exchange(
            target, GIUROLL_PING, lambda data: data == GIUROLL_PONG,
            min(timeout_sec, 0.35),
        )
        if reply is not None:
            return max(1, int((time.monotonic() - started) * 1000))
    if giuroll:
        return None
    # Vanilla/older mods have no dedicated ping; retain bounded compatibility.
    for _ in range(attempts):
        started = time.monotonic()
        reply = sock.exchange(target, soku_echo_packet(), is_valid_reply, timeout_sec)
        if reply is not None:
            return max(1, int((time.monotonic() - started) * 1000))
    return None


def _probe_rtt_direct(host, port, *, giuroll=False, timeout_sec=1.0, attempts=2):
    return _rtt(get_transport(), (host, port), giuroll=giuroll,
                timeout_sec=timeout_sec, attempts=attempts)


def _probe_rtt_autopunch(host, port, *, giuroll=False):
    sock = get_transport()
    relay_host, raw_port = AUTOPUNCH_RELAY.rsplit(":", 1)
    relay_addr = (socket.gethostbyname(relay_host), int(raw_port))
    if not sock.exchange(relay_addr, b"\x00", lambda data: data == b"\x00", 0.7):
        return None
    lookup = struct.pack("!H4sH", sock.getsockname()[1], socket.inet_aton(host), port)

    def valid_mapping(data):
        return (
            len(data) == 8 and data[4:8] == socket.inet_aton(host)
            and int.from_bytes(data[2:4], "big") > 0
            and port in (int.from_bytes(data[:2], "big"), int.from_bytes(data[2:4], "big"))
        )

    mapping = sock.exchange(relay_addr, lookup, valid_mapping, 0.7)
    if mapping is None:
        return None
    # Lookup and RTT must use the same source port for NAT filtering.
    target = (host, int.from_bytes(mapping[2:4], "big"))
    return _rtt(sock, target, giuroll=giuroll, timeout_sec=0.4, attempts=2)


def probe_rtt_ms(
    host: str,
    port: int,
    *,
    autopunch: bool = False,
    giuroll: bool = False,
    timeout_sec: float = 1.0,
    attempts: int = 2,
) -> Optional[int]:
    """Coalesce tabs/callers; a pong proves liveness, not an available slot."""
    try:
        host = str(ipaddress.IPv4Address(host))
        if not 0 < port < 65536:
            return None
    except (TypeError, ValueError):
        return None

    def probe():
        try:
            if autopunch:
                result = _probe_rtt_autopunch(host, port, giuroll=giuroll)
                if result is not None:
                    return result
                # Reuse a recent/in-flight direct check (e.g. paused self-check).
                return probe_rtt_ms(
                    host, port, giuroll=giuroll,
                    timeout_sec=timeout_sec, attempts=attempts,
                )
            return _probe_rtt_direct(
                host, port, giuroll=giuroll, timeout_sec=max(0.01, timeout_sec),
                attempts=max(1, min(2, attempts)),
            )
        except (OSError, ValueError):
            return None

    return _rtt_cache.run((host, port, bool(autopunch), bool(giuroll)), probe)
