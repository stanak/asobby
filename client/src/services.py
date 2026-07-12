# services.py
from __future__ import annotations

from tkinter import Tk, filedialog

from dataclasses import dataclass

__version__ = "0.2.0"

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


def edit_post_settings(current: dict) -> dict | None:
    """投稿設定（rank / comment / stream_url）を編集するダイアログ。

    OK で {"rank", "comment", "stream_url"} を返し、キャンセルで None を返す。
    """
    from tkinter import StringVar, ttk

    # "All" はフィルタ専用なので募集ランクの選択肢から除外する
    rank_options = MODE_OPTIONS[1:]
    label_by_value = {v: label for label, v in rank_options}

    root = Tk()
    root.title("asobby 投稿設定")
    root.attributes("-topmost", True)
    root.resizable(False, False)

    rank_var = StringVar(value=label_by_value.get(current.get("rank", "any"), "Any"))
    comment_var = StringVar(value=current.get("comment", ""))
    stream_var = StringVar(value=current.get("stream_url", ""))
    result: dict | None = None

    frame = ttk.Frame(root, padding=12)
    frame.grid(sticky="nsew")

    ttk.Label(frame, text="Post Rank:").grid(row=0, column=0, sticky="e", pady=4, padx=(0, 8))
    rank_box = ttk.Combobox(
        frame,
        textvariable=rank_var,
        values=[label for label, _ in rank_options],
        state="readonly",
        width=12,
    )
    rank_box.grid(row=0, column=1, sticky="w", pady=4)

    ttk.Label(frame, text="Comment:").grid(row=1, column=0, sticky="e", pady=4, padx=(0, 8))
    ttk.Entry(frame, textvariable=comment_var, width=40).grid(row=1, column=1, sticky="we", pady=4)

    ttk.Label(frame, text="Stream URL:").grid(row=2, column=0, sticky="e", pady=4, padx=(0, 8))
    ttk.Entry(frame, textvariable=stream_var, width=40).grid(row=2, column=1, sticky="we", pady=4)

    def on_ok() -> None:
        nonlocal result
        value_by_label = {label: v for label, v in rank_options}
        result = {
            "rank": value_by_label.get(rank_var.get(), "any"),
            "comment": comment_var.get(),
            "stream_url": stream_var.get(),
        }
        root.destroy()

    def on_cancel() -> None:
        root.destroy()

    btns = ttk.Frame(frame)
    btns.grid(row=3, column=0, columnspan=2, sticky="e", pady=(12, 0))
    ttk.Button(btns, text="OK", command=on_ok).grid(row=0, column=0, padx=(0, 8))
    ttk.Button(btns, text="キャンセル", command=on_cancel).grid(row=0, column=1)

    root.protocol("WM_DELETE_WINDOW", on_cancel)
    root.bind("<Return>", lambda _e: on_ok())
    root.bind("<Escape>", lambda _e: on_cancel())

    # 画面中央に配置
    root.update_idletasks()
    x = (root.winfo_screenwidth() - root.winfo_width()) // 2
    y = (root.winfo_screenheight() - root.winfo_height()) // 2
    root.geometry(f"+{x}+{y}")

    root.mainloop()
    return result
