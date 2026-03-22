# services.py
from __future__ import annotations

from tkinter import Tk, filedialog

from dataclasses import dataclass

MODE_OPTIONS = [
    ("All", "all"),
    ("Any", "any"),
    ("E", "easy"),
    ("N", "normal"),
    ("Ex", "ex"),
    ("H", "hard"),
    ("L", "luna"),
    ("Ph", "ph"),
]

NET_UNKNOWN = 0
NET_DEAD = 1
NET_CHECKING = 2
NET_ALIVE = 3
NET_BATTLE = 4


@dataclass
class Post:
    id: str = ""
    rank: str = "any"
    addr: str = ""
    comment: str = ""
    updated_at: float = 0
    stream_url: str = ""
    giuroll: bool = False
    autopunch: bool = False
    match_status: str = ""
    net_status: int = NET_UNKNOWN


def clip_text(p: Post) -> str:
    """コピーするテキスト"""
    return p.addr


def pick_path(title: str = "Select file") -> str | None:
    root = Tk()
    root.withdraw()
    root.attributes("-topmost", True)

    path = filedialog.askopenfilename(
        title=title,
        filetypes=[
            ("Executable", "*.exe"),
            ("All Files", "*.*"),
        ],
    )

    root.destroy()
    return path or None
