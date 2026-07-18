"""非想天則ホストへの UDP echo プローブ (viewer 側 RTT 計測用)。"""
from __future__ import annotations

import ipaddress
import os
import socket
import struct
import time
from typing import Optional

AUTOPUNCH_RELAY = os.environ.get("ASOBBY_AUTOPUNCH_RELAY", "delthas.fr:14763")


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
    return len(data) >= 1 and data[0] in (0x07, 0x08)


def _autopunch_nat_port(host: str, port: int) -> Optional[int]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(("0.0.0.0", 0))
        my_port = sock.getsockname()[1]

        relay_host, relay_port_s = AUTOPUNCH_RELAY.rsplit(":", 1)
        try:
            relay_port = int(relay_port_s)
            if not (0 < relay_port < 65536):
                raise ValueError
            relay_ip = socket.gethostbyname(relay_host)
        except (OSError, ValueError):
            return None

        relay_addr = (relay_ip, relay_port)
        lookup = struct.pack("!H", my_port) + socket.inet_aton(host) + struct.pack("!H", port)
        nat_port: Optional[int] = None
        for _ in range(3):
            try:
                sock.sendto(lookup, relay_addr)
                deadline = time.monotonic() + 1.0
                while time.monotonic() < deadline:
                    remaining = deadline - time.monotonic()
                    sock.settimeout(remaining)
                    data, addr = sock.recvfrom(4096)
                    if addr[0] != relay_ip or len(data) != 8:
                        continue
                    candidate_nat_port = struct.unpack("!H", data[2:4])[0]
                    ip = socket.inet_ntoa(data[4:8])
                    if ip == host:
                        nat_port = candidate_nat_port
                        break
                if nat_port is not None:
                    break
            except OSError:
                pass
        return nat_port
    except OSError:
        return None
    finally:
        sock.close()


def probe_host_once(
    host: str,
    port: int,
    packet: bytes,
    *,
    timeout_sec: float = 0.35,
) -> Optional[bytes]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(("0.0.0.0", 0))
        sock.settimeout(timeout_sec)
        sock.sendto(packet, (host, port))
        deadline = time.monotonic() + timeout_sec
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            sock.settimeout(remaining)
            data, addr = sock.recvfrom(4096)
            if addr[0] == host:
                return data
    except (socket.timeout, OSError):
        return None
    finally:
        sock.close()


def probe_rtt_ms(
    host: str,
    port: int,
    *,
    autopunch: bool = False,
    timeout_sec: float = 0.35,
) -> Optional[int]:
    """ホストへ 1 回 echo を送り、有効応答までの RTT (ms) を返す。"""
    try:
        ipaddress.IPv4Address(host)
    except ValueError:
        return None
    if not (0 < port < 65536):
        return None

    probe_port = port
    if autopunch:
        nat_port = _autopunch_nat_port(host, port)
        if nat_port is None:
            return None
        probe_port = nat_port

    packet = soku_echo_packet()
    started = time.monotonic()
    reply = probe_host_once(host, probe_port, packet, timeout_sec=timeout_sec)
    if reply is None or not is_valid_reply(reply):
        return None
    elapsed_ms = int((time.monotonic() - started) * 1000)
    return max(elapsed_ms, 1)
