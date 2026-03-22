from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, replace
from typing import Dict, List, Optional, Literal, Any
from collections import defaultdict

import httpx

from sse_client import SSEClient
from api_client import ApiClient
from detect_api import DetectionState
from hisoutensoku_memory import read_detection_state
from services import Post, NET_ALIVE, NET_BATTLE
from config_manager import ConfigManager
from tool_manager import ToolManager


API_BASE = "http://127.0.0.1:8000"
ActionType = Literal["upsert", "close"]


@dataclass
class Action:
    type: ActionType
    payload: dict


class Controller:
    """Postsのローカルストア + 自動投稿制御"""

    def __init__(self, app) -> None:
        self.log_sink = app.emit_log
        self.posts_sink = app.emit_posts
        self.my_post_sink = app.emit_my_post
        self.btn_labels_sink = app.emit_btn_labels

        self.config_mgr = ConfigManager()
        self.config = self.config_mgr.get()
        self.tool_mgr = ToolManager(self.config_mgr)

        self.http = httpx.AsyncClient(timeout=10.0)
        self.api = ApiClient(self.http, API_BASE)
        self._action_q: asyncio.Queue[Action] = asyncio.Queue()
        self._stop = asyncio.Event()

        self._stable_counts = defaultdict(int)
        self._seen_recruit_this_run = False
        self._last_keepalive_ts = 0.0
        self._close_grace_sec = 4

        self._last_heartbeat_ts = 0.0
        self._heartbeat_sec = 3

        self._tool_labels: Dict[str, str] = {}
        self._posts: Dict[str, Post] = {}
        self._last_sent_payload: Optional[dict] = None

        self.my_post: Post = Post()
        self.update_my_post(**self._default_post_params())

    # -----------------
    # basic helpers
    # -----------------
    def _default_post_params(self) -> Dict[str, Any]:
        return self.config_mgr.get_post_defaults()

    def clear_my_post(self) -> None:
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
        return bool(self.my_post and self.my_post.id)

    def _stable_key(self, key: str, need: int, *, seen: bool) -> bool:
        if seen:
            self._stable_counts[key] += 1
        else:
            self._stable_counts[key] = 0
        return self._stable_counts[key] >= need

    def _build_match_status(self, st: DetectionState, *, is_recruiting: bool, is_battle: bool) -> str:
        lp = (st.lprof or "").strip()
        rp = (st.rprof or "").strip()
        lc = (st.lchar_name or "?").strip()
        rc = (st.rchar_name or "?").strip()

        if is_battle and lp and rp:
            return f"{lp}({lc}) vs {rp}({rc})"

        if is_recruiting and lp:
            return lp

        # charsel/other ではキャラ名を出さず、プロフィール名だけ
        if lp:
            return lp

        return ""

    def _current_addr(self, my_ip: str, port: Optional[int]) -> str:
        if port is None:
            return self.my_post.addr or ""
        return f"{my_ip}:{port}" if my_ip else f"0.0.0.0:{port}"

    def _compare_payload(self, payload: dict) -> dict:
        d = dict(payload)
        d.pop("id", None)
        return d

    # -----------------
    # sync / sse / loops
    # -----------------
    async def sync_initial(self) -> None:
        try:
            r = await self.http.get(f"{API_BASE}/posts")
            r.raise_for_status()
            posts: List[Post] = [Post(**x) for x in r.json()]
            self._posts = {p.id: p for p in posts}
            self._apply_posts()
        except Exception as e:
            self.log_sink("error", f"initial error: {e}")

    async def sse_loop(self) -> None:
        sse = SSEClient(self.http, f"{API_BASE}/sse/posts")
        try:
            async for ev in sse.events(self._stop):
                self._apply_sse(ev.event, ev.data)
        except Exception as e:
            self.log_sink("error", f"SSE error: {e}")

    async def detector_loop(self) -> None:
        try:
            my_ip = await self.api.myip()
        except Exception as e:
            self.log_sink("error", f"Detector error: {e}")
            my_ip = ""

        while not self._stop.is_set():
            st: DetectionState = read_detection_state()
            self.update_btn_labels("soku", st.alive)
            self.update_btn_labels("autopunch", st.autopunch)
            self.update_btn_labels("giuroll", st.giuroll)
            act = self.on_detect(st, my_ip=my_ip)
            if act:
                await self._action_q.put(act)
            await asyncio.sleep(1)

    async def api_loop(self) -> None:
        while not self._stop.is_set():
            act = await self._action_q.get()
            try:
                if act.type == "upsert":
                    res = await self.api.upsert(act.payload)
                    self.on_upsert_result(res)

                elif act.type == "close":
                    await self.api.close(act.payload["id"], act.payload.get("reason", "auto"))

                    # close成功時だけローカル状態を消す
                    self._seen_recruit_this_run = False
                    self._last_sent_payload = None
                    self.clear_my_post()
            except httpx.HTTPStatusError as e:
                if act.type == "upsert":
                    self._last_sent_payload = None
                    self._seen_recruit_this_run = False
                if e.response.status_code == 409:
                    self.log_sink("error", f"Please either open the port or start autopunch.")
                else:
                    self.log_sink("error", f"API error: {e}")

    def _apply_posts(self) -> None:
        self.posts_sink(self._posts.values())

    def _apply_sse(self, event: str, data: str) -> None:
        if event == "hello":
            return

        try:
            obj = json.loads(data) if isinstance(data, str) else data
        except Exception as e:
            self.log_sink("error", f"SSE decode error: {e}")
            return

        if event == "upsert":
            try:
                post = Post(**obj)
            except Exception as e:
                self.log_sink("error", f"SSE upsert parse error: {e}")
                return
            self._posts[post.id] = post

        elif event == "close":
            post_id = obj.get("id")
            if post_id:
                self._posts.pop(str(post_id), None)

        self._apply_posts()

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
            if self.has_active_post():
                post_id = self.my_post.id
                self._seen_recruit_this_run = False
                self._last_sent_payload = None
                return Action("close", {"id": post_id, "reason": "process_dead"})
            return None

        # -----------------
        # classify
        # -----------------
        is_recruiting = (st.mode == "host_wait") and (st.port is not None)
        is_battle = (st.mode == "battle")
        has_profile = bool((st.lprof or "").strip() or (st.rprof or "").strip())

        if is_battle or has_profile or is_recruiting:
            self._last_keepalive_ts = now

        match_status = self._build_match_status(
            st,
            is_recruiting=is_recruiting,
            is_battle=is_battle,
        )

        # -----------------
        # 1) recruiting upsert
        # -----------------
        if self._stable_key("recruiting", need=3, seen=is_recruiting):
            payload = {
                "id": self.my_post.id if self.has_active_post() else None,
                "rank": self.my_post.rank or "any",
                "addr": self._current_addr(my_ip, st.port),
                "comment": self.my_post.comment or "",
                "stream_url": self.my_post.stream_url or "",
                "giuroll": st.giuroll,
                "autopunch": st.autopunch,
                "match_status": match_status,
                "net_status": NET_ALIVE,
            }
            self._seen_recruit_this_run = True
            compare_payload = self._compare_payload(payload)
            if compare_payload != self._last_sent_payload:
                self._last_sent_payload = compare_payload
                self.update_my_post(**compare_payload)
                return Action("upsert", payload)

        # -----------------
        # 2) battle upsert
        # -----------------
        if self.has_active_post() and self._seen_recruit_this_run and self._stable_key("battle", need=2, seen=is_battle):
            prev = self._posts.get(self.my_post.id, self.my_post)

            payload = {
                "id": self.my_post.id,
                "rank": self.my_post.rank or prev.rank or "any",
                "addr": prev.addr or self.my_post.addr or "",
                "comment": self.my_post.comment or prev.comment or "",
                "stream_url": self.my_post.stream_url or prev.stream_url or "",
                "giuroll": st.giuroll,
                "autopunch": st.autopunch,
                "match_status": match_status,
                "net_status": NET_BATTLE,
            }

            compare_payload = self._compare_payload(payload)
            if compare_payload != self._last_sent_payload:
                self._last_sent_payload = compare_payload
                self.update_my_post(**compare_payload)
                return Action("upsert", payload)

        # -----------------
        # 3) heartbeat
        # -----------------
        if self.has_active_post() and (now - self._last_heartbeat_ts) >= self._heartbeat_sec:
            prev = self._posts.get(self.my_post.id, self.my_post)
            self._last_heartbeat_ts = now
            payload = {
                "id": self.my_post.id,
                "rank": self.my_post.rank or "any",
                "addr": self.my_post.addr or "",
                "comment": self.my_post.comment or "",
                "stream_url": self.my_post.stream_url or "",
                "giuroll": self.my_post.giuroll,
                "autopunch": self.my_post.autopunch,
                "match_status": prev.match_status or self.my_post.match_status or "",
                "net_status": self.my_post.net_status or NET_ALIVE,
            }
            self.update_my_post(**payload)
            return Action("upsert", payload)

        # -----------------
        # 4) close
        # -----------------
        if self.has_active_post():
            grace_ok = (now - self._last_keepalive_ts) >= self._close_grace_sec
            quiet = self._stable_key(
                "idle_or_other",
                need=5,
                seen=(not is_recruiting and not has_profile and not is_battle),
            )

            if grace_ok and quiet and (not is_battle):
                post_id = self.my_post.id
                self.clear_my_post()
                return Action("close", {"id": post_id, "reason": "recruit_end"})

        return None

    # -----------------
    # external updates
    # -----------------
    def on_upsert_result(self, result: dict) -> None:
        rid = result.get("id")
        if rid:
            self.my_post = replace(self.my_post, id=str(rid))
            self.my_post_sink(self.my_post)

    def update_my_post(self, **kwargs) -> None:
        self.my_post = replace(self.my_post, **kwargs)
        self.my_post_sink(self.my_post)

    def update_btn_labels(self, tool_name: str, is_active: bool) -> None:
        self.tool_mgr.set_active(tool_name, is_active)
        self._tool_labels = {
            **self._tool_labels,
            tool_name: self.tool_mgr.button_label(tool_name),
        }
        self.btn_labels_sink(self._tool_labels)


    async def close(self) -> None:
        self._stop.set()
        await self.config_mgr.flush()
        await self.http.aclose()
