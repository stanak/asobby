from __future__ import annotations

import asyncio
import os
import time
import webbrowser
from dataclasses import dataclass, replace, fields
from pathlib import Path
from typing import Dict, Optional, Literal, Any, Tuple
from urllib.parse import parse_qsl, urlsplit
from collections import defaultdict

import httpx

import clipboard_util
from host_clipboard import should_include_autopunch_in_clipboard
from api_client import ApiClient
from detect_api import DetectionState
from hisoutensoku_memory import read_detection_state
from local_store import LocalStore
from services import Post, NET_ALIVE, NET_BATTLE, NET_CHECKING, __version__, format_system_rank
from i18n import bind_locale, get_lang, post_type_label, t
from config_manager import ConfigManager
from replay_refusal import (
    REPLAY_REFUSAL_INACTIVE,
    REPLAY_REFUSAL_PERMANENT,
    is_replay_refusal_active,
    normalize_replay_refusal_until,
    replay_refusal_until_from_duration,
)
from tool_manager import ToolManager, ToolState
from host_probe import probe_rtt_ms
from local_api import start_local_api_server, set_ping_probe_guard, LOCAL_API_PORT


ActionType = Literal["create", "update", "close", "result", "guest_result"]

HEARTBEAT_SEC = 5  # サーバー側 TTL (20s) の 1/4
CREATE_RETRY_COOLDOWN_SEC = 10
UPDATE_CHECK_INTERVAL_SEC = 6 * 3600

# KO 直後の btl_mode==5 は短時間しか観測できないため、AlwaysRecordable と
# 同じ 50ms でポーリングする（1 秒だと勝敗確定を取りこぼす）。
DETECT_INTERVAL_SEC = 0.05

REPLAY_MAX_BYTES = 300 * 1024
REPLAY_POLL_INTERVAL_SEC = 2.0
REPLAY_POLL_TIMEOUT_SEC = 60.0
REPLAY_UPLOAD_RETRIES = 6
REPLAY_UPLOAD_RETRY_DELAY_SEC = 10.0
REPLAY_RESULT_WAIT_SEC = 30.0
REPLAY_MTIME_MARGIN_SEC = 10.0

STATS_SYNC_INITIAL_DELAY_SEC = 10.0
STATS_SYNC_INTERVAL_SEC = 60.0
STATS_SYNC_BATCH = 500
STATS_SYNC_DEFER_SEC = 8.0

LOBBY_POLL_INTERVAL_SEC = 15.0
HOST_SELF_CHECK_INTERVAL_SEC = 20  # 投稿停止中のローカル到達性チェック間隔
PAUSE_UNTIL_RESUME = float("inf")  # pause_auto_detect() 用の無期限 sentinel

PENDING_REQUEST_TTL_SEC = 600  # 未返信リクエストの保持期限

_POST_FIELD_NAMES = {f.name for f in fields(Post)}


@dataclass
class PendingRequest:
    message_id: str
    req_type: str
    from_name: str
    received_at: float


def _parse_version(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.lstrip("v").split(".") if x.isdigit())


def _valid_char_id(cid: Optional[int]) -> bool:
    return cid is not None and 0 <= cid <= 19


def _match_char_ids(st: DetectionState) -> tuple[Optional[int], Optional[int]]:
    """記録用キャラ ID (左=host / 右=guest)。

    対戦・ロード中は確定済みの LCHARID/RCHARID を優先する。
    キャラセレ中は次ラウンドのカーソルが載るため battle オブジェクトのみ使う。
    """
    gl = st.lchar_id if _valid_char_id(st.lchar_id) else None
    gr = st.rchar_id if _valid_char_id(st.rchar_id) else None
    bl = st.battle_lchar_id if _valid_char_id(st.battle_lchar_id) else None
    br = st.battle_rchar_id if _valid_char_id(st.battle_rchar_id) else None

    if st.mode in ("battle", "loading"):
        if gl is not None and gr is not None:
            return gl, gr
        if bl is not None and br is not None:
            return bl, br
        return None, None

    if bl is not None and br is not None:
        return bl, br
    return None, None


def _ko_decided(st: DetectionState) -> bool:
    return (
        st.btl_mode == 5
        and st.lwin is not None
        and st.rwin is not None
        and 0 <= st.lwin <= 2
        and 0 <= st.rwin <= 2
        and (st.lwin == 2 or st.rwin == 2)
    )


def _ko_fingerprint(st: DetectionState, *, host_char: int, guest_char: int) -> str:
    """同一 KO を二重処理しないための指紋 (連戦時は勝数が変わるので別試合になる)。

    キャラ ID は読み取りタイミングで揺れるため指紋に含めない。
    """
    del host_char, guest_char
    if not _ko_decided(st):
        return ""
    return f"{st.lwin}:{st.rwin}:{st.lprof}:{st.rprof}"


def _ko_recordable(*, is_battle: bool, mode: str, round_battle_engaged: bool) -> bool:
    """KO 確定の読み取りを許可するシーン。

    実際に対戦シーンへ入ったラウンドだけ許可する。
    キャラセレ/ロードは同一ラウンドで対戦を観測した後だけ許可する。
    """
    if not round_battle_engaged:
        return False
    if is_battle:
        return True
    return mode in ("loading", "charsel")


@dataclass
class Action:
    type: ActionType
    payload: dict


