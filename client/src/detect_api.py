from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

Mode = Literal["idle", "host_wait", "charsel", "battle", "other"]


@dataclass(frozen=True)
class DetectionState:
    alive: bool
    mode: Mode
    port: Optional[int]

    # tool detection
    giuroll: bool
    autopunch: bool

    # profile names (cp932)
    lprof: str
    rprof: str

    # char info
    lchar_id: Optional[int]
    rchar_id: Optional[int]
    lchar_name: str
    rchar_name: str

    # 対戦勝敗 (NET_BATTLE_SCENES 時のみ読む。KO 確定は btl_mode==5)
    btl_mode: Optional[int] = None
    lwin: Optional[int] = None
    rwin: Optional[int] = None
