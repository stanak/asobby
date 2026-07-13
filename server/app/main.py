from __future__ import annotations

import asyncio
import base64
import bisect
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import sqlite3
import struct
import tempfile
import threading
import time
import socket
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, Literal, Optional
from uuid import uuid4
from urllib.parse import urlencode, urlparse

import httpx
import trueskill
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

import db

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
GUEST_PROBE_INTERVAL_SEC = 10
SSE_PING_INTERVAL_SEC = 15

# ランクマッチ: 昇降格判定に必要な最低試合数
RANKED_EVAL_MIN_GAMES = 10
# 1 セッション (ゲスト接続) でランクマ扱いになるのは最初の 3 戦まで
RANKED_SESSION_MAX_GAMES = 3

# 昇降格ルール (rank -> promote_at, demote_at, promote_to, demote_to)
# promote_at / demote_at は None なら該当方向の判定なし
RANK_LADDER: dict[str, dict[str, Any]] = {
    "easy": {"promote_at": 0.5, "demote_at": None, "promote_to": "normal", "demote_to": None},
    "normal": {"promote_at": 0.5, "demote_at": None, "promote_to": "ex", "demote_to": None},
    "ex": {"promote_at": 0.6, "demote_at": 0.2, "promote_to": "hard", "demote_to": "normal"},
    "hard": {"promote_at": 0.6, "demote_at": 0.2, "promote_to": "luna", "demote_to": "ex"},
    "luna": {"promote_at": 0.6, "demote_at": 0.2, "promote_to": "ph", "demote_to": "hard"},
    "ph": {"promote_at": None, "demote_at": None, "promote_to": None, "demote_to": None},
}

# 作成レート制限（IP 単位）
CREATE_MIN_INTERVAL_SEC = 2.0
MAX_ACTIVE_POSTS_PER_IP = 2

# リプレイアップロード上限 (非想天則の .rep は通常 100KB 程度)
REPLAY_MAX_BYTES = 300 * 1024

# 天則観 (tsk) 戦績 DB インポート上限
TSK_IMPORT_MAX_BYTES = 32 * 1024 * 1024
TSK_IMPORT_MAX_ROWS = 200_000
TSK_SQLITE_MAGIC = b"SQLite format 3\x00"
# FILETIME 下限: 2004-01-01 00:00:00 JST 相当
TSK_FILETIME_MIN = int(
    (datetime(2004, 1, 1, 0, 0, 0, tzinfo=timezone.utc).timestamp() + 11644473600)
    * 10_000_000
)

# キャラ名 (client/src/hisoutensoku_memory.py の CHAR_NAME と同一)
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
    20: "Random",
}

JST = timezone(timedelta(hours=9))
_FILENAME_UNSAFE_RE = re.compile(r"[\x00-\x1f\\/:\"*?<>|]")

# ホスト到達性検証 (UDP プローブ) の有効/無効。
# 外向き UDP が一切通らない環境では off にする。
HOSTCHECK_ENABLED = os.environ.get("ASOBBY_HOSTCHECK", "on").lower() not in ("off", "0", "false")

# fly.io では任意ポートへの外向き UDP が遮断されるが、UDP サービスとして
# 公開したポート (fly-global-services にバインド) を送信元にすれば
# 専用 IPv4 経由で返信が戻ってくる。その場合はここで送信元を固定する。
# 例: ASOBBY_PROBE_BIND_HOST=fly-global-services ASOBBY_PROBE_BIND_PORT=10800
PROBE_BIND_HOST = os.environ.get("ASOBBY_PROBE_BIND_HOST", "")
PROBE_BIND_PORT = int(os.environ.get("ASOBBY_PROBE_BIND_PORT", "0"))

# AutoPunch のリレーサーバー (delthas.fr:14763)。テスト時に差し替え可能にする。
AUTOPUNCH_RELAY = os.environ.get("ASOBBY_AUTOPUNCH_RELAY", "delthas.fr:14763")

# ----------------------------
# Discord OAuth (任意ログイン)
# ----------------------------
# クライアント ID/secret が未設定なら認証エンドポイントは 503 を返す。
DISCORD_CLIENT_ID = os.environ.get("ASOBBY_DISCORD_CLIENT_ID", "")
DISCORD_CLIENT_SECRET = os.environ.get("ASOBBY_DISCORD_CLIENT_SECRET", "")
# OAuth リダイレクトに使う公開 URL（Discord 側にも登録が必要）
PUBLIC_BASE_URL = os.environ.get("ASOBBY_BASE_URL", "https://asobby.com").rstrip("/")
# セッショントークンの署名鍵。未設定だと起動ごとにランダム生成され、
# 再起動で全セッションが無効になるので本番では必ず設定する。
SESSION_SECRET = os.environ.get("ASOBBY_SESSION_SECRET", "")
if not SESSION_SECRET:
    SESSION_SECRET = secrets.token_urlsafe(32)
    if DISCORD_CLIENT_ID:
        print("WARNING: ASOBBY_SESSION_SECRET not set; sessions will not survive restarts")

SESSION_TTL_SEC = 30 * 24 * 3600  # 30 日
DEVICE_CODE_TTL_SEC = 600
DEVICE_POLL_INTERVAL_SEC = 2

# ログアウト済みセッショントークン (クッキー削除に加えサーバー側でも失効)
LOGOUT_REVOKED: set[str] = set()

DISCORD_AUTHORIZE_URL = "https://discord.com/oauth2/authorize"
DISCORD_TOKEN_URL = "https://discord.com/api/oauth2/token"
DISCORD_ME_URL = "https://discord.com/api/users/@me"

# 永続化 (PostgreSQL)。未設定なら Discord ログインは無効 (投稿は通常動作)。
DATABASE_URL = os.environ.get("DATABASE_URL", "")

APP_DIR = Path(__file__).parent
STATIC_DIR = APP_DIR / "static"


# ----------------------------
# Models
# ----------------------------
@dataclass
class Post:
    """クライアントに公開してよいフィールドのみを持つ。"""

    id: str = field(default_factory=lambda: uuid4().hex)
    rank: str = "normal"  # ホストの現在システムランク (作成時に設定)
    post_type: str = "casual"  # "casual" | "ranked"
    rating: Optional[float] = None  # ph のみ表示レート
    addr: str = ""
    comment: str = ""
    updated_at: float = 0
    created_at: float = 0
    stream_url: str = ""
    giuroll: bool = False
    autopunch: bool = False
    match_status: str = ""
    net_status: int = 0
    owner_name: str = ""  # Discord ログイン時の表示名（未ログインなら空）
    owner_avatar: str = ""
    guest_name: str = ""  # 対戦中ゲストが Discord ログイン済みなら表示名
    guest_avatar: str = ""
    guest_connected: bool = False  # プローブでゲスト検出中
    ranked_active: bool = False  # 現在のゲストとのセッションがランクマ扱いか


