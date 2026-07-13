from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any, Callable, Optional

from local_store import LocalStore

# hisoutensoku_memory は Windows 専用のため CHAR_NAME をここに複製
CHAR_NAME: dict[int, str] = {
    0: "Reimu",
    1: "Marisa",
    2: "Sakuya",
    3: "Alice",
    4: "Patchouli",
    5: "Youmu",
    6: "Remilia",
    7: "Yuyuko",
    8: "Yukari",
    9: "Suica",
    10: "Reisen",
    11: "Aya",
    12: "Komachi",
    13: "Iku",
    14: "Tenshi",
    15: "Sanae",
    16: "Cirno",
    17: "Meiling",
    18: "Utsuho",
    19: "Suwako",
}

CHAR_OPTIONS = ["すべて"] + [CHAR_NAME[i] for i in range(20)]

_open_window: Any = None


def _char_label(cid: int | None) -> str:
    if cid is None:
        return "?"
    return CHAR_NAME.get(cid, f"CHAR_{cid}")


def _char_id_from_label(label: str) -> int | None:
    if label == "すべて":
        return None
    for cid, name in CHAR_NAME.items():
        if name == label:
            return cid
    return None


def _result_symbol(row: dict) -> str:
    if LocalStore.is_draw(row):
        return "△"
    return "○" if LocalStore.is_my_win(row) else "×"


def _summary_text(rows: list[dict]) -> str:
    total = len(rows)
    wins = sum(1 for r in rows if LocalStore.is_my_win(r))
    losses = sum(
        1
        for r in rows
        if not LocalStore.is_my_win(r) and not LocalStore.is_draw(r)
    )
    draws = sum(1 for r in rows if LocalStore.is_draw(r))
    rate = (wins / total * 100) if total else 0.0
    return f"{total} 戦 {wins} 勝 {losses} 敗 勝率 {rate:.1f}% (引分 {draws})"


def _aggregate_by_key(
    rows: list[dict],
    key_fn: Callable[[dict], Any],
    *,
    sort_key: Callable[[tuple], Any],
) -> list[tuple]:
    stats: dict[Any, list[int]] = defaultdict(lambda: [0, 0, 0])
    for row in rows:
        key = key_fn(row)
        stats[key][0] += 1
        if LocalStore.is_draw(row):
            stats[key][2] += 1
        elif LocalStore.is_my_win(row):
            stats[key][1] += 1
    out: list[tuple] = []
    for key, (total, wins, draws) in stats.items():
        losses = total - wins - draws
        rate = (wins / total * 100) if total else 0.0
        out.append((key, total, wins, losses, rate))
    out.sort(key=sort_key, reverse=True)
    return out


