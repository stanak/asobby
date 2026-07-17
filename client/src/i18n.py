"""Client UI translations (ja / en)."""
from __future__ import annotations

from typing import Any, Callable

SUPPORTED_LANGS = ("ja", "en")
DEFAULT_LANG = "ja"

_lang = DEFAULT_LANG
_on_change: Callable[[str], None] | None = None

JA: dict[str, str] = {
    "lang.ja": "日本語",
    "lang.en": "English",
    "lang.menu": "言語 / Language",
    "post_type.casual": "カジュアル",
    "post_type.ranked": "ランクマ",
    "tray.already_running": "asobby は既に起動しています",
    "tray.open_lobby": "ロビーページを開く",
    "tray.settings": "投稿設定...",
    "tray.stats": "戦績を見る...",
    "tray.sync_stats": "戦績をサーバーと同期",
    "tray.post_type": "募集タイプ切替",
    "tray.comment": "コメント切替",
    "tray.stream": "配信URL切替",
    "tray.pause": "ホスト自動検知を一時停止",
    "tray.pause_active": "ホスト自動検知 (停止中 残り約 {min} 分)",
    "tray.pause_running": "停止中 (残り約 {min} 分)",
    "tray.pause_resume": "今すぐ再開する",
    "tray.pause_30m": "30 分停止",
    "tray.pause_1h": "1 時間停止",
    "tray.pause_3h": "3 時間停止",
    "tray.copy_addr": "ホスト時に IP:Port をコピー",
    "tray.reply_requests": "リクエストに返信",
    "tray.accept": "承諾する",
    "tray.decline": "ごめんなさい",
    "tray.discord_login": "Discord でログイン",
    "tray.discord_logout": "ログアウト ({name})",
    "tray.open_log": "ログを開く",
    "tray.reset_paths": "ツールのパスをリセット",
    "tray.quit": "終了",
    "tray.download_update": "更新 {tag} をダウンロード",
    "tray.none": "（なし）",
    "tray.add_comment_hint": "投稿設定でコメントを追加できます",
    "tray.add_stream_hint": "投稿設定で配信URLを追加できます",
    "tray.detect_denied": "検出済み・メモリ読取不可 (ゲームが管理者権限?)",
    "tray.detect_paused": "自動検知 停止中 (残り約 {min} 分)",
    "tray.idle": "待機中 - ホストを立てると自動投稿",
    "tray.recruiting": "募集中 ({mode}): {addr}",
    "tray.battle": "対戦中 ({mode}): {detail}",
    "toast.request_hint": "承諾/拒否をボタンで返信できます",
    "toast.request_fallback": "（トレイメニューの「リクエストに返信」から返信できます）",
    "toast.accept": "承諾する",
    "toast.decline": "ごめんなさい",
    "req.giuroll": "Giuroll リクエスト",
    "req.casual_invite": "カジュアルのお誘い",
    "msg.giuroll_request": "{name} さんから Giuroll を使ってほしいとのリクエストが届きました",
    "msg.casual_invite": "{name} さんからカジュアル対戦のお誘いが届きました",
    "msg.generic": "{name} さんからメッセージが届きました",
    "notify.copy_addr": "募集アドレスをコピーしました: {addr}",
    "notify.pause": "ホスト自動検知を {label} 停止しました",
    "notify.pause_resumed": "ホスト自動検知を再開しました",
    "notify.detect_error": "天則プロセスの検出に失敗しました ({detail})",
    "notify.detect_access_denied":
        "非想天則を検出しましたがメモリを読み取れません。"
        "ゲームが管理者権限で動いている場合は、asobby も管理者として実行してください",
    "notify.pause_minutes": "{min} 分",
    "notify.pause_hours": "{hours} 時間",
    "notify.reply_missing": "返信対象が見つかりません",
    "notify.reply_failed_closed": "返信を送れませんでした（募集が終了した可能性）",
    "notify.reply_failed": "返信を送れませんでした",
    "notify.reply_sent": "{name} さんに{action}を返信しました",
    "notify.reply_accept": "承諾",
    "notify.reply_decline": "拒否",
    "notify.update_available": "asobby {tag} が公開されています。トレイメニューから開けます",
    "notify.casual_fallback": "異なるランク帯またはログインしていない相手とのマッチングのため、この対戦はカジュアル扱いになります",
    "notify.login_required_post": "募集には Discord ログインが必要です。トレイメニューからログインしてください",
    "notify.sync_login_required": "戦績の同期には Discord ログインが必要です",
    "notify.sync_running": "戦績を同期中です…",
    "notify.sync_ok": "戦績を同期しました（取得 {pulled} 件 / 送信 {pushed} 件）",
    "notify.sync_failed": "戦績の同期に失敗しました",
    "notify.session_expired": "セッションが切れました。Discord に再ログインしてください",
    "notify.post_failed": "募集に失敗しました: ポート開放または autopunch を確認してください",
    "notify.discord_login_ok": "Discord にログインしました: {name}",
    "notify.discord_login_failed": "Discord ログインに失敗しました",
    "pause.30m": "30 分",
    "pause.1h": "1 時間",
    "pause.3h": "3 時間",
    "settings.title": "asobby 投稿設定",
    "settings.post_mode": "募集モード:",
    "settings.stream_presets": "配信URL候補\n(1行1件):",
    "settings.comment_presets": "コメント候補\n(1行1件):",
    "settings.hint": "使用するコメント・配信URLはトレイの「コメント切替」「配信URL切替」で選べます",
    "settings.ok": "OK",
    "settings.cancel": "キャンセル",
    "stats.title": "asobby 戦績",
    "stats.clear": "クリア",
    "stats.ranked_only": "ランクマのみ",
    "stats.profile_search": "プロファイル検索:",
    "stats.refresh": "更新",
    "stats.my_char": "自キャラ別",
    "stats.opp_char": "相手キャラ別",
    "stats.opp_profile": "相手プロファイル別",
    "stats.history": "対戦履歴",
    "stats.more": "さらに表示",
    "stats.loading": "読み込み中...",
    "stats.col.char": "キャラ",
    "stats.col.profile": "プロファイル",
    "stats.col.games": "対戦数",
    "stats.col.wins": "勝",
    "stats.col.losses": "負",
    "stats.col.win_rate": "勝率",
    "stats.col.played_at": "日時",
    "stats.col.my_char": "自キャラ",
    "stats.col.opp_char": "相手キャラ",
    "stats.col.opp_profile": "相手プロファイル",
    "stats.col.result": "勝敗",
    "stats.col.ranked": "ランクマ",
    "stats.filter_none": "絞り込みなし",
    "stats.filter_prefix": "絞り込み: ",
    "stats.filter_my_char": "自キャラ={char}",
    "stats.filter_opp_char": "相手={char}",
    "stats.filter_opp_profile": "相手プロファイル={profile}",
    "stats.filter_profile_search": "プロファイル検索={query}",
    "stats.filter_ranked_only": "ランクマのみ",
    "stats.summary_line": "{total} 戦 {wins} 勝 {losses} 敗",
    "stats.summary_rate": "勝率 {rate:.1f}% (引分 {draws})",
    "stats.recent_rate": "直近{n}: {rate:.1f}%",
    "stats.no_data": "データなし",
    "stats.none_paren": "(なし)",
    "stats.unknown_paren": "(不明)",
    "stats.all": "(すべて)",
    "stats.history_count": "{shown} / {total} 件を表示",
}