@dataclass
class PostRecord:
    """サーバー内部でのみ保持する情報（owner_token 等）を含むレコード。"""

    post: Post
    owner_token: str
    creator_ip: str
    owner_user_id: str = ""  # 作成者がログイン済みなら Discord ID
    guest_ip: str = ""  # 現在対戦中のゲスト IP（空なら対戦中でない）
    guest_user_id: str = ""  # 同定済みゲストの Discord ID
    guest_rank: str = ""  # 同定済みゲストのランク
    session_games: int = 0  # 現在ゲストとの対戦報告回数


def now_ts() -> float:
    return time.time()


# ----------------------------
# API schemas
# ----------------------------
class CreatePostIn(BaseModel):
    post_type: Literal["casual", "ranked"] = "casual"
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


class ReportResultIn(BaseModel):
    id: str = Field(max_length=64)
    owner_token: str = Field(max_length=128)
    winner: Literal["host", "guest", "draw"]
    host_char: Optional[int] = None
    guest_char: Optional[int] = None
    host_profile: str = Field(default="", max_length=64)
    guest_profile: str = Field(default="", max_length=64)


class GuestReportIn(BaseModel):
    winner: Literal["host", "guest", "draw"]
    host_char: Optional[int] = None
    guest_char: Optional[int] = None
    host_profile: str = Field(default="", max_length=64)
    guest_profile: str = Field(default="", max_length=64)


class DevicePollIn(BaseModel):
    device_code: str = Field(max_length=128)


class ChooseRankIn(BaseModel):
    rank: Literal["easy", "normal", "ex", "hard", "luna"]


# ----------------------------
# Sessions (署名付きトークン; サーバー側の保存なし)
# ----------------------------
def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def make_session_token(user: dict[str, Any], token_version: int) -> str:
    payload = {
        "sub": user["id"],
        "name": user["name"],
        "ver": token_version,  # users.token_version と一致しないトークンは無効
        "exp": int(time.time()) + SESSION_TTL_SEC,
    }
    body = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode())
    sig = _b64url_encode(
        hmac.new(SESSION_SECRET.encode(), body.encode(), hashlib.sha256).digest()
    )
    return f"{body}.{sig}"


def verify_session_token(token: str) -> Optional[dict[str, Any]]:
    """署名と期限が有効なら {"sub", "name", "ver"} を返す。無効なら None。"""
    if not token or token in LOGOUT_REVOKED:
        return None
    try:
        body, sig = token.rsplit(".", 1)
        expected = _b64url_encode(
            hmac.new(SESSION_SECRET.encode(), body.encode(), hashlib.sha256).digest()
        )
        if not hmac.compare_digest(sig, expected):
            return None
        payload = json.loads(_b64url_decode(body))
        if payload.get("exp", 0) < time.time():
            return None
        return {
            "sub": str(payload["sub"]),
            "name": str(payload["name"]),
            "ver": int(payload.get("ver", 0)),
        }
    except Exception:
        return None


def session_from_request(request: Request) -> Optional[dict[str, Any]]:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return verify_session_token(auth[7:].strip())
    cookie = request.cookies.get("asobby_session")
    if cookie:
        return verify_session_token(cookie)
    return None


def discord_avatar_url(user_id: str, avatar_hash: str) -> str:
    if not avatar_hash:
        return ""
    return f"https://cdn.discordapp.com/avatars/{user_id}/{avatar_hash}.png?size=64"


def display_rating(mu: float, sigma: float) -> float:
    """TrueSkill 表示レート = mu - 3 * sigma"""
    return round(mu - 3 * sigma, 1)


async def resolve_session(request: Request) -> Optional[dict[str, Any]]:
    """Bearer トークンを検証し、DB の token_version と突合する。
    有効なら {"id", "name", "avatar", "rank", "rating"} を返し、last_seen / last_ip も最新化する。"""
    sess = session_from_request(request)
    if sess is None or not db.is_configured():
        return None
    user = await db.get_user_if_token_valid(sess["sub"], sess["ver"])
    if user is None:
        return None
    await db.touch_user(user.id, client_ip(request))
    rating = display_rating(user.ts_mu, user.ts_sigma) if user.rank == "ph" else None
    return {
        "id": user.id,
        "name": user.name,
        "avatar": discord_avatar_url(user.id, user.avatar),
        "rank": user.rank,
        "rating": rating,
        "can_choose_rank": not user.rank_locked,
    }


# ----------------------------
# Device-code login flow
# ----------------------------
@dataclass
class DeviceLogin:
    device_code: str  # クライアントがポーリングに使う秘密
    web_code: str     # ブラウザ URL / OAuth state に使う値
    created_at: float
    session_token: str = ""
    user: Optional[dict[str, Any]] = None


DEVICE_LOGINS: Dict[str, DeviceLogin] = {}  # web_code -> DeviceLogin

# Web ブラウザ直接ログイン (state -> created_at)
WEB_LOGINS: Dict[str, float] = {}


def cleanup_device_logins() -> None:
    now = time.time()
    for key in [
        k for k, v in DEVICE_LOGINS.items()
        if (now - v.created_at) > DEVICE_CODE_TTL_SEC
    ]:
        DEVICE_LOGINS.pop(key, None)


def cleanup_web_logins() -> None:
    now = time.time()
    for key in [
        k for k, ts in WEB_LOGINS.items()
        if (now - ts) > DEVICE_CODE_TTL_SEC
    ]:
        WEB_LOGINS.pop(key, None)


def require_discord_configured() -> None:
    if not DISCORD_CLIENT_ID or not DISCORD_CLIENT_SECRET:
        raise HTTPException(status_code=503, detail="discord login is not configured")
    if not db.is_configured():
        raise HTTPException(status_code=503, detail="database is not configured")


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


async def guest_probe_loop() -> None:
    """各募集ホストへ soku echo を送り、対戦中ゲスト IP を定期的に取得する。"""
    if not HOSTCHECK_ENABLED:
        return

    while True:
        try:
            for rec in list(RECORDS.values()):
                post = rec.post
                if RECORDS.get(post.id) is not rec:
                    continue
                try:
                    host, port_s = post.addr.rsplit(":", 1)
                    port = int(port_s)
                    if not (0 < port < 65536):
                        continue
                    socket.inet_aton(host)
                except (ValueError, OSError):
                    continue

                reply = await asyncio.to_thread(
                    probe_post_status, host, port, post.autopunch
                )
                await apply_guest_probe(rec, reply)
        except Exception as e:
            print(f"guest_probe_loop error: {e}")

        await asyncio.sleep(GUEST_PROBE_INTERVAL_SEC)


