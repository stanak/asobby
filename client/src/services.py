# services.py
from __future__ import annotations

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


def edit_post_settings(parent, current: dict, on_ok) -> None:
    """投稿設定を編集する Toplevel ダイアログ。tk メインスレッドで呼ぶこと。

    OK 時に on_ok({"rank", "stream_url", "comment", "comment_presets"}) を呼ぶ。
    comment_presets はテキスト欄の 1 行 1 件。comment (現在有効なコメント) は
    プリセットに残っていればそのまま、消えていれば先頭のプリセットになる。
    """
    import tkinter as tk
    from tkinter import ttk

    # "All" はフィルタ専用なので募集ランクの選択肢から除外する
    rank_options = MODE_OPTIONS[1:]
    label_by_value = {v: label for label, v in rank_options}

    win = tk.Toplevel(parent)
    win.title("asobby 投稿設定")
    win.attributes("-topmost", True)
    win.resizable(False, False)

    rank_var = tk.StringVar(value=label_by_value.get(current.get("rank", "any"), "Any"))
    stream_var = tk.StringVar(value=current.get("stream_url", ""))

    presets = [str(x) for x in current.get("comment_presets", []) if str(x).strip()]
    active_comment = str(current.get("comment", ""))
    if not presets and active_comment:
        # 旧設定 (単一コメント) からの引き継ぎ
        presets = [active_comment]

    frame = ttk.Frame(win, padding=12)
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

    ttk.Label(frame, text="Stream URL:").grid(row=1, column=0, sticky="e", pady=4, padx=(0, 8))
    ttk.Entry(frame, textvariable=stream_var, width=44).grid(row=1, column=1, sticky="we", pady=4)

    ttk.Label(frame, text="コメント候補\n(1行1件):", justify="right").grid(
        row=2, column=0, sticky="ne", pady=4, padx=(0, 8)
    )
    comment_text = tk.Text(frame, width=44, height=6)
    comment_text.grid(row=2, column=1, sticky="we", pady=4)
    if presets:
        comment_text.insert("1.0", "\n".join(presets))

    hint = ttk.Label(
        frame,
        text="使用するコメントはトレイの「コメント切替」で選べます",
        foreground="#888",
    )
    hint.grid(row=3, column=1, sticky="w")

    def do_ok() -> None:
        value_by_label = {label: v for label, v in rank_options}
        lines = [l.strip() for l in comment_text.get("1.0", "end").splitlines() if l.strip()]
        if active_comment in lines:
            comment = active_comment
        else:
            comment = lines[0] if lines else ""
        result = {
            "rank": value_by_label.get(rank_var.get(), "any"),
            "stream_url": stream_var.get(),
            "comment": comment,
            "comment_presets": lines,
        }
        win.destroy()
        on_ok(result)

    def do_cancel() -> None:
        win.destroy()

    btns = ttk.Frame(frame)
    btns.grid(row=4, column=0, columnspan=2, sticky="e", pady=(12, 0))
    ttk.Button(btns, text="OK", command=do_ok).grid(row=0, column=0, padx=(0, 8))
    ttk.Button(btns, text="キャンセル", command=do_cancel).grid(row=0, column=1)

    win.protocol("WM_DELETE_WINDOW", do_cancel)
    win.bind("<Escape>", lambda _e: do_cancel())

    # 画面中央に配置してフォーカスを与える
    win.update_idletasks()
    x = (win.winfo_screenwidth() - win.winfo_width()) // 2
    y = (win.winfo_screenheight() - win.winfo_height()) // 2
    win.geometry(f"+{x}+{y}")
    win.lift()
    win.focus_force()
