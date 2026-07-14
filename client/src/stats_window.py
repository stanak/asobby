from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

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

_ALL_IID = "__all__"

# Treeview への大量 insert は非常に遅いため表示件数を制限する
HISTORY_PAGE = 200
PROFILE_FACET_LIMIT = 150

_open_window: Any = None


def _char_label(cid: int | None) -> str:
    if cid is None:
        return "?"
    return CHAR_NAME.get(cid, f"CHAR_{cid}")


def _result_symbol(row: dict) -> str:
    if LocalStore.is_draw(row):
        return "△"
    return "○" if LocalStore.is_my_win(row) else "×"


@dataclass
class FilterState:
    """ファセット絞り込み状態。"""

    my_char: int | None = None
    opp_char: int | None = None
    opp_profile: str | None = None
    ranked_only: bool = False
    profile_search: str = ""


@dataclass
class MatchSummary:
    total: int
    wins: int
    losses: int
    draws: int
    win_rate: float
    recent_rates: dict[int, float] = field(default_factory=dict)


@dataclass
class FacetSortState:
    """ファセット表の列ソート状態。"""

    col: str = "label"
    asc: bool = True


# 列の初回クリック時の向き（True=昇順）
_FACET_SORT_FIRST_ASC: dict[str, bool] = {
    "label": True,
    "total": False,
    "wins": False,
    "losses": False,
    "rate": False,
}


@dataclass
class AggRow:
    key: Any
    label: str
    total: int
    wins: int
    losses: int
    draws: int
    win_rate: float


def _count_results(rows: list[dict]) -> tuple[int, int, int]:
    wins = sum(1 for r in rows if LocalStore.is_my_win(r))
    draws = sum(1 for r in rows if LocalStore.is_draw(r))
    losses = len(rows) - wins - draws
    return wins, losses, draws


def _win_rate(wins: int, losses: int) -> float:
    decided = wins + losses
    return (wins / decided * 100) if decided else 0.0


def apply_filter_state(
    rows: list[dict],
    state: FilterState,
    *,
    skip: frozenset[str] = frozenset(),
) -> list[dict]:
    """フィルタ状態を適用する。skip に指定した次元は無視（ファセット集計用）。"""
    out = rows
    if state.ranked_only:
        out = [r for r in out if r.get("ranked")]
    if state.profile_search and "profile_search" not in skip:
        needle = state.profile_search.casefold()
        out = [
            r
            for r in out
            if needle in LocalStore.opp_profile(r).casefold()
        ]
    if state.my_char is not None and "my_char" not in skip:
        out = [r for r in out if LocalStore.my_char_id(r) == state.my_char]
    if state.opp_char is not None and "opp_char" not in skip:
        out = [r for r in out if LocalStore.opp_char_id(r) == state.opp_char]
    if state.opp_profile is not None and "opp_profile" not in skip:
        out = [
            r
            for r in out
            if LocalStore.opp_profile(r) == state.opp_profile
        ]
    return out


def compute_summary(rows: list[dict], recent_sizes: tuple[int, ...] = (30, 50, 100)) -> MatchSummary:
    """勝敗サマリと直近 N 戦の勝率を計算する。勝率分母は勝+負（引分除外）。"""
    wins, losses, draws = _count_results(rows)
    sorted_rows = sorted(rows, key=lambda r: r["played_at"], reverse=True)
    recent_rates: dict[int, float] = {}
    for n in recent_sizes:
        chunk = sorted_rows[:n]
        cw, cl, _ = _count_results(chunk)
        recent_rates[n] = _win_rate(cw, cl)
    return MatchSummary(
        total=len(rows),
        wins=wins,
        losses=losses,
        draws=draws,
        win_rate=_win_rate(wins, losses),
        recent_rates=recent_rates,
    )


def format_summary_text(summary: MatchSummary) -> str:
    parts = [
        f"{summary.total} 戦 {summary.wins} 勝 {summary.losses} 敗",
        f"勝率 {summary.win_rate:.1f}% (引分 {summary.draws})",
    ]
    recent_parts = [
        f"直近{n}: {summary.recent_rates.get(n, 0.0):.1f}%"
        for n in (30, 50, 100)
    ]
    return "　".join([" ".join(parts), " / ".join(recent_parts)])