def run_migrations() -> None:
    """Alembic マイグレーションを head まで適用する (起動時に別スレッドで実行)。"""
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(APP_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(APP_DIR / "migrations"))
    cfg.set_main_option("sqlalchemy.url", DATABASE_URL)
    command.upgrade(cfg, "head")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if DATABASE_URL:
        await asyncio.to_thread(run_migrations)
        db.init_engine(DATABASE_URL)
    cleanup_task = asyncio.create_task(cleanup_loop())
    guest_probe_task = asyncio.create_task(guest_probe_loop())
    try:
        yield
    finally:
        cleanup_task.cancel()
        guest_probe_task.cancel()
        if db.is_configured():
            await db.dispose()


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
    records = sorted(
        RECORDS.values(),
        key=lambda r: (-r.post.created_at, r.post.id),
    )
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
@app.get("/stats")
async def stats_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "stats.html")


def _match_is_win(match: db.Match, user_id: str) -> bool:
    if match.host_user_id == user_id:
        return match.winner == "host"
    return match.winner == "guest"


async def evaluate_rank(user_id: str) -> Optional[str]:
    """現ランクでの直近ランクマ勝率に基づき昇降格する。変更があれば新ランクを返す。"""
    rank_info = await db.get_user_rank(user_id)
    if rank_info is None:
        return None
    rank, _mu, _sigma = rank_info
    if rank == "ph":
        return None

    rules = RANK_LADDER.get(rank)
    if rules is None:
        return None

    matches = await db.fetch_ranked_matches_at_current_rank(user_id, limit=50)
    games = len(matches)
    if games < RANKED_EVAL_MIN_GAMES:
        return None

    wins = sum(1 for m in matches if _match_is_win(m, user_id))
    win_rate = wins / games

    new_rank: Optional[str] = None
    demote_at = rules.get("demote_at")
    if demote_at is not None and win_rate < demote_at:
        new_rank = rules.get("demote_to")
    else:
        promote_at = rules.get("promote_at")
        if promote_at is not None and win_rate >= promote_at:
            new_rank = rules.get("promote_to")

    if new_rank and new_rank != rank:
        await db.set_user_rank(user_id, new_rank)
        print(f"rank change: user={user_id} {rank} -> {new_rank} (win_rate={win_rate:.3f}, games={games})")
        return new_rank
    return None


async def update_trueskill_ratings(
    host_user_id: str,
    guest_user_id: str,
    winner: str,
) -> None:
    """ph 同士のランクマ対戦確定時に TrueSkill レートを更新する。"""
    host_info = await db.get_user_rank(host_user_id)
    guest_info = await db.get_user_rank(guest_user_id)
    if host_info is None or guest_info is None:
        return
    host_rank, host_mu, host_sigma = host_info
    guest_rank, guest_mu, guest_sigma = guest_info
    if host_rank != "ph" or guest_rank != "ph":
        return

    host_rating = trueskill.Rating(mu=host_mu, sigma=host_sigma)
    guest_rating = trueskill.Rating(mu=guest_mu, sigma=guest_sigma)

    if winner == "draw":
        new_host, new_guest = trueskill.rate_1vs1(host_rating, guest_rating, drawn=True)
    elif winner == "host":
        new_host, new_guest = trueskill.rate_1vs1(host_rating, guest_rating)
    else:
        new_guest, new_host = trueskill.rate_1vs1(guest_rating, host_rating)

    await db.set_user_rating(host_user_id, new_host.mu, new_host.sigma)
    await db.set_user_rating(guest_user_id, new_guest.mu, new_guest.sigma)


def compute_ranked_stats(matches: list[db.Match], user_id: str) -> dict[str, Any]:
    total = _bucket_stats(matches, user_id)
    recent50 = _bucket_stats(matches[:50], user_id)
    return {
        "total": total,
        "recent50": {
            "games": recent50["games"],
            "wins": recent50["wins"],
            "win_rate": recent50["win_rate"],
        },
    }


async def host_rank_for_post(owner_user_id: str) -> tuple[str, Optional[float]]:
    """募集作成時に Post.rank / Post.rating を決める。"""
    rank_info = await db.get_user_rank(owner_user_id)
    if rank_info is None:
        return "normal", None
    rank, mu, sigma = rank_info
    rating = display_rating(mu, sigma) if rank == "ph" else None
    return rank, rating


def _match_user_view(
    match: db.Match, user_id: str
) -> tuple[Optional[int], Optional[int], str, bool, bool, bool]:
    """視点変換: (自キャラ, 相手キャラ, 相手プロファイル, 勝ち, 負け, 分)"""
    if match.host_user_id == user_id:
        return (
            match.host_char,
            match.guest_char,
            match.guest_profile or "",
            match.winner == "host",
            match.winner == "guest",
            match.winner == "draw",
        )
    return (
        match.guest_char,
        match.host_char,
        match.host_profile or "",
        match.winner == "guest",
        match.winner == "host",
        match.winner == "draw",
    )


def _bucket_stats(matches: list[db.Match], user_id: str) -> dict[str, Any]:
    games = len(matches)
    wins = losses = draws = 0
    for m in matches:
        _, _, _, is_win, is_loss, is_draw = _match_user_view(m, user_id)
        if is_win:
            wins += 1
        elif is_loss:
            losses += 1
        elif is_draw:
            draws += 1
    win_rate = round(wins / games, 4) if games else 0.0
    return {
        "games": games,
        "wins": wins,
        "losses": losses,
        "draws": draws,
        "win_rate": win_rate,
    }


def _group_by_char(
    matches: list[db.Match], user_id: str, *, mine: bool
) -> list[dict[str, Any]]:
    buckets: dict[int, dict[str, int]] = {}
    for m in matches:
        my_char, opp_char, _, is_win, is_loss, is_draw = _match_user_view(m, user_id)
        char = my_char if mine else opp_char
        if char is None:
            continue
        b = buckets.setdefault(char, {"games": 0, "wins": 0, "losses": 0, "draws": 0})
        b["games"] += 1
        if is_win:
            b["wins"] += 1
        elif is_loss:
            b["losses"] += 1
        elif is_draw:
            b["draws"] += 1
    out = []
    for char, b in buckets.items():
        out.append({
            "char": char,
            "games": b["games"],
            "wins": b["wins"],
            "losses": b["losses"],
            "draws": b["draws"],
            "win_rate": round(b["wins"] / b["games"], 4) if b["games"] else 0.0,
        })
    out.sort(key=lambda x: x["games"], reverse=True)
    return out