EN: dict[str, str] = {
    "lang.ja": "日本語",
    "lang.en": "English",
    "lang.menu": "Language / 言語",
    "post_type.casual": "Casual",
    "post_type.ranked": "Ranked",
    "tray.already_running": "asobby is already running",
    "tray.open_lobby": "Open lobby page",
    "tray.settings": "Post settings...",
    "tray.stats": "View stats...",
    "tray.sync_stats": "Sync stats with server",
    "tray.post_type": "Post type",
    "tray.comment": "Comment preset",
    "tray.stream": "Stream URL preset",
    "tray.pause": "Pause auto host detection",
    "tray.pause_active": "Auto detection paused (~{min} min left)",
    "tray.pause_running": "Paused (~{min} min left)",
    "tray.pause_resume": "Resume now",
    "tray.pause_30m": "Pause 30 minutes",
    "tray.pause_1h": "Pause 1 hour",
    "tray.pause_3h": "Pause 3 hours",
    "tray.copy_addr": "Copy IP:Port when hosting",
    "tray.reply_requests": "Reply to requests",
    "tray.accept": "Accept",
    "tray.decline": "Decline",
    "tray.discord_login": "Log in with Discord",
    "tray.discord_logout": "Log out ({name})",
    "tray.open_log": "Open log",
    "tray.reset_paths": "Reset tool paths",
    "tray.quit": "Quit",
    "tray.download_update": "Download update {tag}",
    "tray.none": "(none)",
    "tray.add_comment_hint": "Add comments in Post settings",
    "tray.add_stream_hint": "Add stream URLs in Post settings",
    "tray.detect_denied": "Detected but memory read failed (game elevated?)",
    "tray.detect_paused": "Auto detection paused (~{min} min left)",
    "tray.idle": "Idle — start a host to post automatically",
    "tray.recruiting": "Recruiting ({mode}): {addr}",
    "tray.battle": "In battle ({mode}): {detail}",
    "toast.request_hint": "Use the buttons to accept or decline",
    "toast.request_fallback": "(Reply from tray menu: Reply to requests)",
    "toast.accept": "Accept",
    "toast.decline": "Decline",
    "req.giuroll": "Giuroll request",
    "req.casual_invite": "Casual invite",
    "msg.giuroll_request": "{name} asked you to use Giuroll",
    "msg.casual_invite": "{name} invited you to a casual match",
    "msg.generic": "Message from {name}",
    "notify.copy_addr": "Copied host address: {addr}",
    "notify.pause": "Auto host detection paused for {label}",
    "notify.pause_resumed": "Auto host detection resumed",
    "notify.detect_error": "Failed to detect Hisoutensoku ({detail})",
    "notify.detect_access_denied":
        "Hisoutensoku detected but memory is not readable. "
        "If the game runs as administrator, run asobby as administrator too",
    "notify.pause_minutes": "{min} minutes",
    "notify.pause_hours": "{hours} hour(s)",
    "notify.reply_missing": "Reply target not found",
    "notify.reply_failed_closed": "Could not reply (post may have closed)",
    "notify.reply_failed": "Could not send reply",
    "notify.reply_sent": "Replied {action} to {name}",
    "notify.reply_accept": "accepted",
    "notify.reply_decline": "declined",
    "notify.update_available": "asobby {tag} is available. Open it from the tray menu",
    "notify.casual_fallback": "This match is casual due to rank mismatch or guest login status",
    "notify.login_required_post": "Discord login is required to post. Use the tray menu",
    "notify.sync_login_required": "Discord login is required to sync stats",
    "notify.sync_running": "Syncing stats…",
    "notify.sync_ok": "Stats synced (pulled {pulled} / pushed {pushed})",
    "notify.sync_failed": "Stats sync failed",
    "notify.session_expired": "Session expired. Log in to Discord again",
    "notify.post_failed": "Failed to post: check port forwarding or autopunch",
    "notify.discord_login_ok": "Logged in to Discord: {name}",
    "notify.discord_login_failed": "Discord login failed",
    "pause.30m": "30 minutes",
    "pause.1h": "1 hour",
    "pause.3h": "3 hours",
    "settings.title": "asobby post settings",
    "settings.post_mode": "Post mode:",
    "settings.stream_presets": "Stream URL presets\n(one per line):",
    "settings.comment_presets": "Comment presets\n(one per line):",
    "settings.hint": "Choose active comment/stream URL from the tray submenus",
    "settings.ok": "OK",
    "settings.cancel": "Cancel",
    "stats.title": "asobby stats",
    "stats.clear": "Clear",
    "stats.ranked_only": "Ranked only",
    "stats.profile_search": "Profile search:",
    "stats.refresh": "Refresh",
    "stats.my_char": "My character",
    "stats.opp_char": "Opponent character",
    "stats.opp_profile": "Opponent profile",
    "stats.history": "Match history",
    "stats.more": "Show more",
    "stats.loading": "Loading...",
    "stats.col.char": "Char",
    "stats.col.profile": "Profile",
    "stats.col.games": "Games",
    "stats.col.wins": "W",
    "stats.col.losses": "L",
    "stats.col.win_rate": "Win%",
    "stats.col.played_at": "Time",
    "stats.col.my_char": "My char",
    "stats.col.opp_char": "Opp char",
    "stats.col.opp_profile": "Opp profile",
    "stats.col.result": "Result",
    "stats.col.ranked": "Ranked",
    "stats.filter_none": "No filter",
    "stats.filter_prefix": "Filter: ",
    "stats.filter_my_char": "My char={char}",
    "stats.filter_opp_char": "Opponent={char}",
    "stats.filter_opp_profile": "Opp profile={profile}",
    "stats.filter_profile_search": "Profile search={query}",
    "stats.filter_ranked_only": "Ranked only",
    "stats.summary_line": "{total} games {wins}W {losses}L",
    "stats.summary_rate": "Win rate {rate:.1f}% (draws {draws})",
    "stats.recent_rate": "Last {n}: {rate:.1f}%",
    "stats.no_data": "No data",
    "stats.none_paren": "(none)",
    "stats.unknown_paren": "(unknown)",
    "stats.all": "(All)",
    "stats.history_count": "Showing {shown} / {total}",
}

