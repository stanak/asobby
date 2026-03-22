from __future__ import annotations

import asyncio
import json
import time
import socket
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Optional
from uuid import uuid4
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

ALLOWED_STREAM_DOMAINS = {
    "youtube.com",
    "www.youtube.com",
    "youtu.be",
    "twitch.tv",
    "www.twitch.tv",
    "nicovideo.jp",
    "www.nicovideo.jp",
    "nico.ms",
}
POST_TTL_SEC = 3
CLEANUP_INTERVAL_SEC = 2


# ----------------------------
# Models
# ----------------------------
@dataclass
class Post:
    id: str = field(default_factory=lambda: uuid4().hex)
    rank: str = "any"
    addr: str = ""
    comment: str = ""
    updated_at: float = 0
    stream_url: str = ""
    giuroll: bool = False
    autopunch: bool = False
    match_status: str = ""
    net_status: int = 0


def now_ts() -> float:
    return time.time()


# ----------------------------
# API schemas
# ----------------------------

class UpsertPostIn(BaseModel):
    id: Optional[str] = None
    rank: str = "any"
    addr: str
    comment: str
    stream_url: str = ""
    giuroll: bool = False
    autopunch: bool = False
    match_status: str = ""
    net_status: int = 0


class ClosePostIn(BaseModel):
    id: str
    reason: str = "manual"


# ----------------------------
# SSE hub
# ----------------------------
class SSEHub:
    def __init__(self) -> None:
        self._queues: set[asyncio.Queue[str]] = set()
        self._lock = asyncio.Lock()

    async def subscribe(self) -> asyncio.Queue[str]:
        q: asyncio.Queue[str] = asyncio.Queue(maxsize=200)
        async with self._lock:
            self._queues.add(q)
        return q

    async def unsubscribe(self, q: asyncio.Queue[str]) -> None:
        async with self._lock:
            self._queues.discard(q)

    async def publish(self, event: str, data: dict[str, Any]) -> None:
        payload = self._format_sse(event, data)
        async with self._lock:
            queues = list(self._queues)
        for q in queues:
            if q.full():
                try:
                    _ = q.get_nowait()
                except Exception:
                    pass
            try:
                q.put_nowait(payload)
            except Exception:
                pass

    def _format_sse(self, event: str, data: dict[str, Any]) -> str:
        s = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        return f"event: {event}\ndata: {s}\n\n"


# ----------------------------
# App
# ----------------------------
app = FastAPI(title="assoby api", version="0.1")

HUB = SSEHub()
POSTS: Dict[str, Post] = {}


@app.get("/myip")
async def get_myip(request: Request) -> dict[str, str]:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        ip = xff.split(",")[0].strip()
        if ip:
            return {"ip": ip}

    xri = request.headers.get("x-real-ip")
    if xri:
        return {"ip": xri.strip()}

    client = request.client.host if request.client else ""
    return {"ip": client or ""}


@app.get("/posts")
async def list_posts() -> list[dict[str, Any]]:
    # 初回同期用
    return [asdict(p) for p in sorted(POSTS.values(), key=lambda x: x.updated_at, reverse=True)]


@app.post("/posts/upsert")
async def upsert_post(body: UpsertPostIn) -> dict[str, Any]:
    if not is_allowed_stream_url(body.stream_url):
        raise HTTPException(
            status_code=422,
            detail="stream_url must be youtube, twitch, or niconico",
        )

    pid = body.id or uuid4().hex

    p = POSTS.get(pid)
    is_new = p is None
    if p is None:
        p = Post(id=pid)

    # 新規投稿時だけ、AutoPunchなしならその場で hostability を確認
    if is_new and (not body.autopunch):
        await verify_hostable_or_raise(body.addr)

    p.rank = body.rank
    p.addr = body.addr
    p.comment = body.comment
    p.stream_url = body.stream_url
    p.giuroll = body.giuroll
    p.autopunch = body.autopunch
    p.match_status = body.match_status
    p.net_status = body.net_status
    p.updated_at = now_ts()

    POSTS[pid] = p

    data = asdict(p)
    await HUB.publish("upsert", data)
    return data