def _group_by_profile(
    matches: list[db.Match], user_id: str
) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, int]] = {}
    for m in matches:
        _, _, opp_profile, is_win, is_loss, is_draw = _match_user_view(m, user_id)
        if not opp_profile:
            continue
        b = buckets.setdefault(opp_profile, {"games": 0, "wins": 0, "losses": 0, "draws": 0})
        b["games"] += 1
        if is_win:
            b["wins"] += 1
        elif is_loss:
            b["losses"] += 1
        elif is_draw:
            b["draws"] += 1
    out = []
    for profile, b in buckets.items():
        out.append({
            "profile": profile,
            "games": b["games"],
            "wins": b["wins"],
            "losses": b["losses"],
            "draws": b["draws"],
            "win_rate": round(b["wins"] / b["games"], 4) if b["games"] else 0.0,
        })
    out.sort(key=lambda x: x["games"], reverse=True)
    return out


def compute_user_stats(matches: list[db.Match], user_id: str) -> dict[str, Any]:
    total = _bucket_stats(matches, user_id)
    recent: dict[str, dict[str, Any]] = {}
    for n in (30, 50, 100):
        subset = matches[:n]
        g = len(subset)
        w = sum(1 for m in subset if _match_user_view(m, user_id)[3])
        recent[str(n)] = {
            "games": g,
            "wins": w,
            "win_rate": round(w / g, 4) if g else 0.0,
        }
    return {
        "total": total,
        "recent": recent,
        "by_my_char": _group_by_char(matches, user_id, mine=True),
        "by_opp_char": _group_by_char(matches, user_id, mine=False),
        "by_opp_profile": _group_by_profile(matches, user_id),
    }


def _filetime_to_played_at(ft: int) -> datetime:
    """天則観 FILETIME (JST 壁時計) を UTC の played_at に変換する。"""
    unix_like = ft / 10_000_000 - 11644473600
    jst_dt = datetime.fromtimestamp(unix_like, tz=timezone.utc).replace(tzinfo=JST)
    return jst_dt.astimezone(timezone.utc)


def _decode_tsk_name(raw: bytes | str | None) -> str:
    """天則観の CP932 プロファイル名をデコードする。"""
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw[:64]
    try:
        return raw.decode("cp932", errors="replace")[:64]
    except Exception:
        return ""


def _tsk_match_id(user_id: str, ft: int) -> str:
    return hashlib.md5(f"tsk:{user_id}:{ft}".encode()).hexdigest()


def _dt_ts(dt: datetime) -> float:
    """datetime を UTC タイムスタンプに正規化する (naive は UTC 扱い)。"""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc).timestamp()
    return dt.timestamp()


def _is_near_existing(
    played_at: datetime,
    existing_ts: list[float],
    *,
    window_sec: int = 60,
) -> bool:
    """existing_ts (昇順) に ±window_sec 以内の時刻があるか bisect で判定する。"""
    if not existing_ts:
        return False
    ts = _dt_ts(played_at)
    lo = bisect.bisect_left(existing_ts, ts - window_sec)
    hi = bisect.bisect_right(existing_ts, ts + window_sec)
    for i in range(lo, hi):
        if abs(existing_ts[i] - ts) <= window_sec:
            return True
    return False


def _parse_tsk_db(
    data: bytes,
    user_id: str,
    existing_ids: set[str],
    existing_ts: list[float],
    now_ft: int,
) -> tuple[list[dict[str, Any]], int, int, int]:
    """天則観 SQLite を解析し、(insert 行, skipped_duplicate, skipped_invalid, total) を返す。"""
    skipped_duplicate = 0
    skipped_invalid = 0
    total = 0
    to_insert: list[dict[str, Any]] = []
    seen_ids: set[str] = set(existing_ids)

    tmp_path: Optional[str] = None
    conn: Optional[sqlite3.Connection] = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            tmp.write(data)
            tmp_path = tmp.name

        conn = sqlite3.connect(f"file:{tmp_path}?mode=ro", uri=True)
        conn.text_factory = bytes
        cur = conn.cursor()

        try:
            cur.execute("SELECT COUNT(*) FROM trackrecord123")
            total = int(cur.fetchone()[0])
        except sqlite3.Error as e:
            raise HTTPException(status_code=422, detail=f"invalid tsk database: {e}") from e

        if total > TSK_IMPORT_MAX_ROWS:
            raise HTTPException(status_code=413, detail="too many rows in tsk database")

        cur.execute(
            "SELECT timestamp, p1name, p1id, p1win, p2name, p2id, p2win "
            "FROM trackrecord123"
        )
        for row in cur.fetchall():
            ft, p1name, p1id, p1win, p2name, p2id, p2win = row

            if not isinstance(ft, int):
                skipped_invalid += 1
                continue
            if ft < TSK_FILETIME_MIN or ft > now_ft:
                skipped_invalid += 1
                continue

            try:
                p1id = int(p1id)
                p2id = int(p2id)
                p1win = int(p1win)
                p2win = int(p2win)
            except (TypeError, ValueError):
                skipped_invalid += 1
                continue

            if not (0 <= p1win <= 2 and 0 <= p2win <= 2):
                skipped_invalid += 1
                continue
            if p1win != 2 and p2win != 2:
                skipped_invalid += 1
                continue
            if not (0 <= p1id <= 19 and 0 <= p2id <= 19):
                skipped_invalid += 1
                continue

            match_id = _tsk_match_id(user_id, ft)
            if match_id in seen_ids:
                skipped_duplicate += 1
                continue

            played_at = _filetime_to_played_at(ft)
            if _is_near_existing(played_at, existing_ts):
                skipped_duplicate += 1
                continue

            winner = "host" if p1win == 2 else "guest"
            to_insert.append({
                "id": match_id,
                "host_user_id": user_id,
                "guest_user_id": None,
                "host_ip": "",
                "guest_ip": "",
                "winner": winner,
                "host_char": p1id,
                "guest_char": p2id,
                "host_profile": _decode_tsk_name(p1name),
                "guest_profile": _decode_tsk_name(p2name),
                "ranked": False,
                "source": "import",
                "played_at": played_at,
            })
            seen_ids.add(match_id)
            bisect.insort(existing_ts, _dt_ts(played_at))

        return to_insert, skipped_duplicate, skipped_invalid, total
    finally:
        if conn is not None:
            conn.close()
        if tmp_path is not None:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