def _aggregate_rows(
    rows: list[dict],
    key_fn: Callable[[dict], Any],
    label_fn: Callable[[Any], str],
) -> list[AggRow]:
    stats: dict[Any, list[int]] = defaultdict(lambda: [0, 0, 0])
    for row in rows:
        key = key_fn(row)
        stats[key][0] += 1
        if LocalStore.is_draw(row):
            stats[key][2] += 1
        elif LocalStore.is_my_win(row):
            stats[key][1] += 1
    out: list[AggRow] = []
    for key, (total, wins, draws) in stats.items():
        losses = total - wins - draws
        out.append(
            AggRow(
                key=key,
                label=label_fn(key),
                total=total,
                wins=wins,
                losses=losses,
                draws=draws,
                win_rate=_win_rate(wins, losses),
            )
        )
    return out


def _sort_agg_rows(
    rows: list[AggRow],
    sort_state: FacetSortState,
    *,
    char_facet: bool,
) -> list[AggRow]:
    """表示用に AggRow をソートする。"""

    def sort_key(agg: AggRow) -> Any:
        col = sort_state.col
        if col == "label":
            return agg.key if char_facet else agg.label
        if col == "total":
            return agg.total
        if col == "wins":
            return agg.wins
        if col == "losses":
            return agg.losses
        if col == "rate":
            return agg.win_rate
        return agg.key if char_facet else agg.label

    return sorted(rows, key=sort_key, reverse=not sort_state.asc)


def aggregate_by_my_char(rows: list[dict]) -> list[AggRow]:
    return _aggregate_rows(
        rows,
        key_fn=lambda r: LocalStore.my_char_id(r),
        label_fn=_char_label,
    )


def aggregate_by_opp_char(rows: list[dict]) -> list[AggRow]:
    return _aggregate_rows(
        rows,
        key_fn=lambda r: LocalStore.opp_char_id(r),
        label_fn=_char_label,
    )


def aggregate_by_opp_profile(rows: list[dict]) -> list[AggRow]:
    return _aggregate_rows(
        rows,
        key_fn=lambda r: LocalStore.opp_profile(r) or "",
        label_fn=lambda p: p or "(不明)",
    )


