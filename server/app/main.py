from __future__ import annotations

import asyncio
import json
import secrets
import time
import socket
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional
from uuid import uuid4
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

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

# クライアントは 5 秒間隔でハートビートを送る。TTL はその 4 倍で
# 一時的なネットワーク断や GC 停止では投稿が消えないようにする。
POST_TTL_SEC = 20
CLEANUP_INTERVAL_SEC = 5
SSE_PING_INTERVAL_SEC = 15

# 作成レート制限（IP 単位）
CREATE_MIN_INTERVAL_SEC = 2.0
MAX_ACTIVE_POSTS_PER_IP = 2

STATIC_DIR = Path(__file__).parent / "static"


# ----------------------------
# Models
# ----------------------------
@dataclass
class Post:
    """クライアントに公開してよいフィールドのみを持つ。"""

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


@dataclass
class PostRecord:
    """サーバー内部でのみ保持する情報（owner_token 等）を含むレコード。"""

    post: Post
    owner_token: str
    creator_ip: str


def now_ts() -> float:
    return time.time()


# ----------------------------
# API schemas
# ----------------------------
class CreatePostIn(BaseModel):
    rank: str = Field(default="any", max_length=16)
    addr: str = Field(max_length=64)
    comment: str = Field(default="", max_length=200)
    stream_url: str = Field(default="", max_length=300)
    giuroll: bool = False
    autopunch: bool = False
    match_status: str = Field(default="", max_length=200)
    net_status: int = 0


class UpdatePostIn(CreatePostIn):
    id: str = Field(max_length=64)
    owner_token: str = Field(max_length=128)


class ClosePostIn(BaseModel):
    id: str = Field(max_length=64)
    owner_token: str = Field(max_length=128)
    reason: str = Field(default="manual", max_length=64)


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

    async def publish(self, event: str, data: Any) -> None:
        payload = format_sse(event, data)
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


def format_sse(event: str, data: Any) -> str:
    s = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {s}\n\n"


# ----------------------------
# App / state
# ----------------------------
HUB = SSEHub()
RECORDS: Dict[str, PostRecord] = {}
LAST_CREATE_AT: Dict[str, float] = {}


async def cleanup_loop() -> None:
    while True:
        now = time.time()
        stale_ids = [
            post_id
            for post_id, rec in list(RECORDS.items())
            if (now - rec.post.updated_at) >= POST_TTL_SEC
        ]
        for post_id in stale_ids:
            RECORDS.pop(post_id, None)
            await HUB.publish("close", {"id": post_id, "reason": "ttl_expired", "ts": now_ts()})
        await asyncio.sleep(CLEANUP_INTERVAL_SEC)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(cleanup_loop())
    try:
        yield
    finally:
        task.cancel()


app = FastAPI(title="asobby api", version="0.2", lifespan=lifespan)


def client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        ip = xff.split(",")[0].strip()
        if ip:
            return ip

    xri = request.headers.get("x-real-ip")
    if xri:
        return xri.strip()

    return request.client.host if request.client else ""


def sorted_public_posts() -> list[dict[str, Any]]:
    records = sorted(RECORDS.values(), key=lambda r: r.post.updated_at, reverse=True)
    return [asdict(r.post) for r in records]


def get_record_or_raise(post_id: str, owner_token: str) -> PostRecord:
    rec = RECORDS.get(post_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="post not found")
    if not secrets.compare_digest(rec.owner_token, owner_token):
        raise HTTPException(status_code=403, detail="invalid owner_token")
    return rec


# ----------------------------
# Routes
# ----------------------------
@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/myip")
async def get_myip(request: Request) -> dict[str, str]:
    return {"ip": client_ip(request)}


@app.get("/posts")
async def list_posts() -> list[dict[str, Any]]:
    return sorted_public_posts()