_TABLE: dict[str, dict[str, str]] = {"ja": JA, "en": EN}


def normalize_lang(value: Any) -> str:
    if not isinstance(value, str):
        return DEFAULT_LANG
    code = value.strip().lower()
    if code.startswith("en"):
        return "en"
    return "ja"


def get_lang() -> str:
    return _lang


def set_lang(lang: str, *, persist: bool = True) -> None:
    global _lang
    next_lang = normalize_lang(lang)
    if next_lang == _lang:
        return
    _lang = next_lang
    if persist and _on_change is not None:
        _on_change(_lang)


def bind_locale(*, get_fn: Callable[[], str], set_fn: Callable[[str], None]) -> None:
    global _lang, _on_change
    _on_change = set_fn
    _lang = normalize_lang(get_fn())


def t(key: str, **params: Any) -> str:
    text = _TABLE.get(_lang, JA).get(key) or _TABLE[DEFAULT_LANG].get(key) or key
    if params:
        try:
            return text.format(**params)
        except (KeyError, ValueError):
            return text
    return text


def post_type_options() -> list[tuple[str, str]]:
    return [(t("post_type.casual"), "casual"), (t("post_type.ranked"), "ranked")]


def post_type_label(value: str) -> str:
    if value == "ranked":
        return t("post_type.ranked")
    return t("post_type.casual")