def format_filter_label(state: FilterState) -> str:
    parts: list[str] = []
    if state.my_char is not None:
        parts.append(f"自キャラ={_char_label(state.my_char)}")
    if state.opp_char is not None:
        parts.append(f"相手={_char_label(state.opp_char)}")
    if state.opp_profile is not None:
        label = state.opp_profile or "(不明)"
        parts.append(f"相手プロファイル={label}")
    if state.profile_search:
        parts.append(f"プロファイル検索={state.profile_search}")
    if state.ranked_only:
        parts.append("ランクマのみ")
    if not parts:
        return "絞り込みなし"
    return "絞り込み: " + " / ".join(parts)


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
                _open_window.after(
                    100, lambda: _open_window.attributes("-topmost", False)
                )
                _open_window.focus_force()
                return
        except tk.TclError:
            pass

    win = tk.Toplevel(parent)
    win.title("asobby 戦績")
    win.geometry("1100x700")
    _open_window = win

    all_rows: list[dict] = []
    filter_state = FilterState()
    history_limit = [HISTORY_PAGE]  # 「さらに表示」で増える表示上限

    filter_label_var = tk.StringVar(value="絞り込みなし")
    summary_var = tk.StringVar(value="")
    ranked_var = tk.BooleanVar(value=False)
    profile_search_var = tk.StringVar(value="")

    # --- 上部バー ---
    top_frame = ttk.Frame(win, padding=(8, 8, 8, 4))
    top_frame.pack(fill="x")

    ttk.Label(top_frame, textvariable=filter_label_var).pack(side="left", padx=(0, 12))

    def clear_filters() -> None:
        filter_state.my_char = None
        filter_state.opp_char = None
        filter_state.opp_profile = None
        history_limit[0] = HISTORY_PAGE
        refresh_view()

    ttk.Button(top_frame, text="クリア", command=clear_filters).pack(side="left", padx=(0, 8))

    ranked_chk = ttk.Checkbutton(top_frame, text="ランクマのみ", variable=ranked_var)
    ranked_chk.pack(side="left", padx=(0, 8))

    ttk.Label(top_frame, text="プロファイル検索:").pack(side="left", padx=(8, 4))
    profile_search_entry = ttk.Entry(top_frame, textvariable=profile_search_var, width=16)
    profile_search_entry.pack(side="left", padx=(0, 8))

    def apply_profile_search(_event=None) -> None:
        filter_state.profile_search = profile_search_var.get().strip()
        history_limit[0] = HISTORY_PAGE
        refresh_view()

    profile_search_entry.bind("<Return>", apply_profile_search)

    ttk.Button(top_frame, text="更新", command=lambda: reload_data()).pack(side="right")

    # --- サマリ行 ---
    summary_label = ttk.Label(win, textvariable=summary_var, padding=(8, 4))
    summary_label.pack(fill="x")

    # --- 中段: ファセット ---
    facet_pane = ttk.PanedWindow(win, orient="horizontal")
    facet_pane.pack(fill="both", expand=True, padx=8, pady=(0, 4))

    stat_cols = ("label", "total", "wins", "losses", "rate")
    stat_headings = ("キャラ", "対戦数", "勝", "負", "勝率")
    prof_headings = ("プロファイル", "対戦数", "勝", "負", "勝率")

    # ファセットごとのソート状態（絞り込み変更後も維持）
    my_char_sort = FacetSortState(col="label", asc=True)
    opp_char_sort = FacetSortState(col="label", asc=True)
    opp_prof_sort = FacetSortState(col="total", asc=False)

    def _update_facet_headings(
        tree: ttk.Treeview,
        sort_state: FacetSortState,
        headings: tuple[str, ...],
    ) -> None:
        for col, base in zip(stat_cols, headings):
            text = base
            if sort_state.col == col:
                text += " ▲" if sort_state.asc else " ▼"
            tree.heading(col, text=text)

    def _make_facet_frame(
        title: str,
        *,
        sort_state: FacetSortState,
        headings: tuple[str, ...],
        on_sort: Callable[[str], None],
    ) -> tuple[ttk.Frame, ttk.Treeview]:
        frame = ttk.LabelFrame(facet_pane, text=title, padding=4)
        facet_pane.add(frame, weight=1)
        tree = ttk.Treeview(frame, columns=stat_cols, show="headings", height=10)
        for col, text in zip(stat_cols, headings):
            tree.heading(col, text=text, command=lambda c=col: on_sort(c))
        tree.column("label", width=100, anchor="w")
        tree.column("total", width=60, anchor="e")
        tree.column("wins", width=40, anchor="e")
        tree.column("losses", width=40, anchor="e")
        tree.column("rate", width=60, anchor="e")
        scroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        tree.tag_configure("high", foreground="#1a7f37")
        tree.tag_configure("low", foreground="#cf222e")
        _update_facet_headings(tree, sort_state, headings)
        return frame, tree

    def _toggle_facet_sort(sort_state: FacetSortState, col: str) -> None:
        if sort_state.col == col:
            sort_state.asc = not sort_state.asc
        else:
            sort_state.col = col
            sort_state.asc = _FACET_SORT_FIRST_ASC[col]
        refresh_view()

    _, my_char_tree = _make_facet_frame(
        "自キャラ別",
        sort_state=my_char_sort,
        headings=stat_headings,
        on_sort=lambda col: _toggle_facet_sort(my_char_sort, col),
    )
    _, opp_char_tree = _make_facet_frame(
        "相手キャラ別",
        sort_state=opp_char_sort,
        headings=stat_headings,
        on_sort=lambda col: _toggle_facet_sort(opp_char_sort, col),
    )

    prof_frame = ttk.LabelFrame(facet_pane, text="相手プロファイル別", padding=4)
    facet_pane.add(prof_frame, weight=1)
    prof_cols = ("label", "total", "wins", "losses", "rate")
    opp_prof_tree = ttk.Treeview(prof_frame, columns=prof_cols, show="headings", height=10)
    for col, text in zip(prof_cols, prof_headings):
        opp_prof_tree.heading(
            col,
            text=text,
            command=lambda c=col: _toggle_facet_sort(opp_prof_sort, c),
        )
    opp_prof_tree.column("label", width=140, anchor="w")
    opp_prof_tree.column("total", width=60, anchor="e")
    opp_prof_tree.column("wins", width=40, anchor="e")
    opp_prof_tree.column("losses", width=40, anchor="e")
    opp_prof_tree.column("rate", width=60, anchor="e")
    prof_scroll = ttk.Scrollbar(prof_frame, orient="vertical", command=opp_prof_tree.yview)
    opp_prof_tree.configure(yscrollcommand=prof_scroll.set)
    opp_prof_tree.pack(side="left", fill="both", expand=True)
    prof_scroll.pack(side="right", fill="y")
    opp_prof_tree.tag_configure("high", foreground="#1a7f37")
    opp_prof_tree.tag_configure("low", foreground="#cf222e")

    # --- 下段: 対戦履歴 ---
    history_frame = ttk.LabelFrame(win, text="対戦履歴", padding=4)
    history_frame.pack(fill="both", expand=True, padx=8, pady=(0, 8))

    history_bar = ttk.Frame(history_frame)
    history_bar.pack(fill="x", pady=(0, 4))
    history_count_var = tk.StringVar(value="")
    ttk.Label(history_bar, textvariable=history_count_var).pack(side="left")
    more_btn = ttk.Button(history_bar, text="さらに表示")
    more_btn.pack(side="right")

    history_body = ttk.Frame(history_frame)
    history_body.pack(fill="both", expand=True)

    history_cols = ("played_at", "my_char", "opp_char", "opp_profile", "result", "ranked")
    history_tree = ttk.Treeview(
        history_body, columns=history_cols, show="headings", height=12
    )
    history_tree.heading("played_at", text="日時")
    history_tree.heading("my_char", text="自キャラ")
    history_tree.heading("opp_char", text="相手キャラ")
    history_tree.heading("opp_profile", text="相手プロファイル")
    history_tree.heading("result", text="勝敗")
    history_tree.heading("ranked", text="ランクマ")
    history_tree.column("played_at", width=90, anchor="w")
    history_tree.column("my_char", width=80, anchor="w")
    history_tree.column("opp_char", width=80, anchor="w")
    history_tree.column("opp_profile", width=180, anchor="w")
    history_tree.column("result", width=40, anchor="center")
    history_tree.column("ranked", width=50, anchor="center")
    history_scroll = ttk.Scrollbar(history_body, orient="vertical", command=history_tree.yview)
    history_tree.configure(yscrollcommand=history_scroll.set)
    history_tree.pack(side="left", fill="both", expand=True)
    history_scroll.pack(side="right", fill="y")

    def _clear_tree(tree: ttk.Treeview) -> None:
        for item in tree.get_children():
            tree.delete(item)

    def _rate_tag(rate: float) -> tuple[str, ...]:
        if rate >= 60:
            return ("high",)
        if rate <= 40:
            return ("low",)
        return ()

    def _char_iid(cid: int | None) -> str:
        return _ALL_IID if cid is None else f"char_{cid}"

    def _prof_iid(profile_key: str) -> str:
        return f"prof_{hash(profile_key) & 0xFFFFFFFF:08x}"

    def _populate_facet_tree(
        tree: ttk.Treeview,
        agg_rows: list[AggRow],
        *,
        selected_key: Any,
        key_to_iid: Callable[[Any], str],
    ) -> None:
        _clear_tree(tree)
        tree.insert(
            "",
            "end",
            iid=_ALL_IID,
            values=("(すべて)", "", "", "", ""),
        )
        select_iid = _ALL_IID
        for agg in agg_rows:
            iid = key_to_iid(agg.key)
            tags = _rate_tag(agg.win_rate)
            tree.insert(
                "",
                "end",
                iid=iid,
                values=(
                    agg.label,
                    agg.total,
                    agg.wins,
                    agg.losses,
                    f"{agg.win_rate:.1f}%",
                ),
                tags=tags,
            )
            if agg.key == selected_key:
                select_iid = iid
        tree.selection_set(select_iid)
        tree.focus(select_iid)
        tree.see(select_iid)

    def refresh_view() -> None:
        filter_state.ranked_only = ranked_var.get()
        filter_label_var.set(format_filter_label(filter_state))

        filtered = apply_filter_state(all_rows, filter_state)
        summary_var.set(format_summary_text(compute_summary(filtered)))

        my_rows = apply_filter_state(all_rows, filter_state, skip=frozenset({"my_char"}))
        opp_rows = apply_filter_state(all_rows, filter_state, skip=frozenset({"opp_char"}))
        prof_rows = apply_filter_state(all_rows, filter_state, skip=frozenset({"opp_profile"}))

        my_aggs = _sort_agg_rows(
            aggregate_by_my_char(my_rows), my_char_sort, char_facet=True
        )
        opp_aggs = _sort_agg_rows(
            aggregate_by_opp_char(opp_rows), opp_char_sort, char_facet=True
        )
        prof_aggs = _sort_agg_rows(
            aggregate_by_opp_profile(prof_rows), opp_prof_sort, char_facet=False
        )

        _update_facet_headings(my_char_tree, my_char_sort, stat_headings)
        _update_facet_headings(opp_char_tree, opp_char_sort, stat_headings)
        _update_facet_headings(opp_prof_tree, opp_prof_sort, prof_headings)

        _populate_facet_tree(
            my_char_tree,
            my_aggs,
            selected_key=filter_state.my_char,
            key_to_iid=lambda k: _char_iid(k),
        )
        _populate_facet_tree(
            opp_char_tree,
            opp_aggs,
            selected_key=filter_state.opp_char,
            key_to_iid=lambda k: _char_iid(k),
        )
        # 選択中のプロファイルは上限で切られても行として残す
        visible_prof = prof_aggs[:PROFILE_FACET_LIMIT]
        if filter_state.opp_profile is not None and all(
            a.key != filter_state.opp_profile for a in visible_prof
        ):
            visible_prof = visible_prof + [
                a for a in prof_aggs if a.key == filter_state.opp_profile
            ]
        _populate_facet_tree(
            opp_prof_tree,
            visible_prof,
            selected_key=filter_state.opp_profile,
            key_to_iid=_prof_iid,
        )

        _clear_tree(history_tree)
        history_sorted = sorted(filtered, key=lambda r: r["played_at"], reverse=True)
        limit = history_limit[0]
        for row in history_sorted[:limit]:
            played = datetime.fromtimestamp(row["played_at"]).strftime("%m-%d %H:%M")
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
        shown = min(limit, len(history_sorted))
        history_count_var.set(f"{shown} / {len(history_sorted)} 件を表示")
        if len(history_sorted) > limit:
            more_btn.state(["!disabled"])
        else:
            more_btn.state(["disabled"])

    def show_more_history() -> None:
        history_limit[0] += HISTORY_PAGE
        refresh_view()

    more_btn.configure(command=show_more_history)

    def reload_data() -> None:
        """DB 読み込みをワーカースレッドで行い、UI を固めない。"""
        import threading

        summary_var.set("読み込み中...")
        result: list[list[dict]] = []

        def worker() -> None:
            try:
                result.append(local_store.fetch_all())
            except Exception:
                result.append([])

        threading.Thread(target=worker, daemon=True).start()

        def check_loaded() -> None:
            nonlocal all_rows
            if not result:
                try:
                    win.after(50, check_loaded)
                except tk.TclError:
                    pass  # ウィンドウが閉じられた
                return
            all_rows = result[0]
            history_limit[0] = HISTORY_PAGE
            refresh_view()

        win.after(50, check_loaded)

    def _parse_char_iid(iid: str) -> int | None:
        if iid == _ALL_IID:
            return None
        if iid.startswith("char_"):
            return int(iid[5:])
        return None

    def _parse_prof_iid(iid: str, rows: list[AggRow]) -> str | None:
        if iid == _ALL_IID:
            return None
        for agg in rows:
            if _prof_iid(agg.key) == iid:
                return agg.key
        return None

    # 注意: <<TreeviewSelect>> は使わない。selection_set() が発火する仮想
    # イベントがガード解除後に届き refresh_view() が無限ループするため、
    # 物理クリック (<Button-1>) のみでフィルタを切り替える。
    def _handle_facet_click(
        event: "tk.Event",
        *,
        get_current: Callable[[], Any],
        set_value: Callable[[Any], None],
        parse_iid: Callable[[str], Any],
    ) -> str | None:
        tree = event.widget
        # ヘッダークリックは heading command が処理する
        if tree.identify_region(event.x, event.y) == "heading":
            return None
        iid = tree.identify_row(event.y)
        if not iid:
            return None
        if iid == _ALL_IID:
            new_value = None
        else:
            parsed = parse_iid(iid)
            # 選択中の行を再クリックしたら解除 (トグル)
            new_value = None if parsed == get_current() else parsed
        if new_value != get_current():
            set_value(new_value)
            history_limit[0] = HISTORY_PAGE
            refresh_view()
        # クラスバインディングに渡さない (refresh 後の選択状態を上書きさせない)
        return "break"

    def _bind_facet_tree(
        tree: ttk.Treeview,
        *,
        dimension: str,
        get_current: Callable[[], Any],
        set_value: Callable[[Any], None],
        get_agg_rows: Callable[[], list[AggRow]],
        parse_iid: Callable[[str], Any],
    ) -> None:
        tree.bind(
            "<Button-1>",
            lambda e: _handle_facet_click(
                e,
                get_current=get_current,
                set_value=set_value,
                parse_iid=parse_iid,
            ),
            add="+",
        )

    def _my_agg_rows() -> list[AggRow]:
        rows = apply_filter_state(all_rows, filter_state, skip=frozenset({"my_char"}))
        return aggregate_by_my_char(rows)

    def _opp_agg_rows() -> list[AggRow]:
        rows = apply_filter_state(all_rows, filter_state, skip=frozenset({"opp_char"}))
        return aggregate_by_opp_char(rows)

    def _prof_agg_rows() -> list[AggRow]:
        rows = apply_filter_state(all_rows, filter_state, skip=frozenset({"opp_profile"}))
        return aggregate_by_opp_profile(rows)

    _bind_facet_tree(
        my_char_tree,
        dimension="my_char",
        get_current=lambda: filter_state.my_char,
        set_value=lambda v: setattr(filter_state, "my_char", v),
        get_agg_rows=_my_agg_rows,
        parse_iid=_parse_char_iid,
    )
    _bind_facet_tree(
        opp_char_tree,
        dimension="opp_char",
        get_current=lambda: filter_state.opp_char,
        set_value=lambda v: setattr(filter_state, "opp_char", v),
        get_agg_rows=_opp_agg_rows,
        parse_iid=_parse_char_iid,
    )
    _bind_facet_tree(
        opp_prof_tree,
        dimension="opp_profile",
        get_current=lambda: filter_state.opp_profile,
        set_value=lambda v: setattr(filter_state, "opp_profile", v),
        get_agg_rows=_prof_agg_rows,
        parse_iid=lambda iid: _parse_prof_iid(iid, _prof_agg_rows()),
    )

    def on_ranked_toggle() -> None:
        history_limit[0] = HISTORY_PAGE
        refresh_view()

    ranked_chk.configure(command=on_ranked_toggle)

    def on_close() -> None:
        global _open_window
        _open_window = None
        win.destroy()

    win.protocol("WM_DELETE_WINDOW", on_close)

    reload_data()
