# services.py
from __future__ import annotations

from dataclasses import dataclass

from i18n import post_type_label, post_type_options, t

__version__ = "0.4.19"

RANK_LABEL = {
    "easy": "E",
    "normal": "N",
    "ex": "Ex",
    "hard": "H",
    "luna": "L",
    "ph": "Ph",
}

NET_UNKNOWN = 0
NET_DEAD = 1
NET_CHECKING = 2
NET_ALIVE = 3
NET_BATTLE = 4


def format_system_rank(rank: str, rating: float | None = None) -> str:
    label = RANK_LABEL.get(rank, rank or "?")
    if rank == "ph" and rating is not None:
        return f"Ph ({rating})"
    return label


@dataclass
class Post:
    id: str = ""
    post_type: str = "casual"
    addr: str = ""
    comment: str = ""
    updated_at: float = 0
    stream_url: str = ""
    giuroll: bool = False
    autopunch: bool = False
    match_status: str = ""
    net_status: int = NET_UNKNOWN
    challenge_upper: bool = False


def edit_post_settings(parent, current: dict, on_ok) -> None:
    """投稿設定を編集する Toplevel ダイアログ。tk メインスレッドで呼ぶこと。

    OK 時に on_ok({"post_type", "stream_url", "stream_url_presets", "comment", "comment_presets"}) を呼ぶ。
    comment_presets / stream_url_presets はテキスト欄の 1 行 1 件。
    comment / stream_url (現在有効な値) はプリセットに残っていればそのまま、
    消えていれば先頭のプリセットになる。
    """
    import tkinter as tk
    from tkinter import ttk

    label_by_value = {v: label for label, v in post_type_options()}

    win = tk.Toplevel(parent)
    win.title(t("settings.title"))
    win.attributes("-topmost", True)
    win.resizable(False, False)

    post_type_var = tk.StringVar(
        value=label_by_value.get(
            current.get("post_type", "casual"), post_type_label("casual")
        )
    )

    presets = [str(x) for x in current.get("comment_presets", []) if str(x).strip()]
    active_comment = str(current.get("comment", ""))
    if not presets and active_comment:
        # 旧設定 (単一コメント) からの引き継ぎ
        presets = [active_comment]

    stream_presets = [str(x) for x in current.get("stream_url_presets", []) if str(x).strip()]
    active_stream = str(current.get("stream_url", ""))
    if not stream_presets and active_stream:
        # 旧設定 (単一 stream_url) からの引き継ぎ
        stream_presets = [active_stream]

    frame = ttk.Frame(win, padding=12)
    frame.grid(sticky="nsew")

    ttk.Label(frame, text=t("settings.post_mode")).grid(
        row=0, column=0, sticky="e", pady=4, padx=(0, 8)
    )
    post_type_box = ttk.Combobox(
        frame,
        textvariable=post_type_var,
        values=[label for label, _ in post_type_options()],
        state="readonly",
        width=12,
    )
    post_type_box.grid(row=0, column=1, sticky="w", pady=4)

    challenge_upper_var = tk.BooleanVar(
        value=bool(current.get("challenge_upper", False))
    )
    value_by_label = {label: v for label, v in post_type_options()}
    challenge_upper_chk = ttk.Checkbutton(
        frame,
        text=t("settings.challenge_upper"),
        variable=challenge_upper_var,
    )
    challenge_upper_chk.grid(row=1, column=1, sticky="w", pady=(0, 4))

    ping_warn_var = tk.StringVar(
        value=str(int(current.get("ping_warn_ms", 60) or 60))
    )
    ping_warn_giuroll_var = tk.StringVar(
        value=str(int(current.get("ping_warn_giuroll_ms", 100) or 100))
    )
    ttk.Label(frame, text=t("settings.ping_warn_ms")).grid(
        row=2, column=0, sticky="e", pady=4, padx=(0, 8)
    )
    ping_warn_entry = ttk.Entry(frame, textvariable=ping_warn_var, width=8)
    ping_warn_entry.grid(row=2, column=1, sticky="w", pady=4)
    ttk.Label(frame, text=t("settings.ping_warn_ms_unit")).grid(
        row=2, column=1, sticky="w", padx=(72, 0), pady=4
    )
    ttk.Label(frame, text=t("settings.ping_warn_giuroll_ms")).grid(
        row=3, column=0, sticky="e", pady=4, padx=(0, 8)
    )
    ping_warn_giuroll_entry = ttk.Entry(frame, textvariable=ping_warn_giuroll_var, width=8)
    ping_warn_giuroll_entry.grid(row=3, column=1, sticky="w", pady=4)
    ttk.Label(frame, text=t("settings.ping_warn_giuroll_ms_unit")).grid(
        row=3, column=1, sticky="w", padx=(72, 0), pady=4
    )

    def sync_challenge_upper_state(*_args) -> None:
        selected = value_by_label.get(post_type_var.get(), "casual")
        state = "normal" if selected == "ranked" else "disabled"
        challenge_upper_chk.configure(state=state)
        if selected != "ranked":
            challenge_upper_var.set(False)

    post_type_var.trace_add("write", sync_challenge_upper_state)
    sync_challenge_upper_state()

    ttk.Label(frame, text=t("settings.stream_presets"), justify="right").grid(
        row=4, column=0, sticky="ne", pady=4, padx=(0, 8)
    )
    stream_text = tk.Text(frame, width=44, height=4)
    stream_text.grid(row=4, column=1, sticky="we", pady=4)
    if stream_presets:
        stream_text.insert("1.0", "\n".join(stream_presets))

    ttk.Label(frame, text=t("settings.comment_presets"), justify="right").grid(
        row=5, column=0, sticky="ne", pady=4, padx=(0, 8)
    )
    comment_text = tk.Text(frame, width=44, height=6)
    comment_text.grid(row=5, column=1, sticky="we", pady=4)
    if presets:
        comment_text.insert("1.0", "\n".join(presets))

    hint = ttk.Label(
        frame,
        text=t("settings.hint"),
        foreground="#888",
    )
    hint.grid(row=6, column=1, sticky="w")

    def do_ok() -> None:
        post_type = value_by_label.get(post_type_var.get(), "casual")
        try:
            ping_warn_ms = int(str(ping_warn_var.get()).strip())
        except ValueError:
            ping_warn_ms = 60
        ping_warn_ms = max(1, min(5000, ping_warn_ms))
        try:
            ping_warn_giuroll_ms = int(str(ping_warn_giuroll_var.get()).strip())
        except ValueError:
            ping_warn_giuroll_ms = 100
        ping_warn_giuroll_ms = max(1, min(5000, ping_warn_giuroll_ms))
        comment_lines = [l.strip() for l in comment_text.get("1.0", "end").splitlines() if l.strip()]
        if active_comment in comment_lines:
            comment = active_comment
        else:
            comment = comment_lines[0] if comment_lines else ""
        stream_lines = [l.strip() for l in stream_text.get("1.0", "end").splitlines() if l.strip()]
        if active_stream in stream_lines:
            stream_url = active_stream
        else:
            stream_url = stream_lines[0] if stream_lines else ""
        result = {
            "post_type": post_type,
            "challenge_upper": bool(challenge_upper_var.get()) and post_type == "ranked",
            "ping_warn_ms": ping_warn_ms,
            "ping_warn_giuroll_ms": ping_warn_giuroll_ms,
            "stream_url": stream_url,
            "stream_url_presets": stream_lines,
            "comment": comment,
            "comment_presets": comment_lines,
        }
        win.destroy()
        on_ok(result)

    def do_cancel() -> None:
        win.destroy()

    btns = ttk.Frame(frame)
    btns.grid(row=7, column=0, columnspan=2, sticky="e", pady=(12, 0))
    ttk.Button(btns, text=t("settings.ok"), command=do_ok).grid(
        row=0, column=0, padx=(0, 8)
    )
    ttk.Button(btns, text=t("settings.cancel"), command=do_cancel).grid(row=0, column=1)

    win.protocol("WM_DELETE_WINDOW", do_cancel)
    win.bind("<Escape>", lambda _e: do_cancel())

    # 画面中央に配置してフォーカスを与える
    win.update_idletasks()
    x = (win.winfo_screenwidth() - win.winfo_width()) // 2
    y = (win.winfo_screenheight() - win.winfo_height()) // 2
    win.geometry(f"+{x}+{y}")
    win.lift()
    win.focus_force()
