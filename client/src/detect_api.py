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