class Controller:
    """非想天則の状態を検出して募集投稿を自動管理するエージェント"""

    def __init__(self, app) -> None:
        self.log_sink = app.emit_log
        self.notify_sink = app.emit_notify
        self.request_sink = app.emit_request
        self.my_post_sink = app.emit_my_post
        self.lobby_activity_sink = app.emit_lobby_activity
        self.btn_labels_sink = app.emit_btn_labels
        self.pause_ui_sink = app.emit_pause_state_changed
        self.detect_ui_sink = app.emit_detect_ui_changed
        self._loop: asyncio.AbstractEventLoop | None = None

        self.config_mgr = ConfigManager()
        bind_locale(
            get_fn=lambda: self.config_mgr.get_value("options", "locale", "ja"),
            set_fn=lambda lang: self.config_mgr.set_value("options", "locale", lang),
        )
        self.config = self.config_mgr.get()
        self.tool_mgr = ToolManager(self.config_mgr)
        db_path = self.config_mgr.path.resolve().parent / "matches.db"
        self.local_store = LocalStore.open_with_fallback(db_path)

        # 非想天則の対戦は IPv4 のみ。IPv6 でサーバーに接続すると /myip が
        # IPv6 を返して募集アドレスが壊れるため、通信を IPv4 に強制する。
        self.http = httpx.AsyncClient(
            timeout=10.0,
            transport=httpx.AsyncHTTPTransport(local_address="0.0.0.0"),
        )
        self.api = ApiClient(self.http, self.config_mgr.get_api_base())
        self._action_q: asyncio.Queue[Action] = asyncio.Queue()
        self._stop = asyncio.Event()

        self._stable_counts = defaultdict(int)
        self._seen_recruit_this_run = False
        self._last_keepalive_ts = 0.0
        self._close_grace_sec = 4

        self._last_heartbeat_ts = 0.0
        self._next_create_ts = 0.0

        self._tool_labels: Dict[str, str] = {}
        self._last_sent_payload: Optional[dict] = None
        self._create_pending = False
        self._close_pending = False
        self._result_reported = False
        self._last_ko_fingerprint = ""
        self._round_battle_engaged = False
        self._round_char_ids: tuple[Optional[int], Optional[int]] = (None, None)
        self._pending_local_match: Optional[dict] = None

        self.owner_token: str = ""
        self.my_post: Post = Post()
        self.update_my_post(**self._default_post_params())

        self.update_available: Optional[tuple[str, str]] = None

        try:
            start_local_api_server()
            set_ping_probe_guard(lambda: not self.local_net_active())
            self.log_sink("info", f"Local lobby API listening on 127.0.0.1:{LOCAL_API_PORT}")
        except OSError as e:
            self.log_sink("warn", f"Local lobby API unavailable: {e}")
        self._notified_update_tag: str = ""

        # Discord ログイン（任意）。設定に保存済みのセッションを復元する。
        auth = self.config_mgr.get_section("auth")
        self.api.session_token = str(auth.get("session_token", ""))
        self.discord_user: str = str(auth.get("username", "")) if self.api.session_token else ""
        self._lobby_badges = {"ranked": False, "casual": False}
        self._login_in_progress = False
        self._notified_login_required = False
        self._auto_login_attempted = False
        self._notified_casual_fallback = False

        self._stats_sync_running = False

        self._battle_start_ts = 0.0
        self._replay_pending = False
        self._uploaded_replay_keys: set[tuple[str, int]] = set()
        self._replay_result_reported = False
        self._replay_match_meta: Optional[dict[str, str]] = None
        self._replay_upload_lock = asyncio.Lock()

        self.pending_requests: list[PendingRequest] = []

        # ロビー自動投稿の一時停止。0 なら停止していない (検知自体は止めない)
        self._detect_pause_until: float = 0.0
        self._last_host_self_check_ts: float = 0.0
        self._host_unreachable_notified: bool = False

        # トレイ表示用 (検知状態。投稿の有無とは独立)
        self._tray_icon_key: str = "idle"
        self._tray_host_addr: str = ""
        self._tray_match_detail: str = ""

        # 同一相手との連続対戦の勝敗数 (ホスト勝-クライアント勝)
        self._session_score_key: str = ""
        self._session_host_wins: int = 0
        self._session_client_wins: int = 0
        self._session_my_wins: int = 0
        self._session_my_losses: int = 0

        # ローカル天則がネット対戦フロー中 (ロビー Ping を止める)
        self._local_net_active: bool = False

        # ホスト検知時の IP:Port クリップボードコピー (1 ホストセッション 1 回)
        self._addr_copied = False

        # 検知系の異常 ("" = 正常)。トレイの状態表示に使う
        self.detect_error: str = ""

        # 検知スナップショットの診断ログ用
        self._last_raw_logged: str = ""
        self._last_raw_log_ts: float = 0.0
        self._last_exe_logged: str = ""
        self._last_modules_logged: str = ""
        self._last_dump_log_ts: float = 0.0

        try:
            self._sync_tools_from_detection(read_detection_state())
        except Exception:
            pass

    # -----------------
    # basic helpers
    # -----------------
    def _default_post_params(self) -> Dict[str, Any]:
        # post_defaults には comment_presets 等 Post に無いキーも入るので絞る
        d = self.config_mgr.get_post_defaults()
        return {k: d[k] for k in ("post_type", "comment", "stream_url", "challenge_upper") if k in d}

    def challenge_upper_enabled(self) -> bool:
        return bool(self.config_mgr.get_value("post_defaults", "challenge_upper", False))

    def set_challenge_upper_enabled(self, enabled: bool) -> None:
        self.config_mgr.set_post_default("challenge_upper", bool(enabled))
        self.update_my_post(challenge_upper=bool(enabled))
        self.log_sink(
            "info",
            t("log.challenge_upper", state=t("common.on" if enabled else "common.off")),
        )

    def ping_warn_enabled(self) -> bool:
        return bool(self.config_mgr.get_value("options", "ping_warn_enabled", True))

    def set_ping_warn_enabled(self, enabled: bool) -> None:
        value = bool(enabled)
        self.config_mgr.set_value("options", "ping_warn_enabled", value)
        self.log_sink(
            "info",
            t("log.ping_warn_enabled", state=t("common.on" if value else "common.off")),
        )

    def ping_warn_ms(self) -> int:
        raw = self.config_mgr.get_value("options", "ping_warn_ms", 60)
        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = 60
        return max(1, min(5000, value))

    def ping_warn_giuroll_ms(self) -> int:
        raw = self.config_mgr.get_value("options", "ping_warn_giuroll_ms", 100)
        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = 100
        return max(1, min(5000, value))

    def set_ping_warn_ms(self, ms: int) -> None:
        value = max(1, min(5000, int(ms)))
        self.config_mgr.set_value("options", "ping_warn_ms", value)
        self.log_sink("info", t("log.ping_warn_ms", ms=value))

    def set_ping_warn_giuroll_ms(self, ms: int) -> None:
        value = max(1, min(5000, int(ms)))
        self.config_mgr.set_value("options", "ping_warn_giuroll_ms", value)
        self.log_sink("info", t("log.ping_warn_giuroll_ms", ms=value))

    def local_net_active(self) -> bool:
        """ローカル天則がホスト待ち・キャラセレ・対戦などネットフロー中。"""
        return self._local_net_active

    def session_score_notify_enabled(self) -> bool:
        raw = self.config_mgr.get_value(
            "options", "session_score_notify_enabled", False
        )
        return raw is True

    def set_session_score_notify_enabled(self, enabled: bool) -> None:
        value = bool(enabled)
        self.config_mgr.set_value("options", "session_score_notify_enabled", value)
        if value and not self.session_score_notify_rules():
            self.config_mgr.set_value("options", "session_score_notify_mode", "all")
        self.log_sink(
            "info",
            t(
                "log.session_score_notify_enabled",
                state=t("common.on" if value else "common.off"),
            ),
        )

    def session_score_notify_mode(self) -> str:
        mode = str(
            self.config_mgr.get_value("options", "session_score_notify_mode", "all")
        )
        return mode if mode in ("all", "rules") else "all"

    def _effective_session_score_notify_mode(self) -> str:
        mode = self.session_score_notify_mode()
        if mode == "rules" and not self.session_score_notify_rules():
            return "all"
        return mode

    def set_session_score_notify_mode(self, mode: str) -> None:
        value = mode if mode in ("all", "rules") else "rules"
        self.config_mgr.set_value("options", "session_score_notify_mode", value)
        if value == "all":
            self.config_mgr.set_value("options", "session_score_notify_enabled", True)
        self.log_sink(
            "info",
            t(
                "log.session_score_notify_mode",
                mode=t(f"session_score.mode.{value}"),
            ),
        )

    def session_score_notify_rules(self) -> list[dict[str, int | str]]:
        raw = self.config_mgr.get_value("options", "session_score_notify_rules", [])
        if not isinstance(raw, list):
            return []
        out: list[dict[str, int | str]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            try:
                count = int(item.get("count", 0))
            except (TypeError, ValueError):
                continue
            kind = str(item.get("kind", ""))
            if count >= 1 and kind in ("win", "loss"):
                out.append({"count": count, "kind": kind})
        return out

    def set_session_score_notify_rules(
        self, rules: list[dict[str, int | str]]
    ) -> None:
        normalized: list[dict[str, int | str]] = []
        for item in rules:
            try:
                count = int(item.get("count", 0))
            except (TypeError, ValueError):
                continue
            kind = str(item.get("kind", ""))
            if count >= 1 and kind in ("win", "loss"):
                normalized.append({"count": count, "kind": kind})
        self.config_mgr.set_value(
            "options", "session_score_notify_rules", normalized
        )
        self.log_sink(
            "info",
            t("log.session_score_notify_rules", count=len(normalized)),
        )

    def replay_refusal_until(self) -> float:
        raw = self.config_mgr.get_value("options", "replay_refusal_until", 0)
        return normalize_replay_refusal_until(raw)

    def is_replay_refusal_active(self) -> bool:
        until = self.replay_refusal_until()
        if not is_replay_refusal_active(until):
            if until != REPLAY_REFUSAL_INACTIVE:
                self.config_mgr.set_value(
                    "options", "replay_refusal_until", REPLAY_REFUSAL_INACTIVE
                )
            return False
        return True

    def is_replay_refusal_permanent(self) -> bool:
        return self.replay_refusal_until() == REPLAY_REFUSAL_PERMANENT

    def replay_refusal_remaining_min(self) -> int:
        until = self.replay_refusal_until()
        if self.is_replay_refusal_permanent():
            return 0
        rest = until - time.time()
        return max(0, int((rest + 59) // 60))

    def replay_refusal_remaining_label(self) -> str:
        if self.is_replay_refusal_permanent():
            return t("pause.until_resume")
        minutes = self.replay_refusal_remaining_min()
        if minutes <= 0:
            return t("pause.remaining_m", min=0)
        if minutes >= 60:
            hours = minutes // 60
            rem_min = minutes % 60
            if rem_min:
                return t("pause.remaining_hm", hours=hours, min=rem_min)
            return t("pause.remaining_h", hours=hours)
        return t("pause.remaining_m", min=minutes)

    def set_replay_refusal(self, seconds: float) -> None:
        until = replay_refusal_until_from_duration(seconds)
        self.config_mgr.set_value("options", "replay_refusal_until", until)
        if seconds == REPLAY_REFUSAL_PERMANENT:
            label = t("pause.until_resume")
        else:
            label = self._pause_duration_label(seconds)
        self.log_sink("info", t("log.replay_refusal_enabled", label=label))
        self.notify_sink(t("notify.replay_refusal", label=label))
        self.pause_ui_sink()
        if self.is_logged_in():
            self._schedule_async(self._sync_replay_refusal_to_server())

    def clear_replay_refusal(self) -> None:
        if self.replay_refusal_until() == REPLAY_REFUSAL_INACTIVE:
            return
        self.config_mgr.set_value("options", "replay_refusal_until", REPLAY_REFUSAL_INACTIVE)
        self.log_sink("info", t("log.replay_refusal_cleared"))
        self.notify_sink(t("notify.replay_refusal_cleared"))
        self.pause_ui_sink()
        if self.is_logged_in():
            self._schedule_async(self._sync_replay_refusal_to_server())

    async def _sync_replay_refusal_to_server(self) -> None:
        if not self.is_logged_in():
            return
        try:
            await self.api.patch_user_settings(
                {"replay_refusal_until": self.replay_refusal_until()}
            )
        except httpx.HTTPError as e:
            self.log_sink("warn", t("log.replay_refusal_sync_failed", error=e))

    def _reset_session_score(self) -> None:
        if (
            not self._session_score_key
            and self._session_host_wins == 0
            and self._session_client_wins == 0
        ):
            return
        self._session_score_key = ""
        self._session_host_wins = 0
        self._session_client_wins = 0
        self._session_my_wins = 0
        self._session_my_losses = 0

    def _session_opponent_key(self, payload: dict) -> str:
        my_side = str(payload.get("my_side", ""))
        if my_side == "host":
            opp = str(payload.get("guest_profile") or "").strip()
            return f"guest:{opp}" if opp else "guest:?"
        if my_side == "client":
            opp = str(payload.get("host_profile") or "").strip()
            return f"host:{opp}" if opp else "host:?"
        return ""

    def _should_notify_session_score(self, my_wins: int, my_losses: int) -> bool:
        if not self.session_score_notify_enabled():
            return False
        if self._effective_session_score_notify_mode() == "all":
            return True
        for rule in self.session_score_notify_rules():
            if rule["kind"] == "win" and my_wins == rule["count"]:
                return True
            if rule["kind"] == "loss" and my_losses == rule["count"]:
                return True
        return False

    def _handle_session_score(self, payload: dict) -> None:
        key = self._session_opponent_key(payload)
        if not key:
            return
        if key != self._session_score_key:
            self._session_score_key = key
            self._session_host_wins = 0
            self._session_client_wins = 0
            self._session_my_wins = 0
            self._session_my_losses = 0

        winner = str(payload.get("winner", ""))
        my_side = str(payload.get("my_side", ""))
        if winner == "host":
            self._session_host_wins += 1
        elif winner == "guest":
            self._session_client_wins += 1

        i_won = (my_side == "host" and winner == "host") or (
            my_side == "client" and winner == "guest"
        )
        if i_won:
            self._session_my_wins += 1
        else:
            self._session_my_losses += 1

        score = f"{self._session_host_wins}-{self._session_client_wins}"
        if self.session_score_notify_enabled():
            self.log_sink(
                "info",
                t(
                    "log.session_score_updated",
                    score=score,
                    my_wins=self._session_my_wins,
                    my_losses=self._session_my_losses,
                ),
            )

        if not self._should_notify_session_score(
            self._session_my_wins, self._session_my_losses
        ):
            return

        text = t("notify.session_score", score=score)
        self.notify_sink(text)
        self.log_sink("info", t("log.session_score", score=score))

    def comment_presets(self) -> list[str]:
        v = self.config_mgr.get_value("post_defaults", "comment_presets", [])
        if not isinstance(v, list):
            return []
        return [str(x) for x in v if str(x).strip()]

    def set_active_comment(self, text: str) -> None:
        self.config_mgr.set_post_default("comment", text)
        self.update_my_post(comment=text)

    def set_active_post_type(self, post_type: str) -> None:
        self.config_mgr.set_post_default("post_type", post_type)
        self.update_my_post(post_type=post_type)

    def stream_presets(self) -> list[str]:
        v = self.config_mgr.get_value("post_defaults", "stream_url_presets", [])
        if not isinstance(v, list):
            return []
        return [str(x) for x in v if str(x).strip()]

    def set_active_stream(self, text: str) -> None:
        self.config_mgr.set_post_default("stream_url", text)
        self.update_my_post(stream_url=text)

    def copy_addr_enabled(self) -> bool:
        return bool(self.config_mgr.get_value("options", "copy_addr_on_host", False))

    def set_copy_addr_enabled(self, enabled: bool) -> None:
        self.config_mgr.set_value("options", "copy_addr_on_host", bool(enabled))
        self.log_sink(
            "info",
            f"Copy addr on host: {'enabled' if enabled else 'disabled'}",
        )

    def _local_autopunch_for_clipboard(self) -> bool:
        """サーバー応答より先に、この募集で AP 利用を申告していたか。"""
        payload = self._last_sent_payload or {}
        return bool(payload.get("autopunch"))

    def _format_host_clipboard(
        self,
        addr: str,
        *,
        giuroll: bool,
        include_autopunch: bool,
    ) -> str:
        tools: list[str] = []
        if giuroll:
            tools.append("Giuroll")
        if include_autopunch:
            tools.append("AutoPunch")
        if tools:
            return f"{addr} {', '.join(tools)}"
        return addr

    def _copy_addr_to_clipboard(
        self,
        addr: str,
        *,
        giuroll: bool = False,
        include_autopunch: bool = False,
    ) -> None:
        text = self._format_host_clipboard(
            addr,
            giuroll=giuroll,
            include_autopunch=include_autopunch,
        )
        if clipboard_util.copy_text(text):
            self.log_sink("info", f"Copied host info to clipboard: {text}")
            self.notify_sink(t("notify.copy_addr", addr=text))
        else:
            self.log_sink("warn", "Clipboard copy failed")

    def _try_copy_host_info_from_server(self, post_data: dict) -> None:
        """募集作成/更新後、サーバー到達性判定を反映してクリップボードへコピー。"""
        if not self.copy_addr_enabled() or self._addr_copied:
            return
        addr = str(post_data.get("addr") or self.my_post.addr or "").strip()
        if not addr or addr.startswith("0.0.0.0:"):
            return
        local_autopunch = self._local_autopunch_for_clipboard()
        self._sync_post_reachability_from_server(post_data)
        giuroll = bool(post_data.get("giuroll", self.my_post.giuroll))
        include_autopunch = should_include_autopunch_in_clipboard(
            post_data,
            local_autopunch=local_autopunch,
        )
        self._copy_addr_to_clipboard(
            addr,
            giuroll=giuroll,
            include_autopunch=include_autopunch,
        )
        self._addr_copied = True

    def clear_my_post(self) -> None:
        self.owner_token = ""
        self._seen_recruit_this_run = False
        self._last_sent_payload = None
        self._create_pending = False
        self._close_pending = False
        self._result_reported = False
        self._last_ko_fingerprint = ""
        self._pending_local_match = None
        self._notified_casual_fallback = False
        self.pending_requests.clear()
        self.my_post = replace(
            self.my_post,
            id="",
            addr="",
            match_status="",
            net_status=0,
            giuroll=False,
            autopunch=False,
        )
        self.update_my_post()

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def _schedule_async(self, coro) -> None:
        loop = self._loop
        if loop is None or not loop.is_running():
            return
        asyncio.run_coroutine_threadsafe(coro, loop)

    def has_active_post(self) -> bool:
        return bool(self.my_post.id and self.owner_token)

    def lobby_badges(self) -> dict[str, bool]:
        return dict(self._lobby_badges)

    def lobby_has_other_posts(self) -> bool:
        return self._lobby_badges["ranked"] or self._lobby_badges["casual"]

    async def _refresh_lobby_badge(self) -> None:
        empty = {"ranked": False, "casual": False}
        if not self.is_logged_in():
            new_badges = empty
        else:
            try:
                me = await self.api.auth_me()
                raw = me.get("favicon_badges") or empty
                new_badges = {
                    "ranked": bool(raw.get("ranked")),
                    "casual": bool(raw.get("casual")),
                }
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 401:
                    self._clear_expired_session()
                return
            except httpx.HTTPError:
                return
        if new_badges == self._lobby_badges:
            return
        self._lobby_badges = new_badges
        self.lobby_activity_sink()

    async def lobby_poll_loop(self) -> None:
        while not self._stop.is_set():
            await self._refresh_lobby_badge()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=LOBBY_POLL_INTERVAL_SEC)
            except asyncio.TimeoutError:
                pass

    # -----------------
    # ロビー自動投稿の一時停止
    # -----------------
    def _pause_duration_label(self, seconds: float) -> str:
        if seconds == PAUSE_UNTIL_RESUME:
            return t("pause.until_resume")
        minutes = int(seconds / 60)
        if minutes == 30:
            return t("pause.30m")
        if minutes == 60:
            return t("pause.1h")
        if minutes == 180:
            return t("pause.3h")
        if minutes >= 60 and minutes % 60 == 0:
            return t("notify.pause_hours", hours=minutes // 60)
        return t("notify.pause_minutes", min=minutes)

    def pause_auto_detect(self, seconds: float) -> None:
        """asobby.com への自動投稿だけを止める。検知・到達性チェックは継続。"""
        self._host_unreachable_notified = False
        if seconds == PAUSE_UNTIL_RESUME:
            self._detect_pause_until = PAUSE_UNTIL_RESUME
        else:
            self._detect_pause_until = time.time() + seconds
        label = self._pause_duration_label(seconds)
        self.log_sink("info", f"Auto posting paused for {label}")
        self.notify_sink(t("notify.pause", label=label))
        self.pause_ui_sink()

    def resume_auto_detect(self) -> None:
        if self._detect_pause_until:
            self._detect_pause_until = 0.0
            self._host_unreachable_notified = False
            self.log_sink("info", "Auto posting resumed manually")
            self.notify_sink(t("notify.pause_resumed"))
            self.pause_ui_sink()

    def tray_icon_key(self) -> str:
        return self._tray_icon_key

    def tray_status_text(self) -> str:
        if self.detect_error == "access_denied":
            return t("tray.detect_denied")
        mode = post_type_label(self.my_post.post_type)
        if self._tray_icon_key == "battle":
            base = t(
                "tray.battle",
                mode=mode,
                detail=self._tray_match_detail,
            )
        elif self._tray_icon_key == "recruit":
            base = t("tray.recruiting", mode=mode, addr=self._tray_host_addr)
        else:
            base = t("tray.idle")
        extras: list[str] = []
        if self.is_detect_paused():
            extras.append(
                t("tray.post_paused", remaining=self.detect_pause_remaining_label())
            )
        if self.lobby_has_other_posts():
            extras.append(t("tray.lobby_recruitment"))
        if extras:
            return base + " · " + " · ".join(extras)
        return base

    def _update_tray_ui_state_idle(self) -> None:
        if (
            self._tray_icon_key == "idle"
            and not self._tray_host_addr
            and not self._tray_match_detail
        ):
            return
        self._tray_icon_key = "idle"
        self._tray_host_addr = ""
        self._tray_match_detail = ""
        self.detect_ui_sink()

    def _update_tray_ui_state(
        self,
        *,
        is_recruiting: bool,
        is_battle: bool,
        st: DetectionState,
        my_ip: str,
        match_status: str,
    ) -> None:
        if is_battle and st.net_side == "host":
            key = "battle"
            addr = self.my_post.addr or self._current_addr(my_ip, st.port) or ""
            detail = match_status or addr
        elif is_recruiting and st.net_side != "client":
            key = "recruit"
            addr = self._current_addr(my_ip, st.port) or ""
            detail = ""
        else:
            key = "idle"
            addr = ""
            detail = ""

        changed = (
            key != self._tray_icon_key
            or addr != self._tray_host_addr
            or detail != self._tray_match_detail
        )
        self._tray_icon_key = key
        self._tray_host_addr = addr
        self._tray_match_detail = detail
        if changed:
            self.detect_ui_sink()

    async def _probe_host_reachable(self, addr: str, autopunch: bool) -> bool:
        try:
            host, port_s = addr.rsplit(":", 1)
            port = int(port_s)
        except ValueError:
            return False
        rtt = await asyncio.to_thread(
            probe_rtt_ms, host, port, autopunch=autopunch
        )
        return rtt is not None

    async def _self_check_host_when_paused(self, addr: str, autopunch: bool) -> None:
        if not self.is_detect_paused():
            return
        ok = await self._probe_host_reachable(addr, autopunch)
        if not self.is_detect_paused():
            return
        if not ok:
            if not self._host_unreachable_notified:
                self._host_unreachable_notified = True
                self.log_sink(
                    "error",
                    "Host not reachable while posting paused. "
                    "Please open the port or start autopunch.",
                )
                self.notify_sink(t("notify.post_failed"), important=True)
        else:
            self._host_unreachable_notified = False

    def _track_detect_error(self, err: str) -> None:
        """検知異常の遷移をログ・通知し、トレイ表示を更新する。"""
        if err == self.detect_error:
            return
        self.detect_error = err
        if err == "access_denied":
            self.log_sink(
                "warn",
                "th123.exe found but memory is not readable (access denied). "
                "Game may be running as administrator",
            )
            self.notify_sink(t("notify.detect_access_denied"))
        elif not err:
            self.log_sink("info", "Memory read recovered")
        self.my_post_sink(self.my_post)  # トレイの状態表示を更新

    def _log_detect_snapshot(self, st: DetectionState) -> None:
        """検知の生の値が変化したらログに残す (募集が検知されない環境の診断用)。

        50ms ポーリングのため、変化時のみ・最短 1 秒間隔に絞る。
        """
        now = time.time()
        if st.exe_path and st.exe_path != self._last_exe_logged:
            self._last_exe_logged = st.exe_path
            try:
                size = Path(st.exe_path).stat().st_size
            except OSError:
                size = -1
            self.log_sink("info", f"Soku exe: {st.exe_path} ({size} bytes)")
        # ゲームフォルダ由来の DLL は後から注入されるものもあるので、
        # 変化するたびに記録する (giuroll/autopunch のロードもここに出る)
        if st.modules and st.modules != self._last_modules_logged:
            self._last_modules_logged = st.modules
            self.log_sink("info", f"Soku modules: {st.modules}")
        if (
            st.raw
            and st.raw != self._last_raw_logged
            and (now - self._last_raw_log_ts) >= 1.0
        ):
            self._last_raw_logged = st.raw
            self._last_raw_log_ts = now
            self.log_sink("info", f"Detect: {st.raw}")
        if st.dump and (now - self._last_dump_log_ts) >= 30.0:
            self._last_dump_log_ts = now
            self.log_sink("info", f"Detect dump: {st.dump}")

    def is_detect_paused(self) -> bool:
        return time.time() < self._detect_pause_until

    def is_pause_indefinite(self) -> bool:
        return self._detect_pause_until == PAUSE_UNTIL_RESUME

    def detect_pause_remaining_min(self) -> int:
        """残り停止時間 (分、切り上げ)。停止していなければ 0。"""
        if self.is_pause_indefinite():
            return 0
        rest = self._detect_pause_until - time.time()
        return max(0, int(rest // 60) + (1 if rest % 60 > 0 else 0)) if rest > 0 else 0

    def detect_pause_remaining_label(self) -> str:
        """UI 用の残り時間文字列 (分・時間単位、切り上げ)。"""
        if self.is_pause_indefinite():
            return t("pause.until_resume")
        minutes = self.detect_pause_remaining_min()
        if minutes <= 0:
            return t("pause.remaining_m", min=0)
        if minutes >= 60:
            hours = minutes // 60
            rem_min = minutes % 60
            if rem_min:
                return t("pause.remaining_hm", hours=hours, min=rem_min)
            return t("pause.remaining_h", hours=hours)
        return t("pause.remaining_m", min=minutes)

    def _stable_key(self, key: str, need: int, *, seen: bool) -> bool:
        if seen:
            self._stable_counts[key] += 1
        else:
            self._stable_counts[key] = 0
        return self._stable_counts[key] >= need

    def _stable_for(self, key: str, seconds: float, *, seen: bool) -> bool:
        """seen が seconds 秒間連続したら True（ポーリング周期に依存しない）。"""
        need = max(1, round(seconds / DETECT_INTERVAL_SEC))
        return self._stable_key(key, need, seen=seen)

    def _build_match_status(self, st: DetectionState, *, is_recruiting: bool, is_battle: bool) -> str:
        lp = (st.lprof or "").strip()
        rp = (st.rprof or "").strip()
        lc = (st.lchar_name or "?").strip()
        rc = (st.rchar_name or "?").strip()

        if is_battle and lp and rp:
            return f"{lp}({lc}) vs {rp}({rc})"

        # 募集中/キャラセレ等ではプロフィール名だけ
        return lp

    @staticmethod
    def _host_net_status(st: DetectionState, *, is_battle: bool) -> int:
        if is_battle and st.net_side == "host":
            return NET_BATTLE
        if st.net_side == "host" and st.mode in ("charsel", "loading"):
            return NET_CHECKING
        return NET_ALIVE

    def _host_uses_autopunch(self, st: DetectionState) -> bool:
        """AutoPunch 利用ホストか。DLL 検知に加え、起動済み exe も拾う。"""
        if st.autopunch:
            return True
        if self.tool_mgr.state("autopunch") == ToolState.LOADED:
            return True
        return bool(self.my_post.autopunch)

    def _current_addr(self, my_ip: str, port: Optional[int]) -> str:
        if port is None:
            return self.my_post.addr or ""
        return f"{my_ip}:{port}" if my_ip else f"0.0.0.0:{port}"

    def _build_payload(
        self,
        *,
        addr: str,
        giuroll: bool,
        autopunch: bool,
        match_status: str,
        net_status: int,
    ) -> dict:
        return {
            "post_type": self.my_post.post_type or "casual",
            "challenge_upper": bool(self.my_post.challenge_upper),
            "ping_warn_enabled": self.ping_warn_enabled(),
            "ping_warn_ms": self.ping_warn_ms(),
            "ping_warn_giuroll_ms": self.ping_warn_giuroll_ms(),
            "addr": addr,
            "comment": self.my_post.comment or "",
            "stream_url": self.my_post.stream_url or "",
            "giuroll": giuroll,
            "autopunch": autopunch,
            "match_status": match_status,
            "net_status": net_status,
        }

    def _prune_pending_requests(self) -> None:
        cutoff = time.time() - PENDING_REQUEST_TTL_SEC
        self.pending_requests = [
            r for r in self.pending_requests if r.received_at >= cutoff
        ]

    def _request_type_label(self, req_type: str) -> str:
        if req_type == "giuroll_request":
            return t("req.giuroll")
        if req_type == "casual_invite":
            return t("req.casual_invite")
        return req_type

    def _message_notify_text(self, msg_type: str, from_name: str) -> str:
        if msg_type == "giuroll_request":
            return t("msg.giuroll_request", name=from_name)
        if msg_type == "casual_invite":
            return t("msg.casual_invite", name=from_name)
        return t("msg.generic", name=from_name)

    async def reply_request(self, message_id: str, reply: str) -> None:
        self._prune_pending_requests()
        pending = next(
            (r for r in self.pending_requests if r.message_id == message_id),
            None,
        )
        if pending is None:
            self.notify_sink(t("notify.reply_missing"))
            return
        if not self.has_active_post():
            self.notify_sink(t("notify.reply_failed_closed"))
            return

        try:
            await self.api.reply_message(
                self.my_post.id,
                self.owner_token,
                message_id,
                reply,
            )
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (403, 404):
                self.notify_sink(t("notify.reply_failed_closed"))
            else:
                self.notify_sink(t("notify.reply_failed"))
            return
        except httpx.HTTPError:
            self.notify_sink(t("notify.reply_failed_closed"))
            return

        self.pending_requests = [
            r for r in self.pending_requests if r.message_id != message_id
        ]
        action = t("notify.reply_accept") if reply == "accept" else t("notify.reply_decline")
        self.notify_sink(t("notify.reply_sent", name=pending.from_name, action=action))

    # -----------------
    # loops
    # -----------------
    async def sync_initial(self) -> None:
        await self._validate_session()

    async def _validate_session(self) -> None:
        """起動時にセッションを検証する。サーバー側で IP も最新化される。"""
        if not self.api.session_token:
            return
        try:
            me = await self.api.auth_me()
            name = str(me.get("name", ""))
            if name and name != self.discord_user:
                self.discord_user = name
                self.config_mgr.set_value("auth", "username", name)
            self.log_sink("info", f"Discord ログイン中: {self.discord_user}")
            await self._refresh_lobby_badge()
            await self._sync_replay_refusal_to_server()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                self._clear_expired_session()
        except httpx.HTTPError:
            pass  # オフライン等。セッションは保持したままにする

    async def _check_update(self) -> None:
        result = await self.api.check_update()
        if result is None:
            err = getattr(self.api, "_last_update_check_error", None)
            if err:
                self.log_sink("error", f"Update check failed: {err}")
            return
        latest_tag, release_url = result
        try:
            if _parse_version(latest_tag) > _parse_version(__version__):
                self.update_available = (latest_tag, release_url)
                if self._notified_update_tag != latest_tag:
                    self.notify_sink(
                        t("notify.update_available", tag=latest_tag)
                    )
                    self._notified_update_tag = latest_tag
                self.log_sink(
                    "warn",
                    f"New version {latest_tag} available (current: v{__version__}). {release_url}",
                )
        except Exception:
            pass

    async def update_check_loop(self) -> None:
        """起動直後と、以後6時間ごとに更新を確認する。"""
        while not self._stop.is_set():
            await self._check_update()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=UPDATE_CHECK_INTERVAL_SEC)
            except asyncio.TimeoutError:
                pass

    async def detector_loop(self) -> None:
        try:
            my_ip = await self.api.myip()
        except Exception as e:
            self.log_sink("error", f"Detector error: {e}")
            my_ip = ""

        while not self._stop.is_set():
            try:
                st: DetectionState = read_detection_state()
                self._track_detect_error(st.detect_error)
                self._log_detect_snapshot(st)
                if (
                    st.alive
                    and st.exe_path
                    and not self.tool_mgr.path("soku")
                ):
                    self.log_sink("info", f"Soku path auto-set: {st.exe_path}")
                self._sync_tools_from_detection(st)
                act = self.on_detect(st, my_ip=my_ip)
                if act:
                    await self._action_q.put(act)
                if self._pending_local_match is not None:
                    payload = self._pending_local_match
                    self._pending_local_match = None
                    asyncio.create_task(self._record_local_match(payload))
            except Exception as e:
                self.log_sink("error", f"Detector loop error: {e}")
            await asyncio.sleep(DETECT_INTERVAL_SEC)

    async def api_loop(self) -> None:
        while not self._stop.is_set():
            act = await self._action_q.get()
            try:
                if act.type == "create":
                    res = await self.api.create(act.payload)
                    self._on_create_result(res, giuroll=bool(act.payload.get("giuroll")))

                elif act.type == "update":
                    resp = await self.api.update(
                        self.my_post.id, self.owner_token, act.payload
                    )
                    self._sync_post_reachability_from_server(resp)
                    self._try_copy_host_info_from_server(resp)
                    for msg in resp.get("messages") or []:
                        msg_type = msg.get("type", "")
                        from_name = msg.get("from_name", "")
                        text = self._message_notify_text(msg_type, from_name)
                        if msg_type in ("giuroll_request", "casual_invite"):
                            message_id = str(msg.get("id", ""))
                            if message_id:
                                self._prune_pending_requests()
                                self.pending_requests.append(
                                    PendingRequest(
                                        message_id=message_id,
                                        req_type=msg_type,
                                        from_name=from_name,
                                        received_at=time.time(),
                                    )
                                )
                                self.request_sink(
                                    self.pending_requests[-1],
                                    text,
                                )
                            else:
                                self.notify_sink(text)
                            self.log_sink("info", text)
                    for warn in resp.get("ping_warnings") or []:
                        from_name = str(warn.get("from_name") or "")
                        rtt_ms = int(warn.get("rtt_ms") or 0)
                        threshold = int(warn.get("threshold_ms") or self.ping_warn_ms())
                        msg = t(
                            "notify.high_ping",
                            name=from_name,
                            ms=rtt_ms,
                            threshold=threshold,
                        )
                        self.notify_sink(msg)
                        self.log_sink("info", msg)
                    guest_connected = resp.get("guest_connected")
                    if not guest_connected:
                        self._notified_casual_fallback = False
                    elif (
                        self.my_post.post_type == "ranked"
                        and guest_connected
                        and not resp.get("ranked_active")
                        and not self._notified_casual_fallback
                    ):
                        msg = t("notify.casual_fallback")
                        self.notify_sink(msg)
                        self.log_sink("info", msg)
                        self._notified_casual_fallback = True

                elif act.type == "close":
                    await self.api.close(
                        self.my_post.id,
                        self.owner_token,
                        act.payload.get("reason", "auto"),
                    )
                    self.clear_my_post()

                elif act.type == "result":
                    try:
                        await self.api.report_result(
                            self.my_post.id,
                            self.owner_token,
                            act.payload["winner"],
                            host_char=act.payload.get("host_char"),
                            guest_char=act.payload.get("guest_char"),
                            host_profile=act.payload.get("host_profile", ""),
                            guest_profile=act.payload.get("guest_profile", ""),
                        )
                        self._replay_result_reported = True
                        self.log_sink(
                            "info",
                            f"Match result reported: {act.payload['winner']}",
                        )
                    except httpx.HTTPError as e:
                        self.log_sink("error", f"Result report failed: {e}")

                elif act.type == "guest_result":
                    try:
                        resp = await self.api.report_guest_match(
                            act.payload["winner"],
                            host_char=act.payload.get("host_char"),
                            guest_char=act.payload.get("guest_char"),
                            host_profile=act.payload.get("host_profile", ""),
                            guest_profile=act.payload.get("guest_profile", ""),
                        )
                        if resp.get("recorded") or resp.get("reason") == "duplicate":
                            self._replay_result_reported = True
                        if resp.get("recorded"):
                            self.log_sink(
                                "info",
                                t(
                                    "log.client_result_reported",
                                    winner=act.payload["winner"],
                                ),
                            )
                        elif resp.get("reason") == "duplicate":
                            self.log_sink(
                                "info",
                                t("log.client_result_duplicate"),
                            )
                    except httpx.HTTPError as e:
                        self.log_sink(
                            "error",
                            t("log.client_result_failed", error=e),
                        )

            except httpx.HTTPStatusError as e:
                self._on_api_error(act, e)
            except httpx.HTTPError as e:
                if act.type == "create":
                    self._create_pending = False
                    self._last_sent_payload = None
                elif act.type == "close":
                    self._close_pending = False
                self.log_sink("error", f"API error: {e}")

    # -----------------
    # auto post logic
    # -----------------
    def on_detect(self, st: DetectionState, *, my_ip: str) -> Optional[Action]:
        now = time.time()

        # 一時停止が自然に切れたら一度だけ通知する (無期限停止は除く)
        if (
            self._detect_pause_until
            and not self.is_pause_indefinite()
            and now >= self._detect_pause_until
        ):
            self._detect_pause_until = 0.0
            self._host_unreachable_notified = False
            self.log_sink("info", "Auto posting pause expired")
            self.notify_sink(t("notify.pause_resumed"))
            self.pause_ui_sink()

        # -----------------
        # process dead
        # -----------------
        if not st.alive:
            self.tool_mgr.reset_state()
            self._addr_copied = False
            self._local_net_active = False
            self._reset_session_score()
            self._update_tray_ui_state_idle()
            if self.has_active_post() and not self._close_pending:
                self._close_pending = True
                return Action("close", {"reason": "process_dead"})
            return None

        # -----------------
        # 一時停止中: 自動投稿だけ止める (既存の募集があれば閉じる)。
        # 戦績のローカル記録・クライアント側報告・リプレイ収集は停止中も動かす
        # -----------------
        paused = now < self._detect_pause_until
        if paused and self.has_active_post() and not self._close_pending:
            self._close_pending = True
            return Action("close", {"reason": "detect_paused"})

        # -----------------
        # classify
        # -----------------
        is_recruiting = (st.mode == "host_wait") and (st.port is not None)
        is_battle = (st.mode == "battle")
        in_net_flow = (
            is_recruiting
            or is_battle
            or st.mode in ("charsel", "loading")
            or st.net_side is not None
        )
        self._local_net_active = in_net_flow

        if not in_net_flow:
            self._round_battle_engaged = False
            self._round_char_ids = (None, None)
        elif is_battle:
            if self._stable_for("round_battle_engaged", 0.15, seen=True):
                self._round_battle_engaged = True
            live_lchar, live_rchar = _match_char_ids(st)
            if live_lchar is not None and live_rchar is not None:
                self._round_char_ids = (live_lchar, live_rchar)

        round_battle_engaged = self._round_battle_engaged

        if self._stable_for("session_score_idle", 5.0, seen=not in_net_flow):
            self._reset_session_score()

        if in_net_flow and st.net_side != "client":
            self._last_keepalive_ts = now

        match_status = self._build_match_status(
            st,
            is_recruiting=is_recruiting,
            is_battle=is_battle,
        )
        self._update_tray_ui_state(
            is_recruiting=is_recruiting,
            is_battle=is_battle,
            st=st,
            my_ip=my_ip,
            match_status=match_status,
        )

        # -----------------
        # 他ホストに凸ったら自分の募集を閉じる
        # -----------------
        if (
            self.has_active_post()
            and self._stable_for("client_side", 1.0, seen=(st.net_side == "client"))
            and not self._close_pending
        ):
            self._close_pending = True
            return Action("close", {"reason": "joined_other_host"})

        # -----------------
        # 0) KO 確定 -> ローカル戦績 (ホスト/クライアント、ログイン不要)
        # -----------------
        host_char, guest_char = _match_char_ids(st)
        if host_char is None:
            host_char = self._round_char_ids[0]
        if guest_char is None:
            guest_char = self._round_char_ids[1]
        ko_fp = (
            _ko_fingerprint(st, host_char=host_char, guest_char=guest_char)
            if host_char is not None and guest_char is not None
            else ""
        )
        if (
            st.net_side in ("host", "client")
            and ko_fp
            and ko_fp != self._last_ko_fingerprint
            and _ko_recordable(
                is_battle=is_battle,
                mode=st.mode,
                round_battle_engaged=round_battle_engaged,
            )
        ):
            self._last_ko_fingerprint = ko_fp
            self._round_battle_engaged = False
            self._round_char_ids = (None, None)
            my_side = "host" if st.net_side == "host" else "client"
            winner = "host" if st.lwin == 2 else "guest"
            ranked = 0
            if (
                st.net_side == "host"
                and self.has_active_post()
                and self.my_post.post_type == "ranked"
            ):
                ranked = 1
            payload = {
                "my_side": my_side,
                "winner": winner,
                "host_char": host_char,
                "guest_char": guest_char,
                "host_profile": (st.lprof or ""),
                "guest_profile": (st.rprof or ""),
                "ranked": ranked,
            }
            self._pending_local_match = payload
            self._handle_session_score(payload)

        # -----------------
        # 0b) KO 確定 -> result (ホスト側のみ。クライアントとして凸った対戦は自分の募集に反映しない)
        # -----------------
        if (
            st.net_side == "host"
            and self.has_active_post()
            and not self._result_reported
            and _ko_decided(st)
            and host_char is not None
            and guest_char is not None
            and _ko_recordable(
                is_battle=is_battle,
                mode=st.mode,
                round_battle_engaged=round_battle_engaged,
            )
        ):
            self._result_reported = True
            winner = "host" if st.lwin == 2 else "guest"
            return Action("result", {
                "winner": winner,
                "host_char": host_char,
                "guest_char": guest_char,
                "host_profile": (st.lprof or ""),
                "guest_profile": (st.rprof or ""),
            })

        # クライアント側: ホストが asobby 非導入でも戦績を補完報告する
        if (
            st.net_side == "client"
            and not self._result_reported
            and self.is_logged_in()
            and _ko_decided(st)
            and host_char is not None
            and guest_char is not None
            and _ko_recordable(
                is_battle=is_battle,
                mode=st.mode,
                round_battle_engaged=round_battle_engaged,
            )
        ):
            self._result_reported = True
            winner = "host" if st.lwin == 2 else "guest"
            return Action("guest_result", {
                "winner": winner,
                "host_char": host_char,
                "guest_char": guest_char,
                "host_profile": (st.lprof or ""),
                "guest_profile": (st.rprof or ""),
            })

        # -----------------
        # リプレイ収集 (ホスト/クライアント両方)
        # -----------------
        is_net_battle = is_battle and st.net_side in ("host", "client")

        if is_net_battle and self._battle_start_ts == 0.0:
            if self._stable_for("battle_enter", 0.5, seen=True):
                self._battle_start_ts = now
                self._replay_result_reported = False
                # 新ラウンド開始時のみ KO 報告フラグをリセットする。
                # btl_mode!=5 への一瞬の落ち込みでリセットすると二重登録になる。
                self._result_reported = False
                self._last_ko_fingerprint = ""

        if (
            is_net_battle
            and _ko_decided(st)
            and _ko_recordable(
                is_battle=is_battle,
                mode=st.mode,
                round_battle_engaged=round_battle_engaged,
            )
        ):
            self._replay_pending = True
            self._replay_match_meta = {
                "host_profile": (st.lprof or ""),
                "guest_profile": (st.rprof or ""),
                "winner": "host" if st.lwin == 2 else "guest",
                "my_side": st.net_side or "",
            }

        if self._replay_pending and not is_net_battle:
            self._replay_pending = False
            battle_start_ts = self._battle_start_ts
            battle_end_ts = now
            exe_path = st.exe_path
            self._battle_start_ts = 0.0
            asyncio.create_task(
                self._schedule_replay_upload(battle_start_ts, battle_end_ts, exe_path)
            )

        # -----------------
        # ホスト検知時の IP:Port + 使用ツール クリップボードコピーは、サーバー到達性
        # 判定後 (create/update 応答) に _try_copy_host_info_from_server で行う。
        # -----------------
        if self._stable_for("addr_copy_reset", 5.0, seen=(not is_recruiting and not is_battle)):
            self._addr_copied = False

        # -----------------
        # 1) recruiting -> create / update
        # -----------------
        # 一時停止中もカウンタは更新する (再開時にホスト継続中なら即投稿される)
        recruiting_stable = self._stable_for("recruiting", 3.0, seen=is_recruiting)
        if recruiting_stable and not paused:
            payload = self._build_payload(
                addr=self._current_addr(my_ip, st.port),
                giuroll=st.giuroll,
                autopunch=self._host_uses_autopunch(st),
                match_status=match_status,
                net_status=NET_ALIVE,
            )

            if not self.has_active_post():
                if not self.is_logged_in():
                    # まずブラウザのクッキーセッション引き継ぎを一度だけ試す
                    # (Web 側でログイン済みなら操作なしでログインが完了する)
                    if not self._auto_login_attempted:
                        self._auto_login_attempted = True
                        self.log_sink(
                            "info",
                            "Recruitment detected without login; trying browser session handoff",
                        )
                        asyncio.get_running_loop().create_task(self._auto_login())
                    elif (
                        not self._login_in_progress
                        and not self._notified_login_required
                    ):
                        self._notified_login_required = True
                        self.notify_sink(t("notify.login_required_post"))
                        self.log_sink(
                            "warn",
                            "Recruitment requires Discord login",
                        )
                elif not self._create_pending and now >= self._next_create_ts:
                    self._create_pending = True
                    self._last_sent_payload = payload
                    self.update_my_post(**payload)
                    return Action("create", payload)
            elif payload != self._last_sent_payload:
                self._seen_recruit_this_run = True
                self._last_sent_payload = payload
                self.update_my_post(**payload)
                return Action("update", payload)

        # -----------------
        # 投稿停止中: サーバー create はしないが、到達性はローカルで確認する
        # -----------------
        if (
            paused
            and recruiting_stable
            and my_ip
            and st.port
            and (now - self._last_host_self_check_ts) >= HOST_SELF_CHECK_INTERVAL_SEC
        ):
            addr = self._current_addr(my_ip, st.port)
            if addr:
                self._last_host_self_check_ts = now
                asyncio.get_running_loop().create_task(
                    self._self_check_host_when_paused(addr, self._host_uses_autopunch(st))
                )

        # -----------------
        # 2) battle -> update (ホスト側のみ)
        # -----------------
        if (
            not paused
            and st.net_side == "host"
            and self.has_active_post()
            and self._seen_recruit_this_run
            and self._stable_for("battle", 2.0, seen=is_battle)
        ):
            payload = self._build_payload(
                addr=self.my_post.addr or "",
                giuroll=st.giuroll,
                autopunch=self._host_uses_autopunch(st),
                match_status=match_status,
                net_status=self._host_net_status(st, is_battle=True),
            )

            if payload != self._last_sent_payload:
                self._last_sent_payload = payload
                self.update_my_post(**payload)
                return Action("update", payload)

        # -----------------
        # 3) heartbeat (updated_at を更新して TTL 失効を防ぐ)
        # -----------------
        if not paused and self.has_active_post() and (now - self._last_heartbeat_ts) >= HEARTBEAT_SEC:
            self._last_heartbeat_ts = now
            net_status = self._host_net_status(st, is_battle=is_battle)
            payload = self._build_payload(
                addr=self.my_post.addr or "",
                giuroll=st.giuroll,
                autopunch=self._host_uses_autopunch(st),
                match_status=match_status,
                net_status=net_status,
            )
            return Action("update", payload)

        # -----------------
        # 4) close
        # -----------------
        if self.has_active_post() and not self._close_pending:
            grace_ok = (now - self._last_keepalive_ts) >= self._close_grace_sec
            quiet = self._stable_for(
                "idle_or_other",
                5.0,
                seen=not in_net_flow,
            )

            if grace_ok and quiet and (not is_battle):
                self._close_pending = True
                return Action("close", {"reason": "recruit_end"})

        return None

    def _replay_candidate_dirs(self, exe_path: str) -> list[Path]:
        """th123.exe と同じフォルダ配下の replay/ を返す。

        Program Files 等への書き込みは VirtualStore にリダイレクトされる。
        その場合の保存先は exe 直下ではなく
        %LOCALAPPDATA%\\VirtualStore\\<ドライブルートからの exe 親ディレクトリ>\\replay
        になる。configex123.ini / ReplayLabelEx 等で replay/ 以下のサブフォルダに
        保存される場合もある。
        """
        if not exe_path:
            return []
        p = Path(exe_path)
        game_dir = p.parent
        dirs = [game_dir / "replay"]

        local_app = os.environ.get("LOCALAPPDATA", "")
        anchor = p.anchor
        if not local_app or not anchor:
            return dirs

        try:
            rel_dir = game_dir.relative_to(anchor)
        except ValueError:
            return dirs

        vs_root = Path(local_app) / "VirtualStore"
        if rel_dir.parts:
            dirs.append(vs_root / rel_dir / "replay")
        else:
            dirs.append(vs_root / "replay")
        return dirs

    @staticmethod
    def _replay_upload_key(path: Path) -> tuple[str, int]:
        try:
            return (str(path), int(path.stat().st_mtime))
        except OSError:
            return (str(path), 0)

    def _find_latest_replay(
        self, dirs: list[Path], battle_start_ts: float
    ) -> Optional[Path]:
        cutoff = battle_start_ts - REPLAY_MTIME_MARGIN_SEC if battle_start_ts > 0 else 0.0
        candidates: list[Path] = []
        for d in dirs:
            if not d.is_dir():
                continue
            try:
                # replay/ 直下だけでなく、日付フォルダ (26/07/17) や
                # ReplayLabelEx の %mode\ 等のサブフォルダも対象にする
                for rep in d.rglob("*.rep"):
                    if self._replay_upload_key(rep) in self._uploaded_replay_keys:
                        continue
                    try:
                        if rep.stat().st_mtime >= cutoff:
                            candidates.append(rep)
                    except OSError:
                        continue
            except OSError:
                continue
        if not candidates:
            return None
        return max(candidates, key=lambda p: p.stat().st_mtime)

    async def _schedule_replay_upload(
        self, battle_start_ts: float, battle_end_ts: float, exe_path: str
    ) -> None:
        deadline = time.time() + REPLAY_RESULT_WAIT_SEC
        while time.time() < deadline and not self._replay_result_reported:
            await asyncio.sleep(0.25)
        async with self._replay_upload_lock:
            meta = dict(self._replay_match_meta or {})
            self._replay_match_meta = None
            await self._upload_replay(
                battle_start_ts, battle_end_ts, exe_path, meta
            )

    async def _upload_replay(
        self,
        battle_start_ts: float,
        battle_end_ts: float,
        exe_path: str,
        meta: dict[str, str],
    ) -> None:
        if not self.is_logged_in():
            self.log_sink("info", "Replay upload skipped: not logged in")
            return
        if self.is_replay_refusal_active():
            self.log_sink("info", "Replay upload skipped: replay saving refused")
            return
        if not exe_path:
            self.log_sink("info", "Replay upload skipped: exe path unknown")
            return

        dirs = self._replay_candidate_dirs(exe_path)
        deadline = time.time() + REPLAY_POLL_TIMEOUT_SEC
        chosen: Optional[Path] = None

        while time.time() < deadline:
            chosen = self._find_latest_replay(dirs, battle_start_ts)
            if chosen is not None:
                break
            await asyncio.sleep(REPLAY_POLL_INTERVAL_SEC)

        if chosen is None:
            self.log_sink("info", "Replay file not found after battle")
            return

        await asyncio.sleep(REPLAY_POLL_INTERVAL_SEC)
        try:
            size1 = chosen.stat().st_size
            await asyncio.sleep(REPLAY_POLL_INTERVAL_SEC)
            size2 = chosen.stat().st_size
            if size1 != size2:
                self.log_sink("info", "Replay file still being written; skipping")
                return
        except OSError as e:
            self.log_sink("error", f"Replay stat failed: {e}")
            return

        if size2 > REPLAY_MAX_BYTES:
            self.log_sink("warn", f"Replay too large ({size2} bytes); skipping upload")
            return

        try:
            data = chosen.read_bytes()
        except OSError as e:
            self.log_sink("error", f"Replay read failed: {e}")
            return

        upload_key = self._replay_upload_key(chosen)

        # 相手側の報告 (host result / client report) がまだ届いておらず
        # no_match になることがあるためリトライする
        resp: dict = {}
        for attempt in range(REPLAY_UPLOAD_RETRIES + 1):
            if attempt > 0:
                await asyncio.sleep(REPLAY_UPLOAD_RETRY_DELAY_SEC)
            try:
                resp = await self.api.upload_replay(
                    data,
                    battle_ts=battle_end_ts,
                    host_profile=meta.get("host_profile", ""),
                    guest_profile=meta.get("guest_profile", ""),
                    winner=meta.get("winner", ""),
                    my_side=meta.get("my_side", ""),
                )
            except httpx.HTTPError as e:
                self.log_sink("error", f"Replay upload failed: {e}")
                return
            if resp.get("stored") or resp.get("reason") != "no_match":
                break

        if resp.get("stored"):
            self.log_sink(
                "info",
                f"Replay uploaded: {resp.get('filename', chosen.name)}",
            )
            self._uploaded_replay_keys.add(upload_key)
        else:
            reason = resp.get("reason", "unknown")
            self.log_sink("info", f"Replay not stored: {reason}")
            if reason == "refused":
                self.log_sink("info", t("log.replay_refusal_blocked"))
            if reason == "duplicate":
                self._uploaded_replay_keys.add(upload_key)

    async def _record_local_match(self, payload: dict) -> None:
        try:
            match_id = await asyncio.to_thread(self.local_store.record_local, **payload)
            self.log_sink("info", f"Local match recorded: {match_id}")
            if self.is_logged_in():
                unpushed = await asyncio.to_thread(
                    self.local_store.fetch_unpushed_by_id, match_id
                )
                if unpushed:
                    asyncio.create_task(self._defer_sync_match(match_id))
        except Exception as e:
            self.log_sink("warn", f"Local match record failed: {e}")

    async def _defer_sync_match(self, local_id: str) -> None:
        """posts/result 等の API 報告より先に sync しないよう遅延送信する。"""
        try:
            await asyncio.sleep(STATS_SYNC_DEFER_SEC)
            if await self._sync_match_by_id(local_id):
                self._replay_result_reported = True
        except Exception as e:
            self.log_sink("warn", f"Deferred stats push failed: {e}")

    async def _sync_match_by_id(self, local_id: str) -> bool:
        """1 件のローカル戦績を直ちにサーバーへ送る。"""
        rows = await asyncio.to_thread(
            self.local_store.fetch_unpushed_by_id, local_id
        )
        if not rows:
            return False
        resp = await self.api.sync_matches(
            [self._sync_payload_from_row(rows[0])]
        )
        linked = False
        for result in resp.get("results") or []:
            if str(result.get("client_id", "")) != local_id:
                continue
            status = str(result.get("status", ""))
            server_id = result.get("server_id")
            sid = str(server_id) if server_id else None
            await asyncio.to_thread(
                self.local_store.mark_pushed, local_id, sid
            )
            if status in ("imported", "duplicate") and sid:
                linked = True
                self.log_sink("info", f"Stats push: synced {local_id}")
        return linked

    async def _pull_server_matches(self) -> int:
        """サーバー側の新着戦績をローカルへ取り込む。"""
        since = await asyncio.to_thread(self.local_store.max_server_played_at)
        inserted = 0
        while True:
            resp = await self.api.fetch_my_matches(
                since=since, limit=STATS_SYNC_BATCH
            )
            rows = resp.get("matches") or []
            if rows:
                ins = await asyncio.to_thread(
                    self.local_store.merge_server_rows, rows
                )
                since = max(float(r["played_at"]) for r in rows)
                inserted += ins
            if len(rows) < STATS_SYNC_BATCH:
                break
        return inserted

    def _sync_payload_from_row(self, row: dict) -> dict:
        my_side = row["my_side"]
        if my_side == "client":
            my_side = "guest"
        if my_side == "host":
            my_char = row.get("host_char")
            opp_char = row.get("guest_char")
            my_profile = row.get("host_profile", "")
            opp_profile = row.get("guest_profile", "")
        else:
            my_char = row.get("guest_char")
            opp_char = row.get("host_char")
            my_profile = row.get("guest_profile", "")
            opp_profile = row.get("host_profile", "")
        return {
            "client_id": row["id"],
            "played_at": row["played_at"],
            "my_side": my_side,
            "winner": row["winner"],
            "my_char": my_char,
            "opp_char": opp_char,
            "my_profile": my_profile or "",
            "opp_profile": opp_profile or "",
        }

    async def stats_sync_loop(self) -> None:
        """ログイン時にサーバー戦績と双方向同期する。"""
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=STATS_SYNC_INITIAL_DELAY_SEC)
            return
        except asyncio.TimeoutError:
            pass

        while not self._stop.is_set():
            if self.is_logged_in():
                await self._sync_stats_once()
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=STATS_SYNC_INTERVAL_SEC
                )
                return
            except asyncio.TimeoutError:
                pass

    async def _sync_stats_once(self) -> None:
        try:
            await self._sync_stats_impl()
        except Exception as e:
            self.log_sink("warn", f"Stats sync failed: {e}")

    async def sync_stats_now(self) -> None:
        """トレイメニューからの手動同期。結果をトーストで通知する。"""
        if not self.is_logged_in():
            self.notify_sink(t("notify.sync_login_required"))
            return
        if self._stats_sync_running:
            self.notify_sink(t("notify.sync_running"))
            return
        try:
            pulled, pushed = await self._sync_stats_impl()
            self.notify_sink(t("notify.sync_ok", pulled=pulled, pushed=pushed))
        except Exception as e:
            self.log_sink("warn", f"Stats sync failed: {e}")
            self.notify_sink(t("notify.sync_failed"))

    async def _sync_stats_impl(self) -> tuple[int, int]:
        """サーバー戦績との双方向同期。(取得件数, 送信件数) を返す。"""
        if self._stats_sync_running:
            return (0, 0)
        self._stats_sync_running = True
        pulled = 0
        pushed = 0
        try:
            pulled += await self._pull_server_matches()

            while True:
                unpushed = await asyncio.to_thread(
                    self.local_store.fetch_unpushed, STATS_SYNC_DEFER_SEC
                )
                if not unpushed:
                    break
                batch = unpushed[:STATS_SYNC_BATCH]
                payload = [
                    self._sync_payload_from_row(r) for r in batch
                ]
                resp = await self.api.sync_matches(payload)
                for result in resp.get("results") or []:
                    client_id = str(result.get("client_id", ""))
                    server_id = result.get("server_id")
                    status = result.get("status", "")
                    if not client_id:
                        continue
                    sid = str(server_id) if server_id else None
                    await asyncio.to_thread(
                        self.local_store.mark_pushed, client_id, sid
                    )
                    if status == "imported":
                        pushed += 1
                        self.log_sink("info", f"Stats push: imported {client_id}")
                if len(unpushed) <= STATS_SYNC_BATCH:
                    break
        finally:
            self._stats_sync_running = False
        return (pulled, pushed)

    # -----------------
    # result / error handling
    # -----------------
    def _sync_post_reachability_from_server(self, data: dict) -> None:
        """サーバーが判定した AP 状態をローカルに反映する (404 再作成後も維持)。"""
        if "autopunch" in data:
            self.update_my_post(autopunch=bool(data["autopunch"]))
        if "direct_reachable" in data:
            self.update_my_post(direct_reachable=bool(data["direct_reachable"]))

    def _on_create_result(self, result: dict, *, giuroll: bool) -> None:
        self._create_pending = False
        post = result.get("post") or {}
        token = result.get("owner_token") or ""
        rid = post.get("id")
        if not rid or not token:
            self.log_sink("error", "create response missing id/owner_token")
            return

        self.owner_token = str(token)
        self._seen_recruit_this_run = True
        self.my_post = replace(self.my_post, id=str(rid))
        self._sync_post_reachability_from_server(post)
        self._try_copy_host_info_from_server(post)
        self.my_post_sink(self.my_post)

        post_type = str(post.get("post_type", "casual"))
        type_label = post_type_label(post_type)
        rank_display = format_system_rank(
            str(post.get("rank", "")),
            post.get("rating"),
        )
        self.log_sink(
            "info",
            f"Post created: {self.my_post.addr} [{type_label}] ({rank_display})",
        )

    def _on_api_error(self, act: Action, e: httpx.HTTPStatusError) -> None:
        code = e.response.status_code

        if act.type == "create":
            self._create_pending = False
            self._last_sent_payload = None
            self._next_create_ts = time.time() + CREATE_RETRY_COOLDOWN_SEC

            if code in (401, 403):
                self._clear_expired_session()
                self.notify_sink(t("notify.session_expired"))
            elif code == 409:
                self.log_sink("error", "Host not reachable. Please open the port or start autopunch.")
                self.notify_sink(t("notify.post_failed"), important=True)
            elif code == 429:
                self.log_sink("warn", "Rate limited by server. Retrying soon.")
            else:
                self.log_sink("error", f"API error: {e}")
            return

        # update / close
        if code in (403, 404):
            # サーバー側で投稿が消えている（TTL失効・再起動等）。
            # ローカル状態を破棄すれば、募集継続中なら次の周期で再作成される。
            self.log_sink("warn", "Post lost on server. Re-posting if still hosting.")
            self.clear_my_post()
        elif code == 409:
            if act.type == "update":
                new_addr = str((act.payload or {}).get("addr") or "")
                if new_addr and new_addr == (self.my_post.addr or ""):
                    self.log_sink(
                        "warn",
                        "Host reachability check failed temporarily; keeping post active.",
                    )
                    return
            # アドレス変更時の到達性検証に失敗。ローカルを破棄して
            # 次の周期の create（クールダウン付き）からやり直す。
            self.log_sink("error", "Host not reachable. Please open the port or start autopunch.")
            self.notify_sink(t("notify.post_failed"), important=True)
            self.clear_my_post()
            self._next_create_ts = time.time() + CREATE_RETRY_COOLDOWN_SEC
        else:
            if act.type == "close":
                self._close_pending = False  # リトライ可能にする
            self.log_sink("error", f"API error: {e}")

    # -----------------
    # Discord login
    # -----------------
    def is_logged_in(self) -> bool:
        return bool(self.api.session_token)

    async def _start_handoff_listener(
        self,
    ) -> tuple[asyncio.AbstractServer, int, asyncio.Future]:
        """127.0.0.1 の空きポートで待ち受け、/auth?code=... を 1 回受け取る。"""
        loop = asyncio.get_running_loop()
        code_fut: asyncio.Future = loop.create_future()

        async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                request_line = await asyncio.wait_for(reader.readline(), 10.0)
                while True:
                    line = await asyncio.wait_for(reader.readline(), 10.0)
                    if line in (b"\r\n", b"\n", b""):
                        break
                parts = request_line.decode("latin-1", errors="replace").split(" ")
                path = parts[1] if len(parts) >= 2 else ""
                parsed = urlsplit(path)
                code = dict(parse_qsl(parsed.query)).get("code", "")
                if parsed.path == "/auth" and code:
                    # 連携完了後はそのままロビーページへリダイレクトする
                    location = self.lobby_url().encode("ascii", "ignore")
                    if not code_fut.done():
                        code_fut.set_result(code)
                    writer.write(
                        b"HTTP/1.1 302 Found\r\n"
                        b"Location: " + location + b"\r\n"
                        b"Content-Length: 0\r\n"
                        b"Connection: close\r\n\r\n"
                    )
                else:
                    body = b"<html><body>asobby</body></html>"
                    writer.write(
                        b"HTTP/1.1 200 OK\r\n"
                        b"Content-Type: text/html; charset=utf-8\r\n"
                        b"Content-Length: " + str(len(body)).encode() + b"\r\n"
                        b"Connection: close\r\n\r\n" + body
                    )
                await writer.drain()
            except Exception:
                pass
            finally:
                try:
                    writer.close()
                except Exception:
                    pass

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        return server, port, code_fut

    async def login_discord(self, open_browser, *, force: bool = False) -> None:
        """Discord ログインフロー。ブラウザで /auth/client/handoff を開き、
        Web 側でログイン済みならワンタイムコードが即 localhost へ届く。
        未ログインなら Discord OAuth を挟んでから届く。
        force=True のときは既存 Web セッションを破棄し Discord アカウント選択を表示する。"""
        if self._login_in_progress:
            return
        self._login_in_progress = True
        try:
            server, port, code_fut = await self._start_handoff_listener()
            try:
                base = self.api.base.rstrip("/")
                url = f"{base}/auth/client/handoff?port={port}"
                if force:
                    url += "&force=1"
                open_browser(url)
                self.log_sink("info", "ブラウザで Discord ログインを確認しています...")
                code = await asyncio.wait_for(code_fut, timeout=180.0)
            finally:
                server.close()

            res = await self.api.auth_client_exchange(code)
            if res.get("status") == "ok":
                self.api.session_token = str(res["session_token"])
                self.discord_user = str((res.get("user") or {}).get("name", ""))
                self.config_mgr.set_values(
                    "auth",
                    session_token=self.api.session_token,
                    username=self.discord_user,
                )
                self._notified_login_required = False
                self._auto_login_attempted = False
                self.log_sink("info", f"Discord にログインしました: {self.discord_user}")
                self.notify_sink(t("notify.discord_login_ok", name=self.discord_user))
                await self._refresh_lobby_badge()
                await self._sync_replay_refusal_to_server()
        except asyncio.TimeoutError:
            self.log_sink("warn", "Discord ログインがタイムアウトしました")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 503:
                self.log_sink("error", "サーバーで Discord ログインが設定されていません")
            elif e.response.status_code == 404:
                self.log_sink("warn", "ログイン要求が期限切れになりました。もう一度お試しください")
            else:
                self.log_sink("error", f"Discord ログインに失敗: {e}")
        except httpx.HTTPError as e:
            self.log_sink("error", f"Discord ログインに失敗: {e}")
        finally:
            self._login_in_progress = False

    async def _auto_login(self) -> None:
        """募集検知時にログインしていない場合、ブラウザ経由で自動連携を試みる。
        Web 側でログイン済みなら操作なしで完了する。"""
        await self.login_discord(webbrowser.open, force=False)
        if not self.is_logged_in() and not self._notified_login_required:
            self._notified_login_required = True
            self.notify_sink(t("notify.login_required_post"))

    async def logout_discord(self) -> None:
        base = self.api.base.rstrip("/")
        if self.api.session_token:
            try:
                await self.api.auth_logout()
            except httpx.HTTPError:
                pass
        try:
            webbrowser.open(f"{base}/auth/logout")
        except Exception:
            pass
        self.api.session_token = ""
        self.discord_user = ""
        self.config_mgr.set_values("auth", session_token="", username="")
        self._lobby_badges = {"ranked": False, "casual": False}
        self.lobby_activity_sink()
        # 明示的なログアウト後はブラウザ連携の自動ログインを走らせない
        self._auto_login_attempted = True
        self.log_sink("info", "Discord からログアウトしました")

    def _clear_expired_session(self) -> None:
        self.api.session_token = ""
        self.discord_user = ""
        self.config_mgr.set_values("auth", session_token="", username="")
        self._lobby_badges = {"ranked": False, "casual": False}
        self.lobby_activity_sink()
        # 期限切れは自動再ログインの対象にする
        self._auto_login_attempted = False
        self.log_sink("warn", "Discord セッションが期限切れです。再ログインしてください")

    # -----------------
    # external updates
    # -----------------
    def update_my_post(self, **kwargs) -> None:
        filtered = {k: v for k, v in kwargs.items() if k in _POST_FIELD_NAMES}
        if not filtered:
            return
        self.my_post = replace(self.my_post, **filtered)
        self.my_post_sink(self.my_post)

    async def enqueue_settings_update(self) -> None:
        """募集中なら Ping 警告設定などをサーバーへ即反映する。"""
        if not self.has_active_post():
            return
        payload = self._build_payload(
            addr=self.my_post.addr or "",
            giuroll=bool(self.my_post.giuroll),
            autopunch=bool(self.my_post.autopunch),
            match_status=self.my_post.match_status or "",
            net_status=int(self.my_post.net_status or NET_ALIVE),
        )
        await self._action_q.put(Action("update", payload))

    def update_btn_labels(self, tool_name: str, is_active: bool) -> None:
        self.tool_mgr.set_active(tool_name, is_active)
        self.tool_mgr.sync_loaded_from_detection(tool_name, is_active)
        label = self.tool_mgr.button_label(tool_name)
        # 50ms ポーリングから毎回呼ばれるため、変化した時だけトレイに通知する
        if self._tool_labels.get(tool_name) == label:
            return
        self._tool_labels = {**self._tool_labels, tool_name: label}
        self.btn_labels_sink(self._tool_labels)

    def _sync_tools_from_detection(self, st: DetectionState) -> None:
        if st.alive and st.exe_path and not self.tool_mgr.path("soku"):
            self.tool_mgr.set_path("soku", st.exe_path)
        self.update_btn_labels("soku", st.alive)
        self.update_btn_labels("autopunch", st.autopunch)
        self.update_btn_labels("giuroll", st.giuroll)

    def lobby_url(self) -> str:
        base = self.config_mgr.get_api_base().rstrip("/") + "/"
        lang = get_lang()
        if lang != "ja":
            return f"{base}?lang={lang}"
        return base

    async def close(self) -> None:
        self._stop.set()
        if self.has_active_post():
            try:
                await self.api.close(self.my_post.id, self.owner_token, "app_exit")
            except Exception:
                pass
        await self.config_mgr.flush()
        await self.http.aclose()
