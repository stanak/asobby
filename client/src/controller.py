from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Dict, Optional, Literal, Any, Tuple
from collections import defaultdict

import httpx

from api_client import ApiClient
from detect_api import DetectionState
from hisoutensoku_memory import read_detection_state
from local_store import LocalStore
from services import Post, NET_ALIVE, NET_BATTLE, POST_TYPE_LABEL, __version__, format_system_rank
from config_manager import ConfigManager
from tool_manager import ToolManager


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
REPLAY_MTIME_MARGIN_SEC = 10.0

STATS_SYNC_INITIAL_DELAY_SEC = 10.0
STATS_SYNC_INTERVAL_SEC = 10 * 60
STATS_SYNC_BATCH = 500


def _parse_version(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.lstrip("v").split(".") if x.isdigit())


@dataclass
class Action:
    type: ActionType
    payload: dict


class Controller:
    """非想天則の状態を検出して募集投稿を自動管理するエージェント"""

    def __init__(self, app) -> None:
        self.log_sink = app.emit_log
        self.notify_sink = app.emit_notify
        self.my_post_sink = app.emit_my_post
        self.btn_labels_sink = app.emit_btn_labels

        self.config_mgr = ConfigManager()
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
        self._local_match_recorded = False
        self._pending_local_match: Optional[dict] = None

        self.owner_token: str = ""
        self.my_post: Post = Post()
        self.update_my_post(**self._default_post_params())

        self.update_available: Optional[tuple[str, str]] = None
        self._notified_update_tag: str = ""

        # Discord ログイン（任意）。設定に保存済みのセッションを復元する。
        auth = self.config_mgr.get_section("auth")
        self.api.session_token = str(auth.get("session_token", ""))
        self.discord_user: str = str(auth.get("username", "")) if self.api.session_token else ""
        self._login_in_progress = False
        self._notified_login_required = False
        self._notified_casual_fallback = False

        self._battle_start_ts = 0.0
        self._replay_pending = False
        self._uploaded_replays: set[str] = set()
        self._pending_replay_upload: Optional[Tuple[float, str]] = None

    # -----------------
    # basic helpers
    # -----------------
    def _default_post_params(self) -> Dict[str, Any]:
        # post_defaults には comment_presets 等 Post に無いキーも入るので絞る
        d = self.config_mgr.get_post_defaults()
        return {k: d[k] for k in ("post_type", "comment", "stream_url") if k in d}

    def comment_presets(self) -> list[str]:
        v = self.config_mgr.get_value("post_defaults", "comment_presets", [])
        if not isinstance(v, list):
            return []
        return [str(x) for x in v if str(x).strip()]

    def set_active_comment(self, text: str) -> None:
        self.config_mgr.set_post_default("comment", text)
        self.update_my_post(comment=text)

    def stream_presets(self) -> list[str]:
        v = self.config_mgr.get_value("post_defaults", "stream_url_presets", [])
        if not isinstance(v, list):
            return []
        return [str(x) for x in v if str(x).strip()]

    def set_active_stream(self, text: str) -> None:
        self.config_mgr.set_post_default("stream_url", text)
        self.update_my_post(stream_url=text)

    def clear_my_post(self) -> None:
        self.owner_token = ""
        self._seen_recruit_this_run = False
        self._last_sent_payload = None
        self._create_pending = False
        self._close_pending = False
        self._result_reported = False
        self._local_match_recorded = False
        self._pending_local_match = None
        self._notified_casual_fallback = False
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

    def has_active_post(self) -> bool:
        return bool(self.my_post.id and self.owner_token)

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
            "addr": addr,
            "comment": self.my_post.comment or "",
            "stream_url": self.my_post.stream_url or "",
            "giuroll": giuroll,
            "autopunch": autopunch,
            "match_status": match_status,
            "net_status": net_status,
        }

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
                        f"asobby {latest_tag} が公開されています。トレイメニューから開けます"
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
            st: DetectionState = read_detection_state()
            self.update_btn_labels("soku", st.alive)
            self.update_btn_labels("giuroll", st.giuroll)
            act = self.on_detect(st, my_ip=my_ip)
            if act:
                await self._action_q.put(act)
            if self._pending_replay_upload is not None:
                battle_start_ts, exe_path = self._pending_replay_upload
                self._pending_replay_upload = None
                asyncio.create_task(self._upload_replay(battle_start_ts, exe_path))
            if self._pending_local_match is not None:
                payload = self._pending_local_match
                self._pending_local_match = None
                asyncio.create_task(self._record_local_match(payload))
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
                    guest_connected = resp.get("guest_connected")
                    if not guest_connected:
                        self._notified_casual_fallback = False
                    elif (
                        self.my_post.post_type == "ranked"
                        and guest_connected
                        and not resp.get("ranked_active")
                        and not self._notified_casual_fallback
                    ):
                        msg = (
                            "異なるランク帯またはログインしていない相手とのマッチングのため、"
                            "この対戦はカジュアル扱いになります"
                        )
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
                        if resp.get("recorded"):
                            self.log_sink(
                                "info",
                                f"Guest match result reported: {act.payload['winner']}",
                            )
                        elif resp.get("reason") == "duplicate":
                            self.log_sink(
                                "info",
                                "Guest match result duplicate (already recorded)",
                            )
                    except httpx.HTTPError as e:
                        self.log_sink("error", f"Guest result report failed: {e}")

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

        # -----------------
        # process dead
        # -----------------
        if not st.alive:
            self.tool_mgr.reset_state()
            if self.has_active_post() and not self._close_pending:
                self._close_pending = True
                return Action("close", {"reason": "process_dead"})
            return None

        # -----------------
        # classify
        # -----------------
        is_recruiting = (st.mode == "host_wait") and (st.port is not None)
        is_battle = (st.mode == "battle")
        has_profile = bool((st.lprof or "").strip() or (st.rprof or "").strip())

        if is_battle or has_profile or is_recruiting:
            if st.net_side != "client":
                self._last_keepalive_ts = now

        match_status = self._build_match_status(
            st,
            is_recruiting=is_recruiting,
            is_battle=is_battle,
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
        # 0) KO 確定 -> ローカル戦績 (ホスト/ゲスト、ログイン不要)
        # -----------------
        if (
            st.net_side in ("host", "client")
            and is_battle
            and not self._local_match_recorded
            and st.btl_mode == 5
            and st.lwin is not None
            and st.rwin is not None
            and 0 <= st.lwin <= 2
            and 0 <= st.rwin <= 2
            and (st.lwin == 2 or st.rwin == 2)
        ):
            self._local_match_recorded = True
            my_side = "host" if st.net_side == "host" else "guest"
            winner = "host" if st.lwin == 2 else "guest"
            ranked = 0
            if (
                st.net_side == "host"
                and self.has_active_post()
                and self.my_post.post_type == "ranked"
            ):
                ranked = 1
            self._pending_local_match = {
                "my_side": my_side,
                "winner": winner,
                "host_char": st.lchar_id,
                "guest_char": st.rchar_id,
                "host_profile": (st.lprof or ""),
                "guest_profile": (st.rprof or ""),
                "ranked": ranked,
            }

        # -----------------
        # 0b) KO 確定 -> result (ホスト側のみ。ゲスト対戦は自分の募集に反映しない)
        # -----------------
        if (
            st.net_side == "host"
            and is_battle
            and self.has_active_post()
            and not self._result_reported
            and st.btl_mode == 5
            and st.lwin is not None
            and st.rwin is not None
            and 0 <= st.lwin <= 2
            and 0 <= st.rwin <= 2
            and (st.lwin == 2 or st.rwin == 2)
        ):
            self._result_reported = True
            winner = "host" if st.lwin == 2 else "guest"
            return Action("result", {
                "winner": winner,
                "host_char": st.lchar_id,
                "guest_char": st.rchar_id,
                "host_profile": (st.lprof or ""),
                "guest_profile": (st.rprof or ""),
            })

        # ゲスト側: ホストが asobby 非導入でも戦績を補完報告する
        if (
            st.net_side == "client"
            and is_battle
            and not self._result_reported
            and self.is_logged_in()
            and st.btl_mode == 5
            and st.lwin is not None
            and st.rwin is not None
            and 0 <= st.lwin <= 2
            and 0 <= st.rwin <= 2
            and (st.lwin == 2 or st.rwin == 2)
        ):
            self._result_reported = True
            winner = "host" if st.lwin == 2 else "guest"
            return Action("guest_result", {
                "winner": winner,
                "host_char": st.lchar_id,
                "guest_char": st.rchar_id,
                "host_profile": (st.lprof or ""),
                "guest_profile": (st.rprof or ""),
            })

        if not is_battle:
            self._result_reported = False
            self._local_match_recorded = False

        # -----------------
        # リプレイ収集 (ホスト/ゲスト両方)
        # -----------------
        is_net_battle = is_battle and st.net_side in ("host", "client")

        if is_net_battle and self._battle_start_ts == 0.0:
            if self._stable_for("battle_enter", 0.5, seen=True):
                self._battle_start_ts = now

        if (
            is_net_battle
            and st.btl_mode == 5
            and st.lwin is not None
            and st.rwin is not None
            and 0 <= st.lwin <= 2
            and 0 <= st.rwin <= 2
            and (st.lwin == 2 or st.rwin == 2)
        ):
            self._replay_pending = True

        if self._replay_pending and not is_net_battle:
            self._replay_pending = False
            self._pending_replay_upload = (self._battle_start_ts, st.exe_path)
            self._battle_start_ts = 0.0

        # -----------------
        # 1) recruiting -> create / update
        # -----------------
        if self._stable_for("recruiting", 3.0, seen=is_recruiting):
            payload = self._build_payload(
                addr=self._current_addr(my_ip, st.port),
                giuroll=st.giuroll,
                autopunch=st.autopunch,
                match_status=match_status,
                net_status=NET_ALIVE,
            )

            if not self.has_active_post():
                if not self.is_logged_in():
                    if not self._notified_login_required:
                        self._notified_login_required = True
                        self.notify_sink(
                            "募集には Discord ログインが必要です。トレイメニューからログインしてください"
                        )
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
        # 2) battle -> update (ホスト側のみ)
        # -----------------
        if (
            st.net_side == "host"
            and self.has_active_post()
            and self._seen_recruit_this_run
            and self._stable_for("battle", 2.0, seen=is_battle)
        ):
            payload = self._build_payload(
                addr=self.my_post.addr or "",
                giuroll=st.giuroll,
                autopunch=st.autopunch,
                match_status=match_status,
                net_status=NET_BATTLE,
            )

            if payload != self._last_sent_payload:
                self._last_sent_payload = payload
                self.update_my_post(**payload)
                return Action("update", payload)

        # -----------------
        # 3) heartbeat (updated_at を更新して TTL 失効を防ぐ)
        # -----------------
        if self.has_active_post() and (now - self._last_heartbeat_ts) >= HEARTBEAT_SEC:
            self._last_heartbeat_ts = now
            payload = self._build_payload(
                addr=self.my_post.addr or "",
                giuroll=self.my_post.giuroll,
                autopunch=self.my_post.autopunch,
                match_status=self.my_post.match_status or "",
                net_status=self.my_post.net_status or NET_ALIVE,
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
                seen=(not is_recruiting and not has_profile and not is_battle),
            )

            if grace_ok and quiet and (not is_battle):
                self._close_pending = True
                return Action("close", {"reason": "recruit_end"})

        return None

    def _replay_candidate_dirs(self, exe_path: str) -> list[Path]:
        p = Path(exe_path)
        dirs = [p.parent / "replay"]
        local_app = os.environ.get("LOCALAPPDATA", "")
        if local_app and len(exe_path) >= 3 and exe_path[1] == ":":
            rel = exe_path[3:]
            parent_rel = str(Path(rel).parent)
            dirs.append(Path(local_app) / "VirtualStore" / parent_rel / "replay")
        return dirs

    def _find_latest_replay(
        self, dirs: list[Path], battle_start_ts: float
    ) -> Optional[Path]:
        cutoff = battle_start_ts - REPLAY_MTIME_MARGIN_SEC
        candidates: list[Path] = []
        for d in dirs:
            if not d.is_dir():
                continue
            try:
                for rep in d.glob("*.rep"):
                    if str(rep) in self._uploaded_replays:
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

    async def _upload_replay(self, battle_start_ts: float, exe_path: str) -> None:
        if not self.is_logged_in():
            self.log_sink("info", "Replay upload skipped: not logged in")
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

        try:
            resp = await self.api.upload_replay(data)
        except httpx.HTTPError as e:
            self.log_sink("error", f"Replay upload failed: {e}")
            return

        stored = bool(resp.get("stored"))
        if stored:
            self.log_sink(
                "info",
                f"Replay uploaded: {resp.get('filename', chosen.name)}",
            )
        else:
            reason = resp.get("reason", "unknown")
            self.log_sink("info", f"Replay not stored: {reason}")

        self._uploaded_replays.add(str(chosen))

    async def _record_local_match(self, payload: dict) -> None:
        try:
            match_id = await asyncio.to_thread(self.local_store.record_local, **payload)
            self.log_sink("info", f"Local match recorded: {match_id}")
        except Exception as e:
            self.log_sink("warn", f"Local match record failed: {e}")

    def _sync_payload_from_row(self, row: dict) -> dict:
        my_side = row["my_side"]
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
            since = await asyncio.to_thread(self.local_store.max_server_played_at)
            while True:
                resp = await self.api.fetch_my_matches(
                    since=since, limit=STATS_SYNC_BATCH
                )
                rows = resp.get("matches") or []
                if rows:
                    inserted = await asyncio.to_thread(
                        self.local_store.merge_server_rows, rows
                    )
                    since = max(float(r["played_at"]) for r in rows)
                    if inserted:
                        self.log_sink("info", f"Stats pull: {inserted} new match(es)")
                if len(rows) < STATS_SYNC_BATCH:
                    break

            while True:
                unpushed = await asyncio.to_thread(self.local_store.fetch_unpushed)
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
                        self.log_sink("info", f"Stats push: imported {client_id}")
                if len(unpushed) <= STATS_SYNC_BATCH:
                    break
        except Exception as e:
            self.log_sink("warn", f"Stats sync failed: {e}")

    # -----------------
    # result / error handling
    # -----------------
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
        self.my_post_sink(self.my_post)

        post_type = str(post.get("post_type", "casual"))
        type_label = POST_TYPE_LABEL.get(post_type, post_type)
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
                self.notify_sink("セッションが切れました。Discord に再ログインしてください")
            elif code == 409:
                self.log_sink("error", "Host not reachable. Please open the port or start autopunch.")
                self.notify_sink("募集に失敗しました: ポート開放または autopunch を確認してください")
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
            # アドレス変更時の到達性検証に失敗。ローカルを破棄して
            # 次の周期の create（クールダウン付き）からやり直す。
            self.log_sink("error", "Host not reachable. Please open the port or start autopunch.")
            self.notify_sink("募集に失敗しました: ポート開放または autopunch を確認してください")
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

    async def login_discord(self, open_browser) -> None:
        """Discord ログインフロー。open_browser(url) でブラウザを開き、
        完了までポーリングする。"""
        if self._login_in_progress:
            return
        self._login_in_progress = True
        try:
            start = await self.api.auth_device_start()
            open_browser(start["verify_url"])
            self.log_sink("info", "ブラウザで Discord ログインを完了してください")

            interval = float(start.get("interval", 2))
            deadline = time.time() + float(start.get("expires_in", 600))
            while time.time() < deadline and not self._stop.is_set():
                await asyncio.sleep(interval)
                res = await self.api.auth_device_poll(start["device_code"])
                if res.get("status") == "ok":
                    self.api.session_token = str(res["session_token"])
                    self.discord_user = str((res.get("user") or {}).get("name", ""))
                    self.config_mgr.set_values(
                        "auth",
                        session_token=self.api.session_token,
                        username=self.discord_user,
                    )
                    self._notified_login_required = False
                    self.log_sink("info", f"Discord にログインしました: {self.discord_user}")
                    return
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

    def logout_discord(self) -> None:
        self.api.session_token = ""
        self.discord_user = ""
        self.config_mgr.set_values("auth", session_token="", username="")
        self.log_sink("info", "Discord からログアウトしました")

    def _clear_expired_session(self) -> None:
        self.api.session_token = ""
        self.discord_user = ""
        self.config_mgr.set_values("auth", session_token="", username="")
        self.log_sink("warn", "Discord セッションが期限切れです。再ログインしてください")

    # -----------------
    # external updates
    # -----------------
    def update_my_post(self, **kwargs) -> None:
        self.my_post = replace(self.my_post, **kwargs)
        self.my_post_sink(self.my_post)

    def update_btn_labels(self, tool_name: str, is_active: bool) -> None:
        self.tool_mgr.set_active(tool_name, is_active)
        label = self.tool_mgr.button_label(tool_name)
        # 50ms ポーリングから毎回呼ばれるため、変化した時だけトレイに通知する
        if self._tool_labels.get(tool_name) == label:
            return
        self._tool_labels = {**self._tool_labels, tool_name: label}
        self.btn_labels_sink(self._tool_labels)

    def lobby_url(self) -> str:
        return self.config_mgr.get_api_base().rstrip("/") + "/"

    async def close(self) -> None:
        self._stop.set()
        if self.has_active_post():
            try:
                await self.api.close(self.my_post.id, self.owner_token, "app_exit")
            except Exception:
                pass
        await self.config_mgr.flush()
        await self.http.aclose()