@app.post("/posts")
async def create_post(body: CreatePostIn, request: Request) -> dict[str, Any]:
    if not is_allowed_stream_url(body.stream_url):
        raise HTTPException(
            status_code=422,
            detail="stream_url must be youtube, twitch, or niconico",
        )

    ip = client_ip(request)
    now = now_ts()

    last = LAST_CREATE_AT.get(ip, 0.0)
    if (now - last) < CREATE_MIN_INTERVAL_SEC:
        raise HTTPException(status_code=429, detail="too many create requests")

    active = sum(1 for r in RECORDS.values() if r.creator_ip == ip)
    if active >= MAX_ACTIVE_POSTS_PER_IP:
        raise HTTPException(status_code=429, detail="too many active posts")

    if not body.autopunch:
        await verify_hostable_or_raise(body.addr)

    LAST_CREATE_AT[ip] = now

    post = Post(
        rank=body.rank,
        addr=body.addr,
        comment=body.comment,
        stream_url=body.stream_url,
        giuroll=body.giuroll,
        autopunch=body.autopunch,
        match_status=body.match_status,
        net_status=body.net_status,
        updated_at=now,
    )
    rec = PostRecord(
        post=post,
        owner_token=secrets.token_urlsafe(24),
        creator_ip=ip,
    )
    RECORDS[post.id] = rec

    await HUB.publish("upsert", asdict(post))
    return {"post": asdict(post), "owner_token": rec.owner_token}


@app.post("/posts/update")
async def update_post(body: UpdatePostIn) -> dict[str, Any]:
    if not is_allowed_stream_url(body.stream_url):
        raise HTTPException(
            status_code=422,
            detail="stream_url must be youtube, twitch, or niconico",
        )

    rec = get_record_or_raise(body.id, body.owner_token)
    p = rec.post

    # 再ホスト等でアドレスが変わった場合のみ到達性を再確認する
    if body.addr != p.addr and not body.autopunch:
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

    data = asdict(p)
    await HUB.publish("upsert", data)
    return data


@app.post("/posts/close")
async def close_post(body: ClosePostIn) -> dict[str, Any]:
    _ = get_record_or_raise(body.id, body.owner_token)

    del RECORDS[body.id]
    await HUB.publish("close", {"id": body.id, "reason": body.reason, "ts": now_ts()})
    return {"ok": True, "id": body.id}


@app.post("/posts/upsert")
async def legacy_upsert() -> None:
    raise HTTPException(
        status_code=410,
        detail="this client version is no longer supported; please update asobby",
    )


@app.get("/sse/posts")
async def sse_posts(request: Request):
    q = await HUB.subscribe()

    async def gen():
        try:
            # 購読開始後にスナップショットを送ることで、接続直後の
            # イベント取りこぼしをなくす（重複 upsert は冪等なので無害）。
            yield format_sse("snapshot", sorted_public_posts())

            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=SSE_PING_INTERVAL_SEC)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                    continue
                yield msg
        finally:
            await HUB.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream")


# ----------------------------
# Hostability check
# ----------------------------
def soku_echo_packet(
    should_match: bool = False,
    profile_name: str = "asobby",
) -> bytes:
    profile_name_bytes = str.encode(profile_name, "shift-jis")
    return bytes.fromhex(
        "05"
        "6e7365d9" "ffc46e48" "8d7ca192" "31347295"
        "00000000" "28000000"
        f"{int(should_match):02}"
        f"{len(profile_name_bytes).to_bytes(1, 'big').hex()}"
        f"{profile_name_bytes.hex():0<48}"
        "00000000" "00000000" "00000000" "0000"
    )


def is_valid_reply(data: bytes) -> bool:
    return len(data) >= 1 and data[0] in (0x07, 0x08)


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

    consecutive = 0

    for i in range(attempts):
        reply = probe_host_once(
            host,
            port,
            packet,
            timeout_sec=timeout_sec,
        )

        if reply is not None and is_valid_reply(reply):
            consecutive += 1
        else:
            consecutive = 0

        if consecutive >= needed_consecutive:
            return True

        if i != attempts - 1:
            time.sleep(interval_sec)

    return False


async def verify_hostable_or_raise(addr: str) -> bool:
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
    return True


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
