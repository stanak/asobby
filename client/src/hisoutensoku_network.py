from __future__ import annotations

import socket
import time
from typing import Callable, Optional

def soku_echo_packet(
    should_match: bool = False,
    profile_name: str = "asobby",
) -> bytes:
    profile_name_bytes = str.encode(profile_name, 'shift-jis')
    return bytes.fromhex(
        '05'
        '647365d9' 'ffc46e48' '8d7ca192' '31347295'
        '00000000' '28000000'
        f'{int(should_match):02}'
        f'{len(profile_name_bytes).to_bytes(1, "big").hex()}'
        f'{profile_name_bytes.hex():0<48}'
        '00000000' '00000000' '00000000' '0000')


def default_is_valid_reply(data: bytes) -> bool:
    """
    ここを天則の実際の応答仕様に合わせて調整する。
    いったんは「何か返ってきたら成功」扱いの仮実装。
    """
    return bool(data)


def send_udp_probe_once(
    host: str,
    port: int,
    packet: bytes,
    *,
    timeout_sec: float = 0.8,
) -> Optional[bytes]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.settimeout(timeout_sec)
        sock.sendto(packet, (host, port))
        data, _addr = sock.recvfrom(4096)
        return data
    except socket.timeout:
        return None
    except OSError:
        return None
    finally:
        sock.close()


def check_hostable(
    host: str,
    port: int,
    *,
    should_match: bool = False,
    profile_name: str = "asobby",
    attempts: int = 5,
    interval_sec: float = 1.0,
    timeout_sec: float = 0.8,
    min_successes: int = 1,
    is_valid_reply: Callable[[bytes], bool] = default_is_valid_reply,
) -> bool:
    """
    1秒おきに最大5回UDP probeを送り、host可能かを判定する。
    - 1回でも有効返信があれば host可能、とするなら min_successes=1
    - 過半数必要なら min_successes=3 など
    """
    packet = soku_echo_packet(
        should_match=should_match,
        profile_name=profile_name,
    )

    success_count = 0

    for i in range(attempts):
        reply = send_udp_probe_once(
            host,
            port,
            packet,
            timeout_sec=timeout_sec,
        )
        if reply is not None and is_valid_reply(reply):
            success_count += 1
            if success_count >= min_successes:
                return True

        if i != attempts - 1:
            time.sleep(interval_sec)

    return False