@app.post("/import/tensokukan")
async def import_tensokukan(request: Request) -> dict[str, Any]:
    """天則観 (tsk) の SQLite 戦績 DB をインポートする。"""
    sess = await resolve_session(request)
    if sess is None:
        raise HTTPException(status_code=401, detail="invalid or expired session")
    if not db.is_configured():
        return {"ok": True, "imported": 0}

    data = await request.body()
    if not data:
        raise HTTPException(status_code=422, detail="empty body")
    if len(data) > TSK_IMPORT_MAX_BYTES:
        raise HTTPException(status_code=413, detail="tsk database too large")
    if not data.startswith(TSK_SQLITE_MAGIC):
        raise HTTPException(status_code=422, detail="not a sqlite database")

    user_id = sess["id"]
    # 現在+1日を FILETIME 上限にする (JST 壁時計)
    now_jst = datetime.now(JST)
    now_unix_like = datetime(
        now_jst.year, now_jst.month, now_jst.day,
        now_jst.hour, now_jst.minute, now_jst.second,
        tzinfo=timezone.utc,
    ).timestamp() + 86400
    now_ft = int((now_unix_like + 11644473600) * 10_000_000)

    existing_times = await db.fetch_user_match_times(user_id, exclude_source="import")
    existing_ts = [_dt_ts(t) for t in existing_times]

    # 解析は CPU/IO を食うのでワーカースレッドで行う (イベントループを塞がない)
    parsed, dup1, inv, total = await asyncio.to_thread(
        _parse_tsk_db, data, user_id, set(), list(existing_ts), now_ft
    )
    skipped_duplicate = dup1
    skipped_invalid = inv

    if parsed:
        candidate_ids = [r["id"] for r in parsed]
        existing_ids = await db.filter_existing_match_ids(candidate_ids)
        if existing_ids:
            filtered: list[dict[str, Any]] = []
            for row in parsed:
                if row["id"] in existing_ids:
                    skipped_duplicate += 1
                else:
                    filtered.append(row)
            parsed = filtered

    imported = await db.bulk_insert_matches(parsed)
    return {
        "ok": True,
        "imported": imported,
        "skipped_duplicate": skipped_duplicate,
        "skipped_invalid": skipped_invalid,
        "total": total,
    }


@app.get("/stats/me")
async def stats_me(request: Request) -> dict[str, Any]:
    sess = await resolve_session(request)
    if sess is None:
        raise HTTPException(status_code=401, detail="invalid or expired session")
    matches = await db.fetch_user_matches(sess["id"])
    stats = compute_user_stats(matches, sess["id"])
    ranked_matches = await db.fetch_user_ranked_matches(sess["id"])
    ranked_stats = compute_ranked_stats(ranked_matches, sess["id"])
    return {
        "user": sess,
        **stats,
        "ranked": {
            "rank": sess["rank"],
            "rating": sess["rating"],
            **ranked_stats,
        },
    }


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/myip")
async def get_myip(request: Request) -> dict[str, str]:
    return {"ip": client_ip(request)}


# ----------------------------
# Auth routes
# ----------------------------
@app.post("/auth/device")
async def auth_device_start() -> dict[str, Any]:
    """クライアントがログインを開始する。verify_url をブラウザで開かせ、
    device_code で /auth/device/poll をポーリングする。"""
    require_discord_configured()
    cleanup_device_logins()

    login = DeviceLogin(
        device_code=secrets.token_urlsafe(32),
        web_code=secrets.token_urlsafe(24),
        created_at=time.time(),
    )
    DEVICE_LOGINS[login.web_code] = login
    return {
        "device_code": login.device_code,
        "verify_url": f"{PUBLIC_BASE_URL}/auth/discord/start?code={login.web_code}",
        "expires_in": DEVICE_CODE_TTL_SEC,
        "interval": DEVICE_POLL_INTERVAL_SEC,
    }


@app.get("/auth/discord/start")
async def auth_discord_start(code: str) -> RedirectResponse:
    require_discord_configured()
    cleanup_device_logins()
    cleanup_web_logins()
    if code not in DEVICE_LOGINS:
        raise HTTPException(status_code=404, detail="login request expired")

    params = urlencode({
        "client_id": DISCORD_CLIENT_ID,
        "redirect_uri": f"{PUBLIC_BASE_URL}/auth/discord/callback",
        "response_type": "code",
        "scope": "identify",
        "state": code,
        "prompt": "none",
    })
    return RedirectResponse(f"{DISCORD_AUTHORIZE_URL}?{params}", status_code=302)


@app.get("/auth/discord/web")
async def auth_discord_web() -> RedirectResponse:
    """Web ページ閲覧用の Discord ログイン。完了後はクッキーセッションを発行する。"""
    require_discord_configured()
    cleanup_web_logins()
    state = "w-" + secrets.token_urlsafe(24)
    WEB_LOGINS[state] = time.time()
    params = urlencode({
        "client_id": DISCORD_CLIENT_ID,
        "redirect_uri": f"{PUBLIC_BASE_URL}/auth/discord/callback",
        "response_type": "code",
        "scope": "identify",
        "state": state,
        "prompt": "none",
    })
    return RedirectResponse(f"{DISCORD_AUTHORIZE_URL}?{params}", status_code=302)


