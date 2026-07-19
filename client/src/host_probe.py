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


def _relay_addr() -> Optional[tuple[str, tuple[str, int]]]:
    relay_host, relay_port_s = AUTOPUNCH_RELAY.rsplit(":", 1)
    try:
        relay_port = int(relay_port_s)
        if not (0 < relay_port < 65536):
            raise ValueError
        relay_ip = socket.gethostbyname(relay_host)
    except (OSError, ValueError):
        return None
    return relay_ip, (relay_ip, relay_port)


def probe_host_once_on_socket(
    sock: socket.socket,
    host: str,
    port: int,
    packet: bytes,
    *,
    timeout_sec: float = 0.35,
) -> Optional[bytes]:
    try:
        sock.sendto(packet, (host, port))
        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            sock.settimeout(remaining)
            data, addr = sock.recvfrom(4096)
            if addr[0] == host:
                return data
    except (socket.timeout, OSError):
        return None
    return None


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
        return probe_host_once_on_socket(
            sock, host, port, packet, timeout_sec=timeout_sec
        )
    except OSError:
        return None
    finally:
        sock.close()


def _probe_rtt_direct(
    host: str,
    port: int,
    *,
    timeout_sec: float = 1.0,
    attempts: int = 2,
) -> Optional[int]:
    packet = soku_echo_packet()
    tries = max(1, int(attempts))
    for i in range(tries):
        started = time.monotonic()
        reply = probe_host_once(host, port, packet, timeout_sec=timeout_sec)
        if reply is not None and is_valid_reply(reply):
            elapsed_ms = int((time.monotonic() - started) * 1000)
            return max(elapsed_ms, 1)
        if i + 1 < tries:
            time.sleep(0.05)
    return None


def _probe_rtt_autopunch(host: str, port: int) -> Optional[int]:
    """AutoPunch ホストへ RTT を測る (server check_hostable_autopunch と同じ 3 段階)。"""
    relay = _relay_addr()
    if relay is None:
        return None
    relay_ip, relay_addr = relay

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(("0.0.0.0", 0))
        my_port = sock.getsockname()[1]

        # Stage 1: リレー到達性
        relay_ok = False
        for _ in range(3):
            try:
                sock.sendto(b"\x00", relay_addr)
                deadline = time.monotonic() + 1.0
                while time.monotonic() < deadline:
                    remaining = deadline - time.monotonic()
                    sock.settimeout(remaining)
                    data, addr = sock.recvfrom(4096)
                    if addr[0] == relay_ip and len(data) == 1:
                        relay_ok = True
                        break
                if relay_ok:
                    break
            except OSError:
                pass
        if not relay_ok:
            return None

        # Stage 2: NAT ポート lookup (lookup した socket で echo する)
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
                    if ip == host and 0 < candidate_nat_port < 65536:
                        nat_port = candidate_nat_port
                        break
                if nat_port is not None:
                    break
            except OSError:
                pass
        if nat_port is None:
            return None

        # Stage 3: NAT ポートへ echo (server と同じ 10 回)
        packet = soku_echo_packet()
        for _ in range(10):
            started = time.monotonic()
            try:
                sock.sendto(packet, (host, nat_port))
                deadline = time.monotonic() + 0.4
                while time.monotonic() < deadline:
                    remaining = deadline - time.monotonic()
                    sock.settimeout(remaining)
                    data, addr = sock.recvfrom(4096)
                    if addr[0] != host:
                        continue
                    if is_valid_reply(data):
                        elapsed_ms = int((time.monotonic() - started) * 1000)
                        return max(elapsed_ms, 1)
            except OSError:
                pass
        return None
    except OSError:
        return None
    finally:
        sock.close()


def probe_rtt_ms(
    host: str,
    port: int,
    *,
    autopunch: bool = False,
    timeout_sec: float = 1.0,
    attempts: int = 2,
) -> Optional[int]:
    """ホストへ echo を送り、有効応答までの RTT (ms) を返す。"""
    try:
        ipaddress.IPv4Address(host)
    except ValueError:
        return None
    if not (0 < port < 65536):
        return None

    if autopunch:
        ap = _probe_rtt_autopunch(host, port)
        if ap is not None:
            return ap

    direct = _probe_rtt_direct(
        host,
        port,
        timeout_sec=timeout_sec,
        attempts=attempts,
    )
    if direct is not None:
        return direct

    return None
