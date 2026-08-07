# services.py
from __future__ import annotations

from dataclasses import dataclass

from i18n import post_type_label, post_type_options, t

__version__ = "0.7.21"

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
    direct_reachable: bool = False
    match_status: str = ""
    net_status: int = NET_UNKNOWN


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

    ping_warn_var = tk.StringVar(
        value=str(int(current.get("ping_warn_ms", 60) or 60))
    )
    ping_warn_giuroll_var = tk.StringVar(
        value=str(int(current.get("ping_warn_giuroll_ms", 100) or 100))
    )
    ping_warn_enabled_var = tk.BooleanVar(
        value=bool(current.get("ping_warn_enabled", True))
    )
    ping_warn_enabled_chk = ttk.Checkbutton(
        frame,
        text=t("settings.ping_warn_enabled"),
        variable=ping_warn_enabled_var,
    )
    ping_warn_enabled_chk.grid(row=1, column=1, sticky="w", pady=(0, 4))
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

    value_by_label = {label: v for label, v in post_type_options()}

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
            "ping_warn_enabled": bool(ping_warn_enabled_var.get()),
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


def edit_session_score_notify_settings(parent, current: dict, on_ok) -> None:
    """勝敗数通知の設定ダイアログ。tk メインスレッドで呼ぶこと。"""
    import tkinter as tk
    from tkinter import ttk

    win = tk.Toplevel(parent)
    win.title(t("session_score.settings_title"))
    win.attributes("-topmost", True)
    win.resizable(False, False)

    frame = ttk.Frame(win, padding=12)
    frame.grid(sticky="nsew")

    enabled_var = tk.BooleanVar(
        value=bool(current.get("session_score_notify_enabled", False))
    )
    ttk.Checkbutton(
        frame,
        text=t("session_score.enabled"),
        variable=enabled_var,
    ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))

    mode_labels = {
        "all": t("session_score.mode.all"),
        "rules": t("session_score.mode.rules"),
    }
    label_by_mode = mode_labels
    mode_by_label = {label: key for key, label in mode_labels.items()}
    initial_mode = str(current.get("session_score_notify_mode", "all"))
    if initial_mode not in ("all", "rules"):
        initial_mode = "rules"
    mode_var = tk.StringVar(value=mode_labels[initial_mode])

    ttk.Label(frame, text=t("session_score.mode_label")).grid(
        row=1, column=0, sticky="e", padx=(0, 8), pady=4
    )
    mode_box = ttk.Combobox(
        frame,
        textvariable=mode_var,
        values=[mode_labels["all"], mode_labels["rules"]],
        state="readonly",
        width=28,
    )
    mode_box.grid(row=1, column=1, columnspan=2, sticky="w", pady=4)

    rules_frame = ttk.LabelFrame(frame, text=t("session_score.rules_label"), padding=8)
    rules_frame.grid(row=2, column=0, columnspan=3, sticky="we", pady=(8, 4))

    rule_rows: list[dict] = []

    def remove_rule_row(row_info: dict) -> None:
        row_info["frame"].destroy()
        if row_info in rule_rows:
            rule_rows.remove(row_info)
        sync_rules_state()

    def add_rule_row(count: int = 1, kind: str = "win") -> None:
        row = ttk.Frame(rules_frame)
        row.pack(fill="x", pady=2)
        count_var = tk.StringVar(value=str(max(1, int(count))))
        kind_label = t("session_score.kind.win" if kind == "win" else "session_score.kind.loss")
        kind_var = tk.StringVar(value=kind_label)
        kind_values = [t("session_score.kind.win"), t("session_score.kind.loss")]
        ttk.Label(row, text=t("session_score.rule_prefix")).pack(side="left")
        ttk.Entry(row, textvariable=count_var, width=5).pack(side="left", padx=(4, 4))
        kind_box = ttk.Combobox(
            row,
            textvariable=kind_var,
            values=kind_values,
            state="readonly",
            width=6,
        )
        kind_box.pack(side="left", padx=(0, 4))
        ttk.Label(row, text=t("session_score.rule_suffix")).pack(side="left")
        row_info = {
            "frame": row,
            "count_var": count_var,
            "kind_var": kind_var,
        }
        ttk.Button(
            row,
            text=t("session_score.remove_rule"),
            command=lambda info=row_info: remove_rule_row(info),
            width=4,
        ).pack(side="right")
        rule_rows.append(row_info)
        sync_rules_state()

    def sync_rules_state(*_args) -> None:
        selected = mode_var.get()
        mode = mode_by_label.get(selected, selected)
        if mode not in ("all", "rules"):
            mode = "rules"
        enabled = mode == "rules"
        for row_info in rule_rows:
            for widget in row_info["frame"].winfo_children():
                if isinstance(widget, ttk.Combobox):
                    widget.configure(state="readonly" if enabled else "disabled")
                else:
                    try:
                        widget.configure(state="normal" if enabled else "disabled")
                    except tk.TclError:
                        pass
        add_btn.configure(state="normal" if enabled else "disabled")

    def load_rules() -> None:
        for item in current.get("session_score_notify_rules") or []:
            if not isinstance(item, dict):
                continue
            try:
                count = int(item.get("count", 1))
            except (TypeError, ValueError):
                count = 1
            kind = str(item.get("kind", "win"))
            if kind not in ("win", "loss"):
                kind = "win"
            add_rule_row(count=count, kind=kind)

    btn_row = ttk.Frame(rules_frame)
    btn_row.pack(fill="x", pady=(6, 0))
    add_btn = ttk.Button(
        btn_row,
        text=t("session_score.add_rule"),
        command=lambda: add_rule_row(),
        width=6,
    )
    add_btn.pack(side="left")

    load_rules()

    def on_mode_change(*_args) -> None:
        sync_rules_state()
        if mode_by_label.get(mode_var.get(), "") == "all":
            enabled_var.set(True)

    mode_var.trace_add("write", on_mode_change)
    sync_rules_state()

    hint = ttk.Label(
        frame,
        text=t("session_score.hint"),
        foreground="#888",
        wraplength=360,
        justify="left",
    )
    hint.grid(row=3, column=0, columnspan=3, sticky="w", pady=(8, 0))

    def selected_mode() -> str:
        selected = mode_var.get()
        mode = mode_by_label.get(selected, selected)
        return mode if mode in ("all", "rules") else "rules"

    def collect_rules() -> list[dict[str, int | str]]:
        rules: list[dict[str, int | str]] = []
        win_label = t("session_score.kind.win")
        for row in rule_rows:
            try:
                count = int(str(row["count_var"].get()).strip())
            except ValueError:
                continue
            count = max(1, min(999, count))
            kind_label = str(row["kind_var"].get())
            kind = "win" if kind_label == win_label else "loss"
            rules.append({"count": count, "kind": kind})
        return rules

    def do_ok() -> None:
        mode = selected_mode()
        rules = collect_rules()
        enabled = bool(enabled_var.get())
        if mode == "all":
            enabled = True
        elif enabled and mode == "rules" and not rules:
            mode = "all"
        result = {
            "session_score_notify_enabled": enabled,
            "session_score_notify_mode": mode,
            "session_score_notify_rules": rules,
        }
        win.destroy()
        on_ok(result)

    def do_cancel() -> None:
        win.destroy()

    btns = ttk.Frame(frame)
    btns.grid(row=4, column=0, columnspan=3, sticky="e", pady=(12, 0))
    ttk.Button(btns, text=t("settings.ok"), command=do_ok).grid(
        row=0, column=0, padx=(0, 8)
    )
    ttk.Button(btns, text=t("settings.cancel"), command=do_cancel).grid(row=0, column=1)

    win.protocol("WM_DELETE_WINDOW", do_cancel)
    win.bind("<Escape>", lambda _e: do_cancel())

    win.update_idletasks()
    x = (win.winfo_screenwidth() - win.winfo_width()) // 2
    y = (win.winfo_screenheight() - win.winfo_height()) // 2
    win.geometry(f"+{x}+{y}")
    win.lift()
    win.focus_force()
