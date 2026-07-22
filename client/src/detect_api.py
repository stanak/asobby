from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

Mode = Literal["idle", "host_wait", "charsel", "loading", "battle", "other"]


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

    # ネット対戦の立場 ("host" / "client" / "watch" / None)
    net_side: Optional[str] = None

    # 対戦勝敗 (対戦/キャラセレ/ロード中で読む。KO 確定は btl_mode==5)
    btl_mode: Optional[int] = None
    lwin: Optional[int] = None
    rwin: Optional[int] = None

    # PBATTLEMGR 上の確定キャラ ID (キャラセレカーソルではない)
    battle_lchar_id: Optional[int] = None
    battle_rchar_id: Optional[int] = None

    # th123.exe のフルパス (リプレイ保存先の解決用)
    exe_path: str = ""

    # 検知系の異常。"" = 正常 / "access_denied" = プロセスは見つかったが
    # メモリを読めない (ゲームが管理者権限で動いている可能性が高い)
    detect_error: str = ""

    # 検知に使った生の値のスナップショット (診断ログ用)
    raw: str = ""

    # システムフォルダ外から読まれているモジュール名 (診断ログ用)
    modules: str = ""

    # server チェーン周辺のメモリダンプ (異常時と host_wait 時のみ)
    dump: str = ""