@app.get("/auth/discord/callback", response_model=None)
async def auth_discord_callback(
    request: Request,
    state: str,
    code: str = "",
    error: str = "",
) -> Response:
    require_discord_configured()
    cleanup_device_logins()
    cleanup_web_logins()

    is_web = state in WEB_LOGINS
    login = DEVICE_LOGINS.get(state)
    if not is_web and login is None:
        raise HTTPException(status_code=404, detail="login request expired")
    if error or not code:
        WEB_LOGINS.pop(state, None)
        DEVICE_LOGINS.pop(state, None)
        return _login_result_page(ok=False, message="ログインがキャンセルされました。")

    async with httpx.AsyncClient(timeout=10.0) as http:
        token_res = await http.post(
            DISCORD_TOKEN_URL,
            data={
                "client_id": DISCORD_CLIENT_ID,
                "client_secret": DISCORD_CLIENT_SECRET,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": f"{PUBLIC_BASE_URL}/auth/discord/callback",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if token_res.status_code != 200:
            if is_web:
                WEB_LOGINS.pop(state, None)
            return _login_result_page(ok=False, message="Discord との連携に失敗しました。")
        access_token = token_res.json().get("access_token", "")

        me_res = await http.get(
            DISCORD_ME_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if me_res.status_code != 200:
            if is_web:
                WEB_LOGINS.pop(state, None)
            return _login_result_page(ok=False, message="Discord ユーザー情報の取得に失敗しました。")
        me = me_res.json()

    user = {
        "id": str(me.get("id", "")),
        "name": str(me.get("global_name") or me.get("username") or ""),
    }
    avatar = str(me.get("avatar") or "")
    if not user["id"] or not user["name"]:
        if is_web:
            WEB_LOGINS.pop(state, None)
        return _login_result_page(ok=False, message="Discord ユーザー情報が不正です。")

    if is_web:
        WEB_LOGINS.pop(state, None)
        user_row = await db.upsert_user_on_login(
            user["id"], user["name"], ip=client_ip(request), avatar=avatar
        )
        token = make_session_token(user, user_row.token_version)
        response = RedirectResponse("/", status_code=302)
        response.set_cookie(
            key="asobby_session",
            value=token,
            httponly=True,
            secure=True,
            samesite="lax",
            max_age=2592000,
            path="/",
        )
        return response

    # デバイスコードフロー: users テーブルに upsert。IP はブラウザ経由の
    # 可能性があるため、クライアント本体からのポーリング時に更新する。
    user_row = await db.upsert_user_on_login(
        user["id"], user["name"], ip="", avatar=avatar
    )

    login.user = user
    login.session_token = make_session_token(user, user_row.token_version)
    return _login_result_page(ok=True, message=f"{user['name']} としてログインしました。アプリに戻ってください。")


@app.get("/auth/logout")
async def auth_logout(request: Request) -> RedirectResponse:
    token = request.cookies.get("asobby_session")
    if token:
        LOGOUT_REVOKED.add(token)
    response = RedirectResponse("/", status_code=302)
    # ログイン時と同じ属性で上書き削除する (Secure 不一致だとブラウザが消せない)
    response.set_cookie(
        key="asobby_session",
        value="",
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=0,
        path="/",
    )
    return response


def _login_result_page(*, ok: bool, message: str) -> HTMLResponse:
    color = "#57c07d" if ok else "#e06c75"
    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="ja"><head><meta charset="utf-8"><title>asobby</title></head>
<body style="background:#14171c;color:#d8dee9;font-family:sans-serif;
display:flex;align-items:center;justify-content:center;height:100vh;margin:0">
<div style="text-align:center">
<h1 style="color:{color}">{"ログイン完了" if ok else "ログイン失敗"}</h1>
<p>{message}</p>
<p style="color:#7b8794">このタブは閉じて構いません。</p>
</div></body></html>""")


@app.post("/auth/device/poll")
async def auth_device_poll(body: DevicePollIn, request: Request) -> dict[str, Any]:
    cleanup_device_logins()
    for web_code, login in list(DEVICE_LOGINS.items()):
        if secrets.compare_digest(login.device_code, body.device_code):
            if not login.session_token:
                return {"status": "pending"}
            DEVICE_LOGINS.pop(web_code, None)  # ワンショット
            # ポーリング元 = クライアント本体なので、この IP を記録する
            if login.user:
                await db.touch_user(login.user["id"], client_ip(request))
            return {
                "status": "ok",
                "session_token": login.session_token,
                "user": login.user,
            }
    raise HTTPException(status_code=404, detail="login request expired")


@app.get("/auth/me")
async def auth_me(request: Request) -> dict[str, Any]:
    """セッション検証。クライアントは起動時に呼び、その都度 IP が最新化される。"""
    sess = await resolve_session(request)
    if sess is None:
        raise HTTPException(status_code=401, detail="invalid or expired session")
    return sess


@app.post("/rank/initial")
async def choose_initial_rank(body: ChooseRankIn, request: Request) -> dict[str, Any]:
    """初回のみ開始ランクを選択する。"""
    sess = await resolve_session(request)
    if sess is None:
        raise HTTPException(status_code=401, detail="invalid or expired session")
    if not await db.choose_initial_rank(sess["id"], body.rank):
        raise HTTPException(status_code=409, detail="rank already locked")
    return {"ok": True, "rank": body.rank}


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

    parse_ipv4_addr_or_raise(body.addr)

    sess = await resolve_session(request)
    if sess is None:
        raise HTTPException(status_code=401, detail="discord login required")

    owner_name = sess["name"]
    owner_avatar = sess["avatar"]
    owner_user_id = sess["id"]

    ip = client_ip(request)
    now = now_ts()

    last = LAST_CREATE_AT.get(ip, 0.0)
    if (now - last) < CREATE_MIN_INTERVAL_SEC:
        raise HTTPException(status_code=429, detail="too many create requests")

    active = sum(1 for r in RECORDS.values() if r.creator_ip == ip)
    if active >= MAX_ACTIVE_POSTS_PER_IP:
        raise HTTPException(status_code=429, detail="too many active posts")

    await verify_hostable_or_raise(body.addr, autopunch=body.autopunch)

    LAST_CREATE_AT[ip] = now

    host_rank, host_rating = await host_rank_for_post(owner_user_id)

    post = Post(
        rank=host_rank,
        post_type=body.post_type,
        rating=host_rating,
        addr=body.addr,
        comment=body.comment,
        stream_url=body.stream_url,
        giuroll=body.giuroll,
        autopunch=body.autopunch,
        match_status=body.match_status,
        net_status=body.net_status,
        owner_name=owner_name,
        owner_avatar=owner_avatar,
        updated_at=now,
        created_at=now,
    )
    rec = PostRecord(
        post=post,
        owner_token=secrets.token_urlsafe(24),
        creator_ip=ip,
        owner_user_id=owner_user_id,
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

    parse_ipv4_addr_or_raise(body.addr)

    rec = get_record_or_raise(body.id, body.owner_token)
    p = rec.post

    # 再ホスト等でアドレスが変わった場合のみ到達性を再確認する
    if body.addr != p.addr:
        await verify_hostable_or_raise(body.addr, autopunch=body.autopunch)

    p.post_type = body.post_type
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


def _sanitize_profile_for_filename(profile: str) -> str:
    """ファイル名に使うプロファイル名をサニタイズする。"""
    s = _FILENAME_UNSAFE_RE.sub("_", profile or "").strip()
    if not s:
        return "unknown"
    return s[:32]


def _char_label(char_id: Optional[int]) -> str:
    if char_id is None:
        return "Unknown"
    return CHAR_NAME.get(char_id, "Unknown")


def build_replay_filename(match: db.Match) -> str:
    """リプレイファイル名を生成する。"""
    played = match.played_at or db.utcnow()
    ts = played.astimezone(JST).strftime("%Y%m%d%H%M%S")
    host_char = _char_label(match.host_char)
    guest_char = _char_label(match.guest_char)
    host_profile = _sanitize_profile_for_filename(match.host_profile)
    guest_profile = _sanitize_profile_for_filename(match.guest_profile)
    if match.winner == "host":
        result = "ox"
    elif match.winner == "guest":
        result = "xo"
    else:
        result = "xx"
    return (
        f"{ts}_{host_profile}-{host_char}_vs_"
        f"{guest_profile}-{guest_char}_{result}.rep"
    )


@app.post("/replays/upload")
async def upload_replay(request: Request) -> dict[str, Any]:
    """ログインユーザーの直近対戦リプレイを受け取る。"""
    sess = await resolve_session(request)
    if sess is None:
        raise HTTPException(status_code=401, detail="invalid or expired session")

    data = await request.body()
    if not data:
        raise HTTPException(status_code=422, detail="empty body")
    if len(data) > REPLAY_MAX_BYTES:
        raise HTTPException(status_code=413, detail="replay too large")

    match = await db.find_recent_match_for_user(sess["id"])
    if match is None:
        recent = await db.find_recent_match_for_user(
            sess["id"], require_no_replay=False
        )
        if recent is not None and await db.replay_count_for_match(recent.id) > 0:
            return {"ok": True, "stored": False, "reason": "duplicate"}
        return {"ok": True, "stored": False, "reason": "no_match"}

    filename = build_replay_filename(match)
    stored = await db.insert_replay(match.id, filename, data)
    if not stored:
        return {"ok": True, "stored": False, "reason": "duplicate"}
    return {"ok": True, "stored": True, "filename": filename}


@app.post("/matches/report")
async def report_guest_match(body: GuestReportIn, request: Request) -> dict[str, Any]:
    """ログイン済みゲストが自分のクライアントから対戦結果を補完報告する。"""
    sess = await resolve_session(request)
    if sess is None:
        raise HTTPException(status_code=401, detail="invalid or expired session")
    if not db.is_configured():
        return {"ok": True, "recorded": False}

    uid = sess["id"]
    if await db.find_recent_match_as_guest(uid) is not None:
        return {"ok": True, "recorded": False, "reason": "duplicate"}

    await db.insert_match_result(
        host_user_id=None,
        guest_user_id=uid,
        host_ip="",
        guest_ip=client_ip(request),
        winner=body.winner,
        host_char=body.host_char,
        guest_char=body.guest_char,
        host_profile=body.host_profile,
        guest_profile=body.guest_profile,
        ranked=False,
        source="guest",
    )
    return {"ok": True, "recorded": True}


@app.post("/posts/result")
async def report_result(body: ReportResultIn) -> dict[str, Any]:
    rec = get_record_or_raise(body.id, body.owner_token)
    if not rec.guest_ip or not db.is_configured():
        return {"ok": True, "recorded": False}

    post = rec.post
    is_ranked = post.ranked_active and rec.session_games < RANKED_SESSION_MAX_GAMES
    host_ip, _, _ = post.addr.partition(":")

    if rec.guest_user_id:
        guest_match = await db.find_recent_guest_reported_match(rec.guest_user_id)
        if guest_match is not None:
            await db.promote_guest_match(
                guest_match.id,
                host_user_id=rec.owner_user_id,
                host_ip=host_ip,
                winner=body.winner,
                host_char=body.host_char,
                guest_char=body.guest_char,
                host_profile=body.host_profile,
                guest_profile=body.guest_profile,
                ranked=is_ranked,
            )
        else:
            await db.insert_match_result(
                host_user_id=rec.owner_user_id,
                guest_user_id=rec.guest_user_id,
                host_ip=host_ip,
                guest_ip=rec.guest_ip,
                winner=body.winner,
                host_char=body.host_char,
                guest_char=body.guest_char,
                host_profile=body.host_profile,
                guest_profile=body.guest_profile,
                ranked=is_ranked,
                source="host",
            )
    else:
        await db.insert_match_result(
            host_user_id=rec.owner_user_id,
            guest_user_id=None,
            host_ip=host_ip,
            guest_ip=rec.guest_ip,
            winner=body.winner,
            host_char=body.host_char,
            guest_char=body.guest_char,
            host_profile=body.host_profile,
            guest_profile=body.guest_profile,
            ranked=is_ranked,
            source="host",
        )
    rec.session_games += 1

    if is_ranked:
        if rec.owner_user_id:
            await db.lock_user_rank(rec.owner_user_id)
        if rec.guest_user_id:
            await db.lock_user_rank(rec.guest_user_id)
        if rec.owner_user_id:
            await evaluate_rank(rec.owner_user_id)
        if rec.guest_user_id:
            await evaluate_rank(rec.guest_user_id)
        if rec.guest_user_id:
            await update_trueskill_ratings(
                rec.owner_user_id,
                rec.guest_user_id,
                body.winner,
            )

    return {"ok": True, "recorded": True, "ranked": is_ranked}


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


def parse_matched_client(data: bytes) -> Optional[tuple[str, int]]:
    """0x08 応答から接続中クライアントの (ip, port) を取り出す。"""
    if len(data) < 13 or data[0] != 0x08:
        return None
    port = int.from_bytes(data[7:9], "big")
    ip = str(ipaddress.IPv4Address(data[9:13]))
    return ip, port


def _autopunch_nat_port(host: str, port: int) -> Optional[int]:
    """AutoPunch リレー Stage 2 と同じ手順で NAT ポートを引く。
    呼び出し側が _probe_lock を保持していること。"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        bind_addr = _probe_bind_addr()
        if bind_addr:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((bind_addr, PROBE_BIND_PORT))
        else:
            sock.bind(("0.0.0.0", 0))

        my_port = sock.getsockname()[1]

        relay_host, relay_port_s = AUTOPUNCH_RELAY.rsplit(":", 1)
        try:
            relay_port = int(relay_port_s)
            if not (0 < relay_port < 65536):
                raise ValueError
            relay_ip = socket.gethostbyname(relay_host)
        except (OSError, ValueError):
            return None

        relay_addr = (relay_ip, relay_port)
        lookup = struct.pack("!H", my_port) + socket.inet_aton(host) + struct.pack("!H", port)
        nat_port: Optional[int] = None
        for _ in range(3):
            try:
                sock.sendto(lookup, relay_addr)
                deadline = time.monotonic() + 1.0
                while time.monotonic() < deadline:
                    remaining = deadline - time.monotonic()
                    sock.settimeout(remaining)
                    data, addr = sock.recvfrom(4096)
                    if addr[0] != relay_ip or len(data) != 8:
                        continue
                    candidate_nat_port = struct.unpack("!H", data[2:4])[0]
                    ip = socket.inet_ntoa(data[4:8])
                    if ip == host:
                        nat_port = candidate_nat_port
                        break
                if nat_port is not None:
                    break
            except OSError:
                pass

        return nat_port
    except OSError:
        return None
    finally:
        sock.close()


def probe_post_status(host: str, port: int, autopunch: bool) -> Optional[bytes]:
    """soku echo でホスト状態を 1 回プローブする（スレッド内で呼ぶ）。"""
    with _probe_lock:
        probe_port = port
        if autopunch:
            nat_port = _autopunch_nat_port(host, port)
            if nat_port is None:
                return None
            probe_port = nat_port

        return probe_host_once(
            host,
            probe_port,
            soku_echo_packet(),
            timeout_sec=0.5,
        )


async def apply_guest_probe(rec: PostRecord, reply: Optional[bytes]) -> None:
    """プローブ応答を PostRecord に反映し、必要なら SSE を更新する。"""
    if reply is None:
        return

    post = rec.post
    matched = parse_matched_client(reply)

    if matched is None:
        if len(reply) >= 1 and reply[0] == 0x07 and rec.guest_ip:
            rec.guest_ip = ""
            rec.guest_user_id = ""
            rec.guest_rank = ""
            rec.session_games = 0
            post.guest_name = ""
            post.guest_avatar = ""
            post.guest_connected = False
            post.ranked_active = False
            await HUB.publish("upsert", asdict(post))
        return

    ip, _port = matched
    if ip == rec.guest_ip:
        return

    rec.guest_ip = ip
    rec.guest_user_id = ""
    rec.guest_rank = ""
    rec.session_games = 0
    user = None
    if db.is_configured():
        user = await db.find_user_by_ip(ip)

    if user is not None:
        rec.guest_user_id = user.id
        rec.guest_rank = user.rank
        post.guest_name = user.name
        post.guest_avatar = discord_avatar_url(user.id, user.avatar)
    else:
        post.guest_name = ""
        post.guest_avatar = ""

    post.guest_connected = True
    post.ranked_active = (
        post.post_type == "ranked"
        and bool(rec.guest_user_id)
        and rec.guest_rank == post.rank
    )

    await HUB.publish("upsert", asdict(post))


_probe_bind_addr_cache: Optional[str] = None
# 送信元ポートを固定する場合、同時プローブは同じポートを取り合うので直列化する
_probe_lock = threading.Lock()


def _probe_bind_addr() -> Optional[str]:
    """PROBE_BIND_HOST の解決結果 (IPv4) を返す。未設定なら None。"""
    global _probe_bind_addr_cache
    if not PROBE_BIND_HOST:
        return None
    if _probe_bind_addr_cache is None:
        infos = socket.getaddrinfo(PROBE_BIND_HOST, None, socket.AF_INET, socket.SOCK_DGRAM)
        _probe_bind_addr_cache = infos[0][4][0]
    return _probe_bind_addr_cache


def probe_host_once(
    host: str,
    port: int,
    packet: bytes,
    *,
    timeout_sec: float = 0.2,
) -> Optional[bytes]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        bind_addr = _probe_bind_addr()
        if bind_addr:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((bind_addr, PROBE_BIND_PORT))

        sock.settimeout(timeout_sec)
        sock.sendto(packet, (host, port))

        # 送信元ポート固定時は他ホストからの迷いパケットも届き得るので
        # 送信先からの応答だけを受け取る
        deadline = time.monotonic() + timeout_sec
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            sock.settimeout(remaining)
            data, addr = sock.recvfrom(4096)
            if addr[0] == host or not bind_addr:
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

    with _probe_lock:
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


def check_hostable_autopunch(host: str, port: int) -> bool:
    """AutoPunch リレー経由でホスト登録と soku echo を検証する。"""
    with _probe_lock:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            bind_addr = _probe_bind_addr()
            if bind_addr:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind((bind_addr, PROBE_BIND_PORT))
            else:
                sock.bind(("0.0.0.0", 0))

            my_port = sock.getsockname()[1]

            relay_host, relay_port_s = AUTOPUNCH_RELAY.rsplit(":", 1)
            try:
                relay_port = int(relay_port_s)
                if not (0 < relay_port < 65536):
                    raise ValueError
                relay_ip = socket.gethostbyname(relay_host)
            except (OSError, ValueError):
                print("autopunch relay resolution failed, skipping verification")
                return True

            relay_addr = (relay_ip, relay_port)

            # Stage 1: リレー到達性 (fail-open)
            relay_ok = False
            for _ in range(3):
                try:
                    sock.sendto(b"\x00", relay_addr)
                    deadline = time.monotonic() + 1.0
                    while time.monotonic() < deadline:
                        remaining = deadline - time.monotonic()
                        sock.settimeout(remaining)
                        data, addr = sock.recvfrom(4096)
                        if addr[0] == relay_ip and len(data) == 1:
                            relay_ok = True
                            break
                    if relay_ok:
                        break
                except OSError:
                    pass

            if not relay_ok:
                print("autopunch relay unreachable, skipping verification")
                return True

            # Stage 2: リレー上の登録確認
            lookup = struct.pack("!H", my_port) + socket.inet_aton(host) + struct.pack("!H", port)
            nat_port: Optional[int] = None
            for _ in range(3):
                try:
                    sock.sendto(lookup, relay_addr)
                    deadline = time.monotonic() + 1.0
                    while time.monotonic() < deadline:
                        remaining = deadline - time.monotonic()
                        sock.settimeout(remaining)
                        data, addr = sock.recvfrom(4096)
                        if addr[0] != relay_ip or len(data) != 8:
                            continue
                        internal_port = struct.unpack("!H", data[0:2])[0]
                        candidate_nat_port = struct.unpack("!H", data[2:4])[0]
                        ip = socket.inet_ntoa(data[4:8])
                        if ip == host:
                            nat_port = candidate_nat_port
                            break
                    if nat_port is not None:
                        break
                except OSError:
                    pass

            if nat_port is None:
                return False

            # Stage 3: NAT ポートへ soku echo プローブ
            packet = soku_echo_packet()
            for _ in range(10):
                try:
                    sock.sendto(packet, (host, nat_port))
                    deadline = time.monotonic() + 0.4
                    while time.monotonic() < deadline:
                        remaining = deadline - time.monotonic()
                        sock.settimeout(remaining)
                        data, addr = sock.recvfrom(4096)
                        if addr[0] != host:
                            continue
                        if len(data) < 2:
                            continue
                        if data[0] in (0x07, 0x08):
                            return True
                except OSError:
                    pass

            return False
        except OSError:
            return False
        finally:
            sock.close()


def parse_ipv4_addr_or_raise(addr: str) -> tuple[str, int]:
    """'IPv4:port' 形式を検証する。非想天則は IPv4 のみ対応のため、
    IPv6 アドレス (クライアントが IPv6 でサーバーに接続した場合など) は弾く。"""
    try:
        host, port_s = addr.rsplit(":", 1)
        port = int(port_s)
        if not (0 < port < 65536):
            raise ValueError
        socket.inet_aton(host)  # IPv4 表記のみ許可
        if host.count(".") != 3:
            raise ValueError  # "127.1" のような省略表記は弾く
    except (ValueError, OSError):
        raise HTTPException(
            status_code=422,
            detail="addr must be IPv4:port (IPv6 is not supported by the game)",
        )
    return host, port


async def verify_hostable_or_raise(addr: str, *, autopunch: bool = False) -> bool:
    host, port = parse_ipv4_addr_or_raise(addr)

    if not HOSTCHECK_ENABLED:
        return True

    if autopunch:
        result = await asyncio.to_thread(check_hostable_autopunch, host, port)
        if not result:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "autopunch host not reachable (is autopunch running?)",
                },
            )
    else:
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