@app.post("/posts/close")
async def close_post(body: ClosePostIn) -> dict[str, Any]:
    p = POSTS.get(body.id)
    if not p:
        raise HTTPException(status_code=404, detail="post not found")

    del POSTS[body.id]
    await HUB.publish("close", {"id": body.id, "reason": body.reason, "ts": now_ts()})
    print(body.reason)
    return {"ok": True, "id": body.id}


@app.get("/sse/posts")
async def sse_posts(request: Request):
    q = await HUB.subscribe()

    async def gen():
        try:
            yield "event: hello\ndata: {}\n\n"

            while True:
                if await request.is_disconnected():
                    break
                msg = await q.get()
                yield msg
        finally:
            await HUB.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream")


async def cleanup_loop():
    while True:
        now = time.time()
        stale_ids = [
            post_id
            for post_id, post in list(POSTS.items())
            if (now - post.updated_at) >= POST_TTL_SEC
        ]
        for post_id in stale_ids:
            POSTS.pop(post_id, None)
            await HUB.publish("close", {"id": post_id, "reason": "ttl_expired", "ts": now_ts()})
        await asyncio.sleep(CLEANUP_INTERVAL_SEC)


@app.on_event("startup")
async def start_cleanup():
    app.state.cleanup_task = asyncio.create_task(cleanup_loop())


def soku_echo_packet(
    should_match: bool = False,
    profile_name: str = "asobby",
) -> bytes:
    profile_name_bytes = str.encode(profile_name, "shift-jis")
    return bytes.fromhex(
        "05"
        "647365d9" "ffc46e48" "8d7ca192" "31347295"
        "00000000" "28000000"
        f"{int(should_match):02}"
        f"{len(profile_name_bytes).to_bytes(1, 'big').hex()}"
        f"{profile_name_bytes.hex():0<48}"
        "00000000" "00000000" "00000000" "0000"
    )


def is_valid_reply(data: bytes) -> bool:
    print(data)
    return len(data) >= 1 and data[0] in (b'\x07', b'\x08')


def probe_host_once(
    host: str,
    port: int,
    packet: bytes,
    *,
    timeout_sec: float = 0.2,
) -> Optional[bytes]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.settimeout(timeout_sec)
        sock.sendto(packet, (host, port))
        data, _addr = sock.recvfrom(4096)
        return data
    except (socket.timeout, OSError):
        return None
    finally:
        sock.close()


def check_hostable_consecutive(
    host: str,
    port: int,
    *,
    should_match: bool = False,
    profile_name: str = "asobby",
    attempts: int = 5,
    interval_sec: float = 0.1,
    timeout_sec: float = 0.2,
    needed_consecutive: int = 2,
) -> bool:
    packet = soku_echo_packet(
        should_match=should_match,
        profile_name=profile_name,
    )

    successes = 0
    consecutive = 0
    replies: list[int] = []

    for i in range(attempts):
        reply = probe_host_once(
            host,
            port,
            packet,
            timeout_sec=timeout_sec,
        )

        if reply is not None:
            replies.append(reply[0])

            if is_valid_reply(reply):
                successes += 1
                consecutive += 1
            else:
                consecutive = 0
        else:
            consecutive = 0

        if consecutive >= needed_consecutive:
            return True

        if i != attempts - 1:
            time.sleep(interval_sec)

    return False


async def verify_hostable_or_raise(addr: str) -> None:
    try:
        host, port_s = addr.rsplit(":", 1)
        port = int(port_s)
    except Exception:
        raise HTTPException(status_code=422, detail="invalid addr")

    result = await asyncio.to_thread(
        check_hostable_consecutive,
        host,
        port,
    )

    if not result:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "host not reachable",
            },
        )

def is_allowed_stream_url(url: str) -> bool:
    url = (url or "").strip()
    if not url:
        return True  # 空は許可

    try:
        parsed = urlparse(url)
    except Exception:
        return False

    if parsed.scheme not in ("http", "https"):
        return False

    host = (parsed.netloc or "").lower().strip()
    if not host:
        return False

    # :443 みたいな port を除去
    if ":" in host:
        host = host.split(":", 1)[0]

    return host in ALLOWED_STREAM_DOMAINS