def open_stats_window(parent, local_store: LocalStore) -> None:
    """戦績ビューアを開く。tkinter メインスレッドから呼ぶこと。"""
    global _open_window

    import tkinter as tk
    from tkinter import ttk

    if _open_window is not None:
        try:
            if _open_window.winfo_exists():
                _open_window.lift()
                _open_window.attributes("-topmost", True)
                _open_window.after(100, lambda: _open_window.attributes("-topmost", False))
                _open_window.focus_force()
                return
        except tk.TclError:
            pass

    win = tk.Toplevel(parent)
    win.title("asobby 戦績")
    win.geometry("900x600")
    _open_window = win

    my_char_var = tk.StringVar(value="すべて")
    opp_char_var = tk.StringVar(value="すべて")
    opp_profile_var = tk.StringVar(value="")
    ranked_var = tk.BooleanVar(value=False)
    summary_var = tk.StringVar(value="")

    filter_frame = ttk.Frame(win, padding=(8, 8, 8, 4))
    filter_frame.pack(fill="x")

    ttk.Label(filter_frame, text="自キャラ:").grid(row=0, column=0, padx=(0, 4))
    my_char_box = ttk.Combobox(
        filter_frame,
        textvariable=my_char_var,
        values=CHAR_OPTIONS,
        state="readonly",
        width=12,
    )
    my_char_box.grid(row=0, column=1, padx=(0, 12))

    ttk.Label(filter_frame, text="相手キャラ:").grid(row=0, column=2, padx=(0, 4))
    opp_char_box = ttk.Combobox(
        filter_frame,
        textvariable=opp_char_var,
        values=CHAR_OPTIONS,
        state="readonly",
        width=12,
    )
    opp_char_box.grid(row=0, column=3, padx=(0, 12))

    ttk.Label(filter_frame, text="相手プロファイル:").grid(row=0, column=4, padx=(0, 4))
    opp_profile_entry = ttk.Entry(filter_frame, textvariable=opp_profile_var, width=18)
    opp_profile_entry.grid(row=0, column=5, padx=(0, 12))

    ranked_chk = ttk.Checkbutton(filter_frame, text="ランクマのみ", variable=ranked_var)
    ranked_chk.grid(row=0, column=6, padx=(0, 8))

    summary_label = ttk.Label(win, textvariable=summary_var, padding=(8, 4))
    summary_label.pack(fill="x")

    notebook = ttk.Notebook(win)
    notebook.pack(fill="both", expand=True, padx=8, pady=(0, 8))

    # --- 対戦履歴タブ ---
    history_frame = ttk.Frame(notebook)
    notebook.add(history_frame, text="対戦履歴")

    history_cols = ("played_at", "my_char", "opp_char", "opp_profile", "result", "ranked")
    history_tree = ttk.Treeview(
        history_frame, columns=history_cols, show="headings", height=20
    )
    history_tree.heading("played_at", text="日時")
    history_tree.heading("my_char", text="自キャラ")
    history_tree.heading("opp_char", text="相手キャラ")
    history_tree.heading("opp_profile", text="相手プロファイル")
    history_tree.heading("result", text="勝敗")
    history_tree.heading("ranked", text="ランクマ")
    history_tree.column("played_at", width=140, anchor="w")
    history_tree.column("my_char", width=90, anchor="w")
    history_tree.column("opp_char", width=90, anchor="w")
    history_tree.column("opp_profile", width=200, anchor="w")
    history_tree.column("result", width=50, anchor="center")
    history_tree.column("ranked", width=60, anchor="center")

    history_scroll = ttk.Scrollbar(history_frame, orient="vertical", command=history_tree.yview)
    history_tree.configure(yscrollcommand=history_scroll.set)
    history_tree.pack(side="left", fill="both", expand=True)
    history_scroll.pack(side="right", fill="y")

    # --- 自キャラ別タブ ---
    my_char_frame = ttk.Frame(notebook)
    notebook.add(my_char_frame, text="自キャラ別")
    my_char_cols = ("char", "total", "wins", "losses", "rate")
    my_char_tree = ttk.Treeview(my_char_frame, columns=my_char_cols, show="headings")
    for col, text in zip(my_char_cols, ("キャラ", "対戦数", "勝", "負", "勝率")):
        my_char_tree.heading(col, text=text)
    my_char_tree.column("char", width=120)
    my_char_tree.column("total", width=80, anchor="e")
    my_char_tree.column("wins", width=60, anchor="e")
    my_char_tree.column("losses", width=60, anchor="e")
    my_char_tree.column("rate", width=80, anchor="e")
    my_char_scroll = ttk.Scrollbar(my_char_frame, orient="vertical", command=my_char_tree.yview)
    my_char_tree.configure(yscrollcommand=my_char_scroll.set)
    my_char_tree.pack(side="left", fill="both", expand=True)
    my_char_scroll.pack(side="right", fill="y")

    # --- 相手キャラ別タブ ---
    opp_char_frame = ttk.Frame(notebook)
    notebook.add(opp_char_frame, text="相手キャラ別")
    opp_char_tree = ttk.Treeview(opp_char_frame, columns=my_char_cols, show="headings")
    for col, text in zip(my_char_cols, ("キャラ", "対戦数", "勝", "負", "勝率")):
        opp_char_tree.heading(col, text=text)
    opp_char_tree.column("char", width=120)
    opp_char_tree.column("total", width=80, anchor="e")
    opp_char_tree.column("wins", width=60, anchor="e")
    opp_char_tree.column("losses", width=60, anchor="e")
    opp_char_tree.column("rate", width=80, anchor="e")
    opp_char_scroll = ttk.Scrollbar(opp_char_frame, orient="vertical", command=opp_char_tree.yview)
    opp_char_tree.configure(yscrollcommand=opp_char_scroll.set)
    opp_char_tree.pack(side="left", fill="both", expand=True)
    opp_char_scroll.pack(side="right", fill="y")

    # --- 相手プロファイル別タブ ---
    opp_prof_frame = ttk.Frame(notebook)
    notebook.add(opp_prof_frame, text="相手プロファイル別")
    opp_prof_cols = ("profile", "total", "wins", "losses", "rate")
    opp_prof_tree = ttk.Treeview(opp_prof_frame, columns=opp_prof_cols, show="headings")
    for col, text in zip(opp_prof_cols, ("プロファイル", "対戦数", "勝", "負", "勝率")):
        opp_prof_tree.heading(col, text=text)
    opp_prof_tree.column("profile", width=220)
    opp_prof_tree.column("total", width=80, anchor="e")
    opp_prof_tree.column("wins", width=60, anchor="e")
    opp_prof_tree.column("losses", width=60, anchor="e")
    opp_prof_tree.column("rate", width=80, anchor="e")
    opp_prof_scroll = ttk.Scrollbar(opp_prof_frame, orient="vertical", command=opp_prof_tree.yview)
    opp_prof_tree.configure(yscrollcommand=opp_prof_scroll.set)
    opp_prof_tree.pack(side="left", fill="both", expand=True)
    opp_prof_scroll.pack(side="right", fill="y")

    def _clear_tree(tree: ttk.Treeview) -> None:
        for item in tree.get_children():
            tree.delete(item)

    def refresh() -> None:
        rows = local_store.query(
            my_char=_char_id_from_label(my_char_var.get()),
            opp_char=_char_id_from_label(opp_char_var.get()),
            opp_profile_like=opp_profile_var.get().strip(),
            ranked_only=ranked_var.get(),
        )
        summary_var.set(_summary_text(rows))

        _clear_tree(history_tree)
        for row in rows:
            played = datetime.fromtimestamp(row["played_at"]).strftime("%Y-%m-%d %H:%M")
            history_tree.insert(
                "",
                "end",
                values=(
                    played,
                    _char_label(LocalStore.my_char_id(row)),
                    _char_label(LocalStore.opp_char_id(row)),
                    LocalStore.opp_profile(row),
                    _result_symbol(row),
                    "o" if row.get("ranked") else "x",
                ),
            )

        _clear_tree(my_char_tree)
        for key, total, wins, losses, rate in _aggregate_by_key(
            rows,
            lambda r: _char_label(LocalStore.my_char_id(r)),
            sort_key=lambda x: x[4],
        ):
            my_char_tree.insert(
                "",
                "end",
                values=(key, total, wins, losses, f"{rate:.1f}%"),
            )

        _clear_tree(opp_char_tree)
        for key, total, wins, losses, rate in _aggregate_by_key(
            rows,
            lambda r: _char_label(LocalStore.opp_char_id(r)),
            sort_key=lambda x: x[4],
        ):
            opp_char_tree.insert(
                "",
                "end",
                values=(key, total, wins, losses, f"{rate:.1f}%"),
            )

        _clear_tree(opp_prof_tree)
        for key, total, wins, losses, rate in _aggregate_by_key(
            rows,
            lambda r: LocalStore.opp_profile(r) or "(不明)",
            sort_key=lambda x: x[1],
        ):
            opp_prof_tree.insert(
                "",
                "end",
                values=(key, total, wins, losses, f"{rate:.1f}%"),
            )

    def do_apply() -> None:
        refresh()

    apply_btn = ttk.Button(filter_frame, text="適用", command=do_apply)
    apply_btn.grid(row=0, column=7)

    my_char_box.bind("<<ComboboxSelected>>", lambda _e: refresh())
    opp_char_box.bind("<<ComboboxSelected>>", lambda _e: refresh())
    ranked_chk.configure(command=refresh)

    def on_close() -> None:
        global _open_window
        _open_window = None
        win.destroy()

    win.protocol("WM_DELETE_WINDOW", on_close)

    refresh()
