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
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, Literal, Optional
from uuid import uuid4
from urllib.parse import quote, urlencode, urlparse

import httpx
import trueskill
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator, model_validator

import db
import geoip
import post_redis
import client_release

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
# 対戦中 (ゲスト接続済み) はデプロイ中のハートビート途切れでも復元できるよう長めに保持
POST_BATTLE_TTL_SEC = 600
CLEANUP_INTERVAL_SEC = 5
# 全募集を 1 周プローブする目標時間 (件数に応じて tick 間隔を自動調整)
GUEST_PROBE_ROUND_SEC = float(os.environ.get("ASOBBY_GUEST_PROBE_ROUND_SEC", "10"))
GUEST_PROBE_MIN_TICK_SEC = float(os.environ.get("ASOBBY_GUEST_PROBE_MIN_TICK_SEC", "0.4"))
GUEST_PROBE_TIMEOUT_SEC = float(os.environ.get("ASOBBY_GUEST_PROBE_TIMEOUT_SEC", "0.35"))
SSE_PING_INTERVAL_SEC = 15

VISITOR_COOKIE = "asobby_visitor"
PRESENCE_TTL_SEC = 90.0
_VISITOR_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{8,64}$")
PRESENCE_LOCAL: dict[str, float] = {}
_PRESENCE_LOCK = threading.Lock()

# ランクマッチ: 昇降格判定に必要な最低試合数
RANKED_EVAL_MIN_GAMES = 30
# 昇降格判定に使う直近ランクマ対戦数
RANKED_EVAL_WINDOW = 30
# 1 セッション (ゲスト接続) でランクマ扱いになるのは最初の 3 戦まで
RANKED_SESSION_MAX_GAMES = 3
# この分数以上空くと別セッション (sync 遅延時の streak 判定)
RANKED_SESSION_GAP_MINUTES = 30

# 昇降格ルール (rank -> promote_at, demote_at, promote_to, demote_to)
# promote_at / demote_at は None なら該当方向の判定なし
RANK_LADDER: dict[str, dict[str, Any]] = {
    "easy": {"promote_at": 0.5, "demote_at": None, "promote_to": "normal", "demote_to": None},
    "normal": {"promote_at": 0.5, "demote_at": None, "promote_to": "ex", "demote_to": None},
    "ex": {"promote_at": 0.6, "demote_at": 0.2, "promote_to": "hard", "demote_to": "normal"},
    "hard": {"promote_at": 0.6, "demote_at": 0.2, "promote_to": "luna", "demote_to": "ex"},
    "luna": {"promote_at": 0.7, "demote_at": 0.2, "promote_to": "ph", "demote_to": "hard"},
    "ph": {"promote_at": None, "demote_at": None, "promote_to": None, "demote_to": None},
}
RANK_ORDER: dict[str, int] = {k: i for i, k in enumerate(RANK_LADDER.keys())}


def ranked_session_active(post: Post, guest_user_id: str, guest_rank: str) -> bool:
    """現在接続中ゲストとの対戦がランクマ扱いか。"""
    if post.post_type != "ranked" or not guest_user_id or not guest_rank:
        return False
    return guest_rank == post.rank


def refresh_ranked_active(rec: PostRecord) -> None:
    rec.post.ranked_active = ranked_session_active(
        rec.post, rec.guest_user_id, rec.guest_rank
    )


def _find_active_ranked_record(
    host_user_id: str,
    guest_user_id: Optional[str],
) -> Optional[PostRecord]:
    """ホストの進行中ランクマ募集 (同一ゲスト) を返す。"""
    if not guest_user_id:
        return None
    for rec in RECORDS.values():
        if rec.owner_user_id != host_user_id:
            continue
        if rec.post.post_type != "ranked":
            continue
        if rec.guest_user_id == guest_user_id:
            return rec
    return None


async def ranked_session_limit_reached(
    host_user_id: str,
    *,
    guest_user_id: Optional[str],
    host_profile: str,
    guest_profile: str,
    match_rank: Optional[str],
    played_at: datetime,
    session_games: int = 0,
) -> bool:
    """同一対戦相手との連続ランクマが 3 戦上限に達したか (入れ替え後も同一ペア)。"""
    if guest_user_id and host_user_id:
        streak = await db.count_ranked_pair_streak_before(
            host_user_id,
            guest_user_id,
            before=played_at,
            match_rank=match_rank,
            gap_minutes=RANKED_SESSION_GAP_MINUTES,
        )
        return streak >= RANKED_SESSION_MAX_GAMES

    if session_games >= RANKED_SESSION_MAX_GAMES:
        return True

    streak = await db.count_ranked_streak_before(
        host_user_id,
        host_profile=host_profile,
        guest_profile=guest_profile,
        guest_user_id=guest_user_id,
        match_rank=match_rank,
        before=played_at,
        gap_minutes=RANKED_SESSION_GAP_MINUTES,
    )
    return streak >= RANKED_SESSION_MAX_GAMES


async def resolve_ranked_for_host_session(
    host_user_id: str,
    *,
    guest_user_id: Optional[str],
    host_profile: str,
    guest_profile: str,
    client_wants_ranked: bool,
    match_rank: Optional[str],
    played_at: datetime,
) -> tuple[bool, Optional[str]]:
    """ホスト側報告/sync の ranked 可否 (セッション 3 戦上限)。"""
    if not client_wants_ranked:
        return False, None

    rec = _find_active_ranked_record(host_user_id, guest_user_id)
    if rec is not None:
        refresh_ranked_active(rec)
        if not rec.post.ranked_active:
            return False, None
        session_games = rec.session_games
        band_rank = rec.post.rank
    else:
        session_games = 0
        band_rank = match_rank
        if not guest_user_id:
            return False, None
        guest_info = await db.get_user_rank(guest_user_id)
        host_info = await db.get_user_rank(host_user_id)
        if guest_info is None or host_info is None:
            return False, None
        guest_rank, _, _ = guest_info
        host_rank, _, _ = host_info
        if guest_rank != host_rank:
            return False, None
        band_rank = band_rank or host_rank

    if await ranked_session_limit_reached(
        host_user_id,
        guest_user_id=guest_user_id,
        host_profile=host_profile,
        guest_profile=guest_profile,
        match_rank=band_rank,
        played_at=played_at,
        session_games=session_games,
    ):
        return False, None

    return True, band_rank


def _guest_ranked_session_rank(
    guest_user_id: str,
    guest_ip: str,
) -> Optional[str]:
    """進行中ランクマ募集に接続中のゲストなら募集帯を返す (ゲスト報告の参考用)。"""
    for rec in RECORDS.values():
        if rec.post.post_type != "ranked":
            continue
        if rec.guest_user_id != guest_user_id and rec.guest_ip != guest_ip:
            continue
        refresh_ranked_active(rec)
        if rec.post.ranked_active:
            return rec.post.rank
    return None


async def _assign_guest_from_ip(
    rec: PostRecord,
    *,
    guest_profile: str = "",
) -> None:
    """echo IP から guest_user_id を同定 (プロファイル整合チェック付き)。"""
    if not rec.guest_ip or rec.guest_user_id:
        return
    user = await db.find_user_by_ip(rec.guest_ip)
    if user is None or user.id == rec.owner_user_id:
        return
    if guest_profile and not await db.ip_guest_identification_ok(
        user.id, guest_profile
    ):
        return
    rec.guest_user_id = user.id
    rec.guest_rank = user.rank
    rec.post.guest_user_id = user.id


async def _bump_session_games(host_user_id: str, guest_user_id: Optional[str]) -> None:
    rec = _find_active_ranked_record(host_user_id, guest_user_id)
    if rec is None:
        return
    rec.session_games += 1
    await _persist_record(rec)

NET_CHECKING = 2  # hisoutensoku net_status: 接続処理中 (キャラセレ/ロード)
NET_ALIVE = 3  # hisoutensoku net_status: ホスト待ち
NET_BATTLE = 4  # hisoutensoku net_status: 対戦中
CREATE_MIN_INTERVAL_SEC = 2.0
MAX_ACTIVE_POSTS_PER_IP = 2

# Web ロビーからホストへの定型メッセージ
MESSAGE_COOLDOWN_SEC = 60.0
MESSAGE_MAX_PENDING = 20
PING_WARN_MS_DEFAULT = 60
PING_WARN_GIUROLL_MS_DEFAULT = 100
PING_WARN_MS_MIN = 1
PING_WARN_MS_MAX = 5000
PING_REPORT_COOLDOWN_SEC = 300.0
PING_WARN_MAX_PENDING = 20
MESSAGE_SENT_LOG_MAX = 50
MESSAGE_LAST_SENT: dict[tuple[str, str], float] = {}  # (sender_user_id, post_id) -> 時刻
PING_REPORT_LAST: dict[tuple[str, str], float] = {}  # (viewer_user_id, post_id) -> 時刻

# ロビーチャット (インメモリ)
LOBBY_CHAT_MAX_MESSAGES = 100
LOBBY_CHAT_MAX_TEXT = 500
LOBBY_CHAT_MAX_LINES = 8
LOBBY_CHAT_COOLDOWN_SEC = 3.0
LOBBY_CHAT_MAX_AGE_SEC = 3600
LOBBY_CHAT_LAST_SENT: dict[str, float] = {}  # user_id -> unix ts

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
HOSTCHECK_UPDATE_INTERVAL_SEC = float(
    os.environ.get("ASOBBY_HOSTCHECK_UPDATE_INTERVAL_SEC", "20")
)
# デプロイ直後は UDP プローブが不安定になりやすい。hydrate 済み募集の
# 到達性フラグをすぐ上書きしないよう、再起動後しばらく再検証を抑止する。
HOSTCHECK_STARTUP_GRACE_SEC = float(
    os.environ.get("ASOBBY_HOSTCHECK_STARTUP_GRACE_SEC", "45")
)
# デプロイ中ハートビートが途切れても Redis から募集を復元できるよう、
# 起動時 hydrate だけ updated_at 判定を POST_TTL より長く許容する。
HYDRATE_STALE_GRACE_SEC = float(
    os.environ.get("ASOBBY_HYDRATE_STALE_GRACE_SEC", "120")
)

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
DATA_DIR = STATIC_DIR / "data"


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
    direct_reachable: bool = False  # 直接 UDP プローブで到達確認できたか (AP 表示用)
    reachability_uncertain: bool = False  # AP 必須だが接続可否が保証できない (❓ 表示用)
    match_status: str = ""
    net_status: int = 0
    owner_name: str = ""  # Discord ログイン時の表示名（未ログインなら空）
    owner_avatar: str = ""
    guest_name: str = ""  # 対戦中ゲストが Discord ログイン済みなら表示名
    guest_avatar: str = ""
    guest_user_id: str = ""  # 同定済みゲストの Discord ID (高 Ping 警告の本人確認用)
    guest_connected: bool = False  # プローブでゲスト検出中
    ranked_active: bool = False  # 現在のゲストとのセッションがランクマ扱いか
    ping_warn_enabled: bool = True  # 高 Ping 警告をホストへ送るか
    ping_warn_ms: int = PING_WARN_MS_DEFAULT  # この RTT 以上の viewer ping でホストへ警告
    ping_warn_giuroll_ms: int = PING_WARN_GIUROLL_MS_DEFAULT  # Giuroll ホスト向け警告しきい値
    country_code: str = ""  # addr の IP から推定した ISO 3166-1 alpha-2
    country_name: str = ""  # 表示用国名（日本語優先）


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
    pending_messages: list[dict] = field(default_factory=list)
    pending_ping_warnings: list[dict] = field(default_factory=list)
    # giuroll_request / casual_invite の送信ログ (返信 API 用。配送キューとは別)
    sent_log: dict[str, dict] = field(default_factory=dict)
    last_hostcheck_at: float = 0.0


POST_REDIS_TTL_SEC = POST_TTL_SEC + 30


def post_is_in_battle(rec: PostRecord) -> bool:
    return bool(rec.guest_ip or rec.post.guest_connected)


def post_record_ttl(rec: PostRecord) -> float:
    if post_is_in_battle(rec):
        return POST_BATTLE_TTL_SEC
    return POST_TTL_SEC


def post_record_redis_ttl(rec: PostRecord) -> int:
    return int(post_record_ttl(rec) + 30)


def post_record_to_dict(rec: PostRecord) -> dict[str, Any]:
    return {
        "post": asdict(rec.post),
        "owner_token": rec.owner_token,
        "creator_ip": rec.creator_ip,
        "owner_user_id": rec.owner_user_id,
        "guest_ip": rec.guest_ip,
        "guest_user_id": rec.guest_user_id,
        "guest_rank": rec.guest_rank,
        "session_games": rec.session_games,
        "pending_messages": list(rec.pending_messages),
        "pending_ping_warnings": list(rec.pending_ping_warnings),
        "sent_log": dict(rec.sent_log),
        "last_hostcheck_at": rec.last_hostcheck_at,
    }


def post_record_from_dict(data: dict[str, Any]) -> PostRecord:
    post_data = dict(data.get("post") or {})
    post_data.pop("challenge_upper", None)
    rec = PostRecord(
        post=Post(**post_data),
        owner_token=str(data.get("owner_token", "")),
        creator_ip=str(data.get("creator_ip", "")),
        owner_user_id=str(data.get("owner_user_id", "")),
        guest_ip=str(data.get("guest_ip", "")),
        guest_user_id=str(data.get("guest_user_id", "")),
        guest_rank=str(data.get("guest_rank", "")),
        session_games=int(data.get("session_games", 0) or 0),
        pending_messages=list(data.get("pending_messages") or []),
        pending_ping_warnings=list(data.get("pending_ping_warnings") or []),
        sent_log=dict(data.get("sent_log") or {}),
        last_hostcheck_at=float(data.get("last_hostcheck_at", 0) or 0),
    )
    if not rec.post.guest_user_id and rec.guest_user_id:
        rec.post.guest_user_id = rec.guest_user_id
    return rec


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
    challenge_upper: bool = False  # 互換用 (無視)
    ping_warn_enabled: bool = True
    ping_warn_ms: int = Field(default=PING_WARN_MS_DEFAULT, ge=PING_WARN_MS_MIN, le=PING_WARN_MS_MAX)
    ping_warn_giuroll_ms: int = Field(
        default=PING_WARN_GIUROLL_MS_DEFAULT,
        ge=PING_WARN_MS_MIN,
        le=PING_WARN_MS_MAX,
    )


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
    played_at: float = 0


class GuestReportIn(BaseModel):
    winner: Literal["host", "guest", "draw"]
    host_char: Optional[int] = None
    guest_char: Optional[int] = None
    host_profile: str = Field(default="", max_length=64)
    guest_profile: str = Field(default="", max_length=64)
    played_at: float = 0


class DevicePollIn(BaseModel):
    device_code: str = Field(max_length=128)


class ClientExchangeIn(BaseModel):
    code: str = Field(max_length=128)


class ChooseRankIn(BaseModel):
    rank: Literal["easy", "normal", "ex", "hard", "luna"]


class FaviconNotifyIn(BaseModel):
    ranked_enabled: Optional[bool] = None
    casual_enabled: Optional[bool] = None
    ranked_same_band_only: Optional[bool] = None
    max_ping_ms: Optional[int] = Field(default=None, ge=1, le=999)
    require_ping: Optional[bool] = None
    exclude_in_battle: Optional[bool] = None


class UserSettingsPatchIn(BaseModel):
    favicon_notify: Optional[FaviconNotifyIn] = None
    replay_refusal_until: Optional[float] = None


class PostMessageIn(BaseModel):
    type: Literal["giuroll_request", "casual_invite"]


class PingReportIn(BaseModel):
    rtt_ms: int = Field(ge=1, le=60000)


class PostReplyIn(BaseModel):
    id: str = Field(max_length=64)
    owner_token: str = Field(max_length=128)
    message_id: str = Field(max_length=64)
    reply: Literal["accept", "decline"]


_CLIENT_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_SYNC_PLAYED_AT_MIN = datetime(2004, 1, 1, 0, 0, 0, tzinfo=timezone.utc)


def _normalize_char(char: Optional[int]) -> Optional[int]:
    if char is None:
        return None
    if 0 <= char <= 19:
        return char
    return None


class SyncMatchIn(BaseModel):
    client_id: str
    played_at: float
    my_side: Literal["host", "guest", "client"]
    winner: Literal["host", "guest", "draw"]
    my_char: Optional[int] = None
    opp_char: Optional[int] = None
    my_profile: str = Field(default="", max_length=64)
    opp_profile: str = Field(default="", max_length=64)
    ranked: bool = False
    match_rank: Optional[str] = None

    @field_validator("client_id")
    @classmethod
    def validate_client_id(cls, v: str) -> str:
        if not _CLIENT_ID_RE.match(v):
            raise ValueError("client_id must be 32 hex chars")
        return v

    @field_validator("my_char", "opp_char", mode="before")
    @classmethod
    def validate_char(cls, v: Any) -> Optional[int]:
        if v is None:
            return None
        try:
            n = int(v)
        except (TypeError, ValueError):
            return None
        return _normalize_char(n)


class SyncMatchesIn(BaseModel):
    matches: list[SyncMatchIn] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_count(self) -> SyncMatchesIn:
        if len(self.matches) > 500:
            raise ValueError("matches must be at most 500")
        return self


class ChatMessageIn(BaseModel):
    text: str = Field(max_length=LOBBY_CHAT_MAX_TEXT + 50)
    lang: str = "ja"


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
    token = session_token_from_request(request)
    if token:
        return verify_session_token(token)
    return None


def session_token_from_request(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.cookies.get("asobby_session") or ""


def revoke_session_token(token: str) -> None:
    if token:
        LOGOUT_REVOKED.add(token)


def clear_session_cookie(response: Response) -> None:
    response.set_cookie(
        key="asobby_session",
        value="",
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=0,
        path="/",
    )


def discord_avatar_url(user_id: str, avatar_hash: str) -> str:
    if not avatar_hash:
        return ""
    return f"https://cdn.discordapp.com/avatars/{user_id}/{avatar_hash}.png?size=64"


def display_rating(mu: float, sigma: float) -> float:
    """TrueSkill 表示レート = mu - 3 * sigma"""
    return round(mu - 3 * sigma, 1)


def valid_ph_char(char_id: Optional[int]) -> bool:
    return char_id is not None and 0 <= char_id <= 20


async def ph_char_ratings_payload(user_id: str) -> list[dict[str, Any]]:
    """Ph ユーザーの全キャラ (0–20) 表示レート一覧。"""
    rows = await db.list_char_ratings(user_id)
    if not rows:
        rank_info = await db.get_user_rank(user_id)
        mu = db.DEFAULT_TS_MU
        sigma = db.DEFAULT_TS_SIGMA
        if rank_info is not None:
            _rank, mu, sigma = rank_info
        await db.init_ph_char_ratings(user_id, mu, sigma)
        rows = await db.list_char_ratings(user_id)
    rating_map = {
        char_id: display_rating(mu, sigma) for char_id, mu, sigma in rows
    }
    default = display_rating(db.DEFAULT_TS_MU, db.DEFAULT_TS_SIGMA)
    return [
        {"char": char_id, "rating": rating_map.get(char_id, default)}
        for char_id in db.PH_CHAR_IDS
    ]


async def resolve_session(request: Request) -> Optional[dict[str, Any]]:
    """Bearer トークンを検証し、DB の token_version と突合する。
    有効なら {"id", "name", "avatar", "rank", "rating"} を返し、last_seen / last_ip も最新化する。"""
    sess = session_from_request(request)
    if sess is None or not db.is_configured():
        return None
    user = await db.get_user_if_token_valid(sess["sub"], sess["ver"])
    if user is None:
        return None
    await db.touch_user(
        user.id,
        client_ip(request),
        client_version=client_version_from_request(request),
    )
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

# Web ブラウザ直接ログイン (state -> (created_at, ログイン後のリダイレクト先))
WEB_LOGINS: Dict[str, tuple[float, str]] = {}


@dataclass
class HandoffCode:
    """ブラウザのクッキーセッションからクライアントへトークンを引き渡す一時コード。"""
    user_id: str
    created_at: float


# クライアント連携用ワンタイムコード (code -> HandoffCode)
CLIENT_HANDOFF_CODES: Dict[str, HandoffCode] = {}
HANDOFF_CODE_TTL_SEC = 120.0


def cleanup_handoff_codes() -> None:
    now = time.time()
    for key in [
        k for k, v in CLIENT_HANDOFF_CODES.items()
        if (now - v.created_at) > HANDOFF_CODE_TTL_SEC
    ]:
        CLIENT_HANDOFF_CODES.pop(key, None)


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
        k for k, v in WEB_LOGINS.items()
        if (now - v[0]) > DEVICE_CODE_TTL_SEC
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
LOBBY_CHAT_LANGS = ("ja", "en")
LOBBY_CHATS: dict[str, deque[dict[str, Any]]] = {
    lang: deque(maxlen=LOBBY_CHAT_MAX_MESSAGES) for lang in LOBBY_CHAT_LANGS
}
RECORDS: Dict[str, PostRecord] = {}
LAST_CREATE_AT: Dict[str, float] = {}
SERVER_STARTED_AT: float = 0.0


async def _persist_record(rec: PostRecord) -> None:
    if not post_redis.is_configured():
        return
    try:
        await asyncio.to_thread(
            post_redis.save_record_dict,
            post_record_to_dict(rec),
            ttl_sec=post_record_redis_ttl(rec),
        )
    except Exception as e:
        print(f"post_redis save error: {e}")


async def _delete_persisted_post(post_id: str) -> None:
    if not post_redis.is_configured():
        return
    try:
        await asyncio.to_thread(post_redis.delete_record, post_id)
    except Exception as e:
        print(f"post_redis delete error: {e}")


async def _hydrate_records_from_redis() -> None:
    if not post_redis.is_configured():
        return
    try:
        rows = await asyncio.to_thread(post_redis.load_all_record_dicts)
    except Exception as e:
        print(f"post_redis load error: {e}")
        return

    now = time.time()
    restored = 0
    for data in rows:
        try:
            rec = post_record_from_dict(data)
        except (TypeError, KeyError):
            post_id = str((data.get("post") or {}).get("id", ""))
            if post_id:
                await _delete_persisted_post(post_id)
            continue
        hydrate_ttl = post_record_ttl(rec) + HYDRATE_STALE_GRACE_SEC
        if now - rec.post.updated_at >= hydrate_ttl:
            await _delete_persisted_post(rec.post.id)
            continue
        RECORDS[rec.post.id] = rec
        restored += 1
    if restored:
        print(f"post_redis: restored {restored} post(s)")


async def _hydrate_chat_from_redis() -> None:
    if not post_redis.is_configured():
        return
    try:
        by_lang = await asyncio.to_thread(
            post_redis.load_all_chat_messages,
            max_messages=LOBBY_CHAT_MAX_MESSAGES,
            max_age_sec=LOBBY_CHAT_MAX_AGE_SEC,
        )
    except Exception as e:
        print(f"post_redis chat load error: {e}")
        return
    total = 0
    for lang in LOBBY_CHAT_LANGS:
        LOBBY_CHATS[lang].clear()
        for msg in by_lang.get(lang, []):
            LOBBY_CHATS[lang].append(msg)
            total += 1
    if total:
        print(f"post_redis: restored {total} chat message(s)")


async def _sync_chat_to_redis() -> None:
    if not post_redis.is_configured():
        return
    try:
        for lang in LOBBY_CHAT_LANGS:
            await asyncio.to_thread(
                post_redis.replace_chat_messages,
                lang,
                list(LOBBY_CHATS[lang]),
                max_messages=LOBBY_CHAT_MAX_MESSAGES,
                max_age_sec=LOBBY_CHAT_MAX_AGE_SEC,
            )
    except Exception as e:
        print(f"post_redis chat save error: {e}")


def _normalize_chat_lang(lang: str | None) -> str:
    return post_redis.normalize_chat_lang(lang)


def _trim_lobby_chat_by_age() -> bool:
    cutoff = time.time() - LOBBY_CHAT_MAX_AGE_SEC
    changed = False
    for lang in LOBBY_CHAT_LANGS:
        chat = LOBBY_CHATS[lang]
        while chat and float(chat[0].get("ts", 0)) < cutoff:
            chat.popleft()
            changed = True
    return changed


def lobby_chat_snapshot() -> dict[str, list[dict[str, Any]]]:
    _trim_lobby_chat_by_age()
    return {lang: list(LOBBY_CHATS[lang]) for lang in LOBBY_CHAT_LANGS}


def _normalize_chat_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _validate_chat_text(text: str) -> str:
    normalized = _normalize_chat_text(text).strip()
    if not normalized:
        raise HTTPException(status_code=422, detail="empty message")
    if len(normalized) > LOBBY_CHAT_MAX_TEXT:
        raise HTTPException(status_code=422, detail="message too long")
    line_count = normalized.count("\n") + 1
    if line_count > LOBBY_CHAT_MAX_LINES:
        raise HTTPException(status_code=422, detail="too many lines")
    return normalized


async def cleanup_loop() -> None:
    while True:
        now = time.time()
        stale_ids = [
            post_id
            for post_id, rec in list(RECORDS.items())
            if (now - rec.post.updated_at) >= post_record_ttl(rec)
        ]
        for post_id in stale_ids:
            RECORDS.pop(post_id, None)
            cleanup_message_state_for_post(post_id)
            await _delete_persisted_post(post_id)
            await HUB.publish("close", {"id": post_id, "reason": "ttl_expired", "ts": now_ts()})
        await asyncio.sleep(CLEANUP_INTERVAL_SEC)


async def guest_probe_loop() -> None:
    """各募集ホストへ soku echo を送り、対戦中ゲスト IP を定期的に取得する。

    送信元 UDP ポート固定のためプローブ本体は直列だが、全件を一括で回すのではなく
    ラウンドロビンで分散し、募集作成時の到達性検証とのロック競合を減らす。
    """
    if not HOSTCHECK_ENABLED:
        return

    cursor = 0
    while True:
        tick_sleep = GUEST_PROBE_ROUND_SEC
        try:
            records = probe_target_records()
            n = len(records)
            if n == 0:
                await asyncio.sleep(GUEST_PROBE_ROUND_SEC)
                continue

            tick_sleep = guest_probe_tick_sleep_sec(n)
            rec = records[cursor % n]
            cursor = (cursor + 1) % n
            post = rec.post
            host, port = parse_probe_addr(post)
            if host is None:
                await asyncio.sleep(tick_sleep)
                continue

            reply = await asyncio.to_thread(
                probe_post_status,
                host,
                port,
                post.autopunch,
            )
            await apply_guest_probe(rec, reply)
        except Exception as e:
            print(f"guest_probe_loop error: {e}")

        await asyncio.sleep(tick_sleep)


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
    global SERVER_STARTED_AT
    if DATABASE_URL:
        await asyncio.to_thread(run_migrations)
        db.init_engine(DATABASE_URL)
    if await asyncio.to_thread(geoip.ensure_geoip_db):
        await asyncio.to_thread(geoip.init_geoip)
    await _hydrate_records_from_redis()
    await _hydrate_chat_from_redis()
    SERVER_STARTED_AT = time.time()
    await client_release.get_latest_release()
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

CANONICAL_HOST = (urlparse(PUBLIC_BASE_URL).hostname or "").lower()


@app.middleware("http")
async def redirect_to_canonical_host(request: Request, call_next):
    """fly.dev 等の代替ホストはカスタムドメインへ寄せる (OAuth / Cookie 整合)。"""
    if not CANONICAL_HOST:
        return await call_next(request)
    host = (request.headers.get("host") or "").split(":")[0].lower()
    if host.endswith(".fly.dev") and host != CANONICAL_HOST:
        target = PUBLIC_BASE_URL.rstrip("/") + request.url.path
        if request.url.query:
            target += "?" + request.url.query
        return RedirectResponse(target, status_code=301)
    return await call_next(request)


@app.middleware("http")
async def client_update_headers(request: Request, call_next):
    response = await call_next(request)
    client_ver = (request.headers.get("X-Asobby-Client-Version") or "").strip()
    if not client_ver:
        return response
    latest = client_release.cached_latest()
    if latest is None:
        latest = await client_release.get_latest_release()
    if latest and client_release.is_older(client_ver, latest["version"]):
        response.headers["X-Asobby-Client-Update"] = latest["tag"]
        download = latest.get("html_url") or latest.get("download_url") or ""
        if download:
            response.headers["X-Asobby-Client-Download"] = download
    return response


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


def client_version_from_request(request: Request) -> str:
    return (request.headers.get("X-Asobby-Client-Version") or "").strip()[:32]


def sorted_public_posts() -> list[dict[str, Any]]:
    records = sorted(
        RECORDS.values(),
        key=lambda r: (-r.post.created_at, r.post.id),
    )
    return [asdict(r.post) for r in records]


def user_has_other_posts(user_id: str) -> bool:
    if not user_id:
        return False
    return any(rec.owner_user_id != user_id for rec in RECORDS.values())


def is_other_post(rec: PostRecord, user_id: str, user_name: str) -> bool:
    if user_id and rec.owner_user_id == user_id:
        return False
    if user_name and rec.post.owner_name == user_name:
        return False
    return True


def ping_passes_notify(prefs: dict[str, Any], ping_ms: Optional[int]) -> bool:
    max_ping = int(prefs.get("max_ping_ms", 60))
    if prefs.get("require_ping"):
        return ping_ms is not None and ping_ms <= max_ping
    if ping_ms is not None and ping_ms > max_ping:
        return False
    return True


def classify_post_notify(
    post: Post,
    *,
    prefs: dict[str, Any],
    viewer_rank: str,
    ping_ms: Optional[int] = None,
) -> tuple[bool, bool]:
    """Returns (ranked_notify, casual_notify) for a single post."""
    if prefs.get("exclude_in_battle") and (
        post.guest_connected or post.net_status == NET_BATTLE
    ):
        return False, False
    if not ping_passes_notify(prefs, ping_ms):
        return False, False

    post_type = post.post_type or "casual"
    ranked = False
    casual = False
    if post_type == "ranked":
        if prefs.get("ranked_enabled", True):
            if prefs.get("ranked_same_band_only", True):
                band = (viewer_rank or "").lower()
                if band and (post.rank or "easy").lower() == band:
                    ranked = True
            else:
                ranked = True
    elif prefs.get("casual_enabled", True):
        casual = True
    return ranked, casual


def compute_favicon_badges(
    user_id: str,
    user_name: str,
    viewer_rank: str,
    prefs: dict[str, Any],
    ping_by_post_id: Optional[dict[str, Optional[int]]] = None,
) -> dict[str, bool]:
    ranked_any = False
    casual_any = False
    pings = ping_by_post_id or {}
    for rec in RECORDS.values():
        if not is_other_post(rec, user_id, user_name):
            continue
        ping_ms = pings.get(rec.post.id)
        ranked, casual = classify_post_notify(
            rec.post,
            prefs=prefs,
            viewer_rank=viewer_rank,
            ping_ms=ping_ms,
        )
        ranked_any = ranked_any or ranked
        casual_any = casual_any or casual
    return {"ranked": ranked_any, "casual": casual_any}


def get_record_or_raise(post_id: str, owner_token: str) -> PostRecord:
    rec = RECORDS.get(post_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="post not found")
    if not secrets.compare_digest(rec.owner_token, owner_token):
        raise HTTPException(status_code=403, detail="invalid owner_token")
    return rec


def post_ping_warn_threshold(post: Post) -> int:
    if not post.ping_warn_enabled:
        return PING_WARN_MS_MAX + 1
    if post.giuroll:
        return int(post.ping_warn_giuroll_ms or PING_WARN_GIUROLL_MS_DEFAULT)
    return int(post.ping_warn_ms or PING_WARN_MS_DEFAULT)


def viewer_may_report_ping(rec: PostRecord, user_id: str, ip: str) -> bool:
    """接続中ゲスト本人だけが ping-report できる。"""
    if rec.guest_user_id:
        return rec.guest_user_id == user_id
    if rec.guest_ip:
        return rec.guest_ip == ip
    return False


def cleanup_message_state_for_post(post_id: str) -> None:
    """投稿削除時に MESSAGE_LAST_SENT / PING_REPORT_LAST の該当エントリを掃除する。"""
    for key in [k for k in MESSAGE_LAST_SENT if k[1] == post_id]:
        MESSAGE_LAST_SENT.pop(key, None)
    for key in [k for k in PING_REPORT_LAST if k[1] == post_id]:
        PING_REPORT_LAST.pop(key, None)


# ----------------------------
# Routes
# ----------------------------
@app.get("/stats")
async def stats_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "stats.html")


@app.get("/guide")
async def guide_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "guide.html")


@app.get("/settings")
async def settings_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "settings.html")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon_ico() -> FileResponse:
    return FileResponse(STATIC_DIR / "favicon.ico", media_type="image/x-icon")


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

    matches = await db.fetch_ranked_matches_at_current_rank(
        user_id, limit=RANKED_EVAL_WINDOW
    )
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
    host_char: Optional[int] = None,
    guest_char: Optional[int] = None,
) -> None:
    """ph 同士のランクマ対戦確定時にキャラ別 TrueSkill レートを更新する。"""
    if not valid_ph_char(host_char) or not valid_ph_char(guest_char):
        return
    assert host_char is not None and guest_char is not None

    host_info = await db.get_user_rank(host_user_id)
    guest_info = await db.get_user_rank(guest_user_id)
    if host_info is None or guest_info is None:
        return
    host_rank, host_mu, host_sigma = host_info
    guest_rank, guest_mu, guest_sigma = guest_info
    if host_rank != "ph" or guest_rank != "ph":
        return

    host_char_info = await db.get_char_rating(host_user_id, host_char)
    guest_char_info = await db.get_char_rating(guest_user_id, guest_char)
    if host_char_info is None:
        await db.init_ph_char_ratings(host_user_id, host_mu, host_sigma)
        host_char_info = (host_mu, host_sigma)
    if guest_char_info is None:
        await db.init_ph_char_ratings(guest_user_id, guest_mu, guest_sigma)
        guest_char_info = (guest_mu, guest_sigma)

    host_rating = trueskill.Rating(mu=host_char_info[0], sigma=host_char_info[1])
    guest_rating = trueskill.Rating(mu=guest_char_info[0], sigma=guest_char_info[1])

    if winner == "draw":
        new_host, new_guest = trueskill.rate_1vs1(host_rating, guest_rating, drawn=True)
    elif winner == "host":
        new_host, new_guest = trueskill.rate_1vs1(host_rating, guest_rating)
    else:
        new_guest, new_host = trueskill.rate_1vs1(guest_rating, host_rating)

    await db.set_char_rating(host_user_id, host_char, new_host.mu, new_host.sigma)
    await db.set_char_rating(guest_user_id, guest_char, new_guest.mu, new_guest.sigma)
    await db.sync_user_aggregate_rating(host_user_id)
    await db.sync_user_aggregate_rating(guest_user_id)


def compute_ranked_stats(matches: list[db.Match], user_id: str) -> dict[str, Any]:
    total = _bucket_stats(matches, user_id)
    recent = _bucket_stats(matches[:RANKED_EVAL_WINDOW], user_id)
    return {
        "total": total,
        "recent30": {
            "games": recent["games"],
            "wins": recent["wins"],
            "win_rate": recent["win_rate"],
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
    window_sec: int = 30,
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


def _sync_match_id(user_id: str, client_id: str) -> str:
    return hashlib.md5(f"sync:{user_id}:{client_id}".encode()).hexdigest()


def _parse_sync_played_at(ts: float) -> Optional[datetime]:
    """unix 秒を UTC datetime に変換。2004 年以前や未来+1 日は None。"""
    try:
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    except (OSError, OverflowError, ValueError):
        return None
    if dt < _SYNC_PLAYED_AT_MIN:
        return None
    if dt > db.utcnow() + timedelta(days=1):
        return None
    return dt


def _resolve_report_played_at(ts: float) -> datetime:
    parsed = _parse_sync_played_at(ts)
    return parsed if parsed is not None else db.utcnow()


async def _find_dedup_match(
    played_at: datetime,
    winner: str,
    host_profile: str,
    guest_profile: str,
    *,
    my_side: Literal["host", "guest", "client"],
) -> Optional[db.Match]:
    """時刻±MATCH_DEDUP_WINDOW_SEC のプロファイル照合 → guest/sync 行の 600s フォールバック。"""
    near = await db.find_near_match_by_profiles(
        played_at,
        winner,
        host_profile,
        guest_profile,
        window_sec=db.MATCH_DEDUP_WINDOW_SEC,
    )
    if near is not None:
        return near
    missing_side: Literal["host", "guest"] = (
        "host" if my_side == "host" else "guest"
    )
    return await db.find_mergeable_profile_match(
        winner,
        host_profile,
        guest_profile,
        missing_side=missing_side,
    )


def _match_to_stats_item(match: db.Match, user_id: str, has_replay: bool) -> dict[str, Any]:
    my_side = "host" if match.host_user_id == user_id else "client"
    return {
        "id": match.id,
        "played_at": _dt_ts(match.played_at) if match.played_at else 0.0,
        "my_side": my_side,
        "winner": match.winner,
        "host_char": match.host_char,
        "guest_char": match.guest_char,
        "host_profile": match.host_profile or "",
        "guest_profile": match.guest_profile or "",
        "ranked": match.ranked,
        "match_rank": match.match_rank,
        "source": match.source,
        "has_replay": has_replay,
    }


def _content_disposition_attachment(filename: str) -> str:
    """RFC 5987 対応の Content-Disposition を生成する。"""
    safe_ascii = _FILENAME_UNSAFE_RE.sub("_", filename or "replay.rep")
    try:
        safe_ascii.encode("ascii")
        ascii_name = safe_ascii
    except UnicodeEncodeError:
        ext = os.path.splitext(safe_ascii)[1]
        ascii_name = "replay" + (ext if ext.isascii() else ".rep")
    if ascii_name != filename:
        return (
            f'attachment; filename="{ascii_name}"; '
            f"filename*=UTF-8''{quote(filename)}"
        )
    return f'attachment; filename="{ascii_name}"'


def _parse_jst_date_start(date_str: str) -> Optional[datetime]:
    """YYYY-MM-DD を JST 00:00:00 として UTC datetime に変換する。"""
    try:
        dt = datetime.strptime(date_str.strip(), "%Y-%m-%d")
    except ValueError:
        return None
    return dt.replace(tzinfo=JST).astimezone(timezone.utc)


def _parse_jst_date_end(date_str: str) -> Optional[datetime]:
    """YYYY-MM-DD を JST 23:59:59 として UTC datetime に変換する。"""
    try:
        dt = datetime.strptime(date_str.strip(), "%Y-%m-%d")
    except ValueError:
        return None
    return dt.replace(hour=23, minute=59, second=59, tzinfo=JST).astimezone(timezone.utc)


def _participant_rank_key(user: Optional[db.User]) -> tuple[int, float]:
    """ランクソート用キー (rank_order, rating)。ユーザー不明は最低。"""
    if user is None:
        return (-1, -1.0)
    rank_order = RANK_ORDER.get(user.rank, -1)
    rating = display_rating(user.ts_mu, user.ts_sigma) if user.rank == "ph" else -1.0
    return (rank_order, rating)


def _match_rank_sort_key(
    host_user: Optional[db.User], guest_user: Optional[db.User]
) -> tuple[int, float]:
    """参加者の高い方の (rank_order, rating) を返す。"""
    return max(_participant_rank_key(host_user), _participant_rank_key(guest_user))


def _user_rank_rating(user: Optional[db.User]) -> tuple[Optional[str], Optional[float]]:
    if user is None:
        return None, None
    rating = display_rating(user.ts_mu, user.ts_sigma) if user.rank == "ph" else None
    return user.rank, rating


def _replay_search_item(
    match: db.Match,
    filename: str,
    host_user: Optional[db.User],
    guest_user: Optional[db.User],
) -> dict[str, Any]:
    host_rank, host_rating = _user_rank_rating(host_user)
    guest_rank, guest_rating = _user_rank_rating(guest_user)
    return {
        "match_id": match.id,
        "played_at": _dt_ts(match.played_at) if match.played_at else 0.0,
        "winner": match.winner,
        "host_char": match.host_char,
        "guest_char": match.guest_char,
        "host_profile": match.host_profile or "",
        "guest_profile": match.guest_profile or "",
        "host_name": host_user.name if host_user else None,
        "guest_name": guest_user.name if guest_user else None,
        "host_rank": host_rank,
        "guest_rank": guest_rank,
        "host_rating": host_rating,
        "guest_rating": guest_rating,
        "filename": filename,
        "ranked": match.ranked,
        "match_rank": match.match_rank,
    }


def _sync_row_from_item(user_id: str, item: SyncMatchIn, match_id: str, played_at: datetime) -> dict[str, Any]:
    my_char = _normalize_char(item.my_char)
    opp_char = _normalize_char(item.opp_char)
    if item.my_side == "host":
        return {
            "id": match_id,
            "host_user_id": user_id,
            "guest_user_id": None,
            "host_ip": "",
            "guest_ip": "",
            "winner": item.winner,
            "host_char": my_char,
            "guest_char": opp_char,
            "host_profile": item.my_profile,
            "guest_profile": item.opp_profile,
            "ranked": item.ranked,
            "match_rank": item.match_rank,
            "source": "sync",
            "played_at": played_at,
        }
    return {
        "id": match_id,
        "host_user_id": None,
        "guest_user_id": user_id,
        "host_ip": "",
        "guest_ip": "",
        "winner": item.winner,
        "host_char": opp_char,
        "guest_char": my_char,
        "host_profile": item.opp_profile,
        "guest_profile": item.my_profile,
        "ranked": item.ranked,
        "match_rank": item.match_rank,
        "source": "sync",
        "played_at": played_at,
    }


@app.get("/stats/me/matches")
async def stats_me_matches(
    request: Request,
    since: float = 0,
    limit: int = 500,
    date_from: str = "",
    date_to: str = "",
) -> dict[str, Any]:
    """ログインユーザーの対戦一覧 (played_at 昇順)。"""
    sess = await resolve_session(request)
    if sess is None:
        raise HTTPException(status_code=401, detail="invalid or expired session")
    if not db.is_configured():
        return {"ok": True, "matches": []}

    limit = min(max(1, limit), 5000)
    dt_from = _parse_jst_date_start(date_from) if date_from.strip() else None
    dt_to = _parse_jst_date_end(date_to) if date_to.strip() else None
    rows = await db.fetch_user_matches_since(
        sess["id"],
        since_ts=since,
        limit=limit,
        date_from=dt_from,
        date_to=dt_to,
    )
    matches = [_match_to_stats_item(m, sess["id"], has_replay) for m, has_replay in rows]
    total = await db.count_user_matches(sess["id"])
    return {"ok": True, "matches": matches, "total": total}


@app.get("/replays")
async def replays_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "replays.html")


@app.get("/replays/search")
async def search_replays(
    player: str = "",
    char1: Optional[int] = Query(None, ge=0, le=19),
    char2: Optional[int] = Query(None, ge=0, le=19),
    date_from: str = "",
    date_to: str = "",
    sort: Literal["date", "rank"] = "date",
    order: Literal["asc", "desc"] = "desc",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """リプレイ付き対戦を検索する (公開・ログイン不要)。"""
    if not db.is_configured():
        return {"ok": True, "total": 0, "replays": []}

    norm_char1 = _normalize_char(char1)
    norm_char2 = _normalize_char(char2)
    dt_from = _parse_jst_date_start(date_from) if date_from.strip() else None
    dt_to = _parse_jst_date_end(date_to) if date_to.strip() else None
    player_q = player.strip()

    rows = await db.search_replay_matches(
        player=player_q or None,
        char1=norm_char1,
        char2=norm_char2 if norm_char1 is not None else None,
        date_from=dt_from,
        date_to=dt_to,
    )

    reverse = order == "desc"
    if sort == "rank":
        rows.sort(
            key=lambda r: _match_rank_sort_key(r[2], r[3]),
            reverse=reverse,
        )
    else:
        rows.sort(
            key=lambda r: _dt_ts(r[0].played_at) if r[0].played_at else 0.0,
            reverse=reverse,
        )

    total = len(rows)
    page_limit = min(max(1, limit), 100)
    page_offset = max(0, offset)
    page = rows[page_offset : page_offset + page_limit]

    return {
        "ok": True,
        "total": total,
        "replays": [
            _replay_search_item(match, filename, host_user, guest_user)
            for match, filename, host_user, guest_user in page
        ],
    }


@app.get("/replays/players")
async def suggest_replay_players(
    q: str = "",
    limit: int = 10,
) -> dict[str, Any]:
    """リプレイ検索用のプレイヤー名候補 (公開・ログイン不要)。"""
    player_q = q.strip()
    if not player_q:
        return {"ok": True, "suggestions": []}
    if not db.is_configured():
        return {"ok": True, "suggestions": []}

    page_limit = min(max(1, limit), 20)
    users, profiles = await db.suggest_replay_players(player_q, page_limit)

    suggestions: list[dict[str, Any]] = []
    for user in users:
        suggestions.append({
            "kind": "user",
            "name": user.name,
            "user_id": user.id,
            "avatar": user.avatar or None,
        })
    for name in profiles:
        suggestions.append({
            "kind": "profile",
            "name": name,
        })
    return {"ok": True, "suggestions": suggestions[:page_limit]}


@app.get("/replays/{match_id}")
async def download_replay(match_id: str) -> Response:
    """リプレイ (.rep) をダウンロードする (公開・ログイン不要)。"""
    if not db.is_configured():
        raise HTTPException(status_code=404, detail="not found")

    match = await db.get_match_by_id(match_id)
    if match is None:
        raise HTTPException(status_code=404, detail="not found")

    replay = await db.get_replay_for_match(match_id)
    if replay is None:
        raise HTTPException(status_code=404, detail="not found")

    return Response(
        content=replay.data,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": _content_disposition_attachment(replay.filename),
        },
    )


@app.post("/matches/sync")
async def sync_matches(body: SyncMatchesIn, request: Request) -> dict[str, Any]:
    """クライアントのローカル戦績を一括登録する。"""
    sess = await resolve_session(request)
    if sess is None:
        raise HTTPException(status_code=401, detail="invalid or expired session")
    if not db.is_configured():
        return {"ok": True, "results": []}

    user_id = sess["id"]
    results: list[dict[str, Any]] = []
    to_insert: list[dict[str, Any]] = []

    existing_ids = await db.filter_existing_match_ids(
        [_sync_match_id(user_id, m.client_id) for m in body.matches]
    )
    for item in body.matches:
        match_id = _sync_match_id(user_id, item.client_id)
        if match_id in existing_ids:
            results.append({
                "client_id": item.client_id,
                "server_id": match_id,
                "status": "duplicate",
            })
            continue

        played_at = _parse_sync_played_at(item.played_at)
        if played_at is None:
            results.append({
                "client_id": item.client_id,
                "server_id": None,
                "status": "invalid",
            })
            continue

        # user_id で照合できない相手側報告 (ゲスト未同定の host 報告など)
        # ともプロファイルで照合する
        if item.my_side == "host":
            hp, gp = item.my_profile, item.opp_profile
        else:
            hp, gp = item.opp_profile, item.my_profile
        near_match = await _find_dedup_match(
            played_at, item.winner, hp, gp, my_side=item.my_side
        )
        if near_match is not None:
            # 既存行に自分の側が未同定で残っていれば紐付ける
            side = "guest" if item.my_side == "client" else item.my_side
            guest_uid = near_match.guest_user_id
            if item.my_side == "host" and not guest_uid:
                guest_uid = await db.find_guest_user_id_by_profiles(
                    item.my_profile, item.opp_profile
                )
            is_ranked = item.ranked
            match_rank = item.match_rank
            if item.my_side == "host":
                is_ranked, match_rank = await resolve_ranked_for_host_session(
                    user_id,
                    guest_user_id=guest_uid,
                    host_profile=item.my_profile,
                    guest_profile=item.opp_profile,
                    client_wants_ranked=item.ranked,
                    match_rank=item.match_rank,
                    played_at=played_at,
                )
            if (
                side == "host"
                and near_match.source in ("guest", "sync")
                and not near_match.host_user_id
                and item.my_side == "host"
            ):
                await db.promote_guest_match(
                    near_match.id,
                    host_user_id=user_id,
                    host_ip="",
                    winner=item.winner,
                    host_char=_normalize_char(item.my_char),
                    guest_char=_normalize_char(item.opp_char),
                    host_profile=item.my_profile,
                    guest_profile=item.opp_profile,
                    ranked=is_ranked,
                    match_rank=match_rank if is_ranked else None,
                    played_at=played_at,
                )
                if is_ranked:
                    await _bump_session_games(user_id, guest_uid)
            else:
                await db.claim_match_side(near_match.id, side, user_id)
            results.append({
                "client_id": item.client_id,
                "server_id": near_match.id,
                "status": "duplicate",
            })
            continue

        row = _sync_row_from_item(user_id, item, match_id, played_at)
        if item.my_side == "host":
            guest_uid = await db.find_guest_user_id_by_profiles(
                item.my_profile, item.opp_profile
            )
            is_ranked, match_rank = await resolve_ranked_for_host_session(
                user_id,
                guest_user_id=guest_uid,
                host_profile=item.my_profile,
                guest_profile=item.opp_profile,
                client_wants_ranked=item.ranked,
                match_rank=item.match_rank,
                played_at=played_at,
            )
            row["ranked"] = is_ranked
            row["match_rank"] = match_rank if is_ranked else None
            if is_ranked:
                await _bump_session_games(user_id, guest_uid)
        to_insert.append(row)
        existing_ids.add(match_id)
        results.append({
            "client_id": item.client_id,
            "server_id": match_id,
            "status": "imported",
        })

    if to_insert:
        await db.bulk_insert_matches(to_insert)

    return {"ok": True, "results": results}


@app.get("/stats/me")
async def stats_me(request: Request) -> dict[str, Any]:
    sess = await resolve_session(request)
    if sess is None:
        raise HTTPException(status_code=401, detail="invalid or expired session")
    matches = await db.fetch_user_matches(sess["id"])
    stats = compute_user_stats(matches, sess["id"])
    ranked_matches = await db.fetch_user_ranked_matches(sess["id"])
    ranked_stats = compute_ranked_stats(ranked_matches, sess["id"])
    char_ratings: Optional[list[dict[str, Any]]] = None
    if sess["rank"] == "ph":
        char_ratings = await ph_char_ratings_payload(sess["id"])
    return {
        "user": sess,
        **stats,
        "ranked": {
            "rank": sess["rank"],
            "rating": sess["rating"],
            **ranked_stats,
            "char_ratings": char_ratings,
        },
    }


def _prune_presence_local(now: float) -> None:
    cutoff = now - PRESENCE_TTL_SEC
    stale = [k for k, ts in PRESENCE_LOCAL.items() if ts < cutoff]
    for k in stale:
        del PRESENCE_LOCAL[k]


def _valid_visitor_id(raw: str | None) -> str | None:
    raw = (raw or "").strip()
    if _VISITOR_ID_RE.fullmatch(raw):
        return raw
    return None


def _new_visitor_id() -> str:
    return secrets.token_urlsafe(16)


def _resolve_visitor_id(request: Request) -> tuple[str, bool]:
    existing = _valid_visitor_id(request.cookies.get(VISITOR_COOKIE))
    if existing:
        return existing, False
    return _new_visitor_id(), True


def _set_visitor_cookie(response: Response, visitor_id: str) -> None:
    response.set_cookie(
        key=VISITOR_COOKIE,
        value=visitor_id,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=86400 * 30,
        path="/",
    )


async def _presence_touch(visitor_id: str) -> int:
    now = time.time()
    if post_redis.is_redis_configured():
        return await asyncio.to_thread(post_redis.presence_touch, visitor_id, now=now)
    with _PRESENCE_LOCK:
        PRESENCE_LOCAL[visitor_id] = now
        _prune_presence_local(now)
        return len(PRESENCE_LOCAL)


async def _presence_count() -> int:
    now = time.time()
    if post_redis.is_redis_configured():
        return await asyncio.to_thread(post_redis.presence_count, now=now)
    with _PRESENCE_LOCK:
        _prune_presence_local(now)
        return len(PRESENCE_LOCAL)


@app.post("/presence/heartbeat")
async def presence_heartbeat(request: Request) -> JSONResponse:
    """ページ閲覧中の訪問者を記録する (cookie + TTL)。"""
    visitor_id, set_cookie = _resolve_visitor_id(request)
    count = await _presence_touch(visitor_id)
    resp = JSONResponse({"ok": True, "count": count})
    if set_cookie:
        _set_visitor_cookie(resp, visitor_id)
    return resp


@app.get("/presence/count")
async def presence_count_endpoint() -> dict[str, Any]:
    """TTL 内に asobby.com を閲覧中の人数。"""
    count = await _presence_count()
    return {"ok": True, "count": count}


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/client/latest")
async def client_latest(
    request: Request,
    current: str = "",
) -> dict[str, Any]:
    """Web / クライアント向け: 最新リリース情報 (キャッシュ)。

    `current` クエリまたは `X-Asobby-Client-Version` ヘッダーで
    実行中バージョンを渡すと `update_available` / `outdated` を付与する。
    """
    info = await client_release.get_latest_release()
    if info is None:
        return {"ok": False}
    cur = (current or request.headers.get("X-Asobby-Client-Version") or "").strip()
    payload = client_release.update_info_for_current(info, cur)
    return {"ok": True, **payload}


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
async def auth_discord_web(next: str = "/", force: bool = False) -> RedirectResponse:
    """Web ページ閲覧用の Discord ログイン。完了後はクッキーセッションを発行する。"""
    require_discord_configured()
    cleanup_web_logins()
    # オープンリダイレクト防止: サイト内パスのみ許可
    if not next.startswith("/") or next.startswith("//"):
        next = "/"
    state = "w-" + secrets.token_urlsafe(24)
    WEB_LOGINS[state] = (time.time(), next)
    params = urlencode({
        "client_id": DISCORD_CLIENT_ID,
        "redirect_uri": f"{PUBLIC_BASE_URL}/auth/discord/callback",
        "response_type": "code",
        "scope": "identify",
        "state": state,
        "prompt": "select_account" if force else "none",
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
        return _login_result_page(ok=False, message_key="auth.cancelled")

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
            return _login_result_page(ok=False, message_key="auth.discordFailed")
        access_token = token_res.json().get("access_token", "")

        me_res = await http.get(
            DISCORD_ME_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if me_res.status_code != 200:
            if is_web:
                WEB_LOGINS.pop(state, None)
            return _login_result_page(ok=False, message_key="auth.discordUserFailed")
        me = me_res.json()

    user = {
        "id": str(me.get("id", "")),
        "name": str(me.get("global_name") or me.get("username") or ""),
    }
    avatar = str(me.get("avatar") or "")
    if not user["id"] or not user["name"]:
        if is_web:
            WEB_LOGINS.pop(state, None)
        return _login_result_page(ok=False, message_key="auth.discordUserInvalid")

    if is_web:
        _, next_path = WEB_LOGINS.pop(state, (0.0, "/"))
        user_row = await db.upsert_user_on_login(
            user["id"], user["name"], ip=client_ip(request), avatar=avatar
        )
        token = make_session_token(user, user_row.token_version)
        response = RedirectResponse(next_path or "/", status_code=302)
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
    return _login_result_page(
        ok=True,
        message_key="auth.loginSuccess",
        message_params={"name": user["name"]},
    )


@app.get("/auth/logout")
async def auth_logout(request: Request) -> RedirectResponse:
    revoke_session_token(session_token_from_request(request))
    response = RedirectResponse("/", status_code=302)
    clear_session_cookie(response)
    return response


@app.post("/auth/logout")
async def auth_logout_api(request: Request) -> dict[str, Any]:
    """クライアント Bearer セッションの失効 (Web クッキーは /auth/logout GET で別途削除)。"""
    token = session_token_from_request(request)
    if not token or verify_session_token(token) is None:
        raise HTTPException(status_code=401, detail="invalid or expired session")
    revoke_session_token(token)
    return {"ok": True}


def _login_result_page(
    *,
    ok: bool,
    message_key: str,
    message_params: dict[str, str] | None = None,
) -> HTMLResponse:
    import json

    color = "#57c07d" if ok else "#e06c75"
    heading_key = "auth.loginOk" if ok else "auth.loginFail"
    params_json = json.dumps(message_params or {}, ensure_ascii=False)
    return HTMLResponse(f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>asobby</title>
<script src="/static/i18n.js?v=0.5.1.1"></script></head>
<body style="background:#14171c;color:#d8dee9;font-family:sans-serif;
display:flex;align-items:center;justify-content:center;height:100vh;margin:0">
<div style="text-align:center">
<h1 id="heading" style="color:{color}"></h1>
<p id="message"></p>
<p id="close-hint" style="color:#7b8794"></p>
<button type="button" id="lang-toggle" style="margin-top:16px;padding:4px 10px;
background:transparent;color:#6ab0f3;border:1px solid #2c3440;border-radius:6px;cursor:pointer"></button>
</div>
<script>
const messageKey = {json.dumps(message_key, ensure_ascii=False)};
const messageParams = {params_json};
applyDocumentI18n();
document.getElementById("heading").textContent = t("{heading_key}");
document.getElementById("message").textContent = t(messageKey, messageParams);
document.getElementById("close-hint").textContent = t("auth.closeTab");
initLangToggle();
</script>
</body></html>""")


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
                await db.touch_user(
                    login.user["id"],
                    client_ip(request),
                    client_version=client_version_from_request(request),
                )
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
    settings = await db.get_user_settings(sess["id"])
    prefs = settings["favicon_notify"]
    badges = compute_favicon_badges(
        sess["id"],
        sess.get("name") or "",
        sess.get("rank") or "",
        prefs,
    )
    return {
        **sess,
        "settings": settings,
        "favicon_badges": badges,
        "has_other_posts": badges["ranked"] or badges["casual"],
    }


@app.get("/user/settings")
async def get_user_settings(request: Request) -> dict[str, Any]:
    sess = await resolve_session(request)
    if sess is None:
        raise HTTPException(status_code=401, detail="login required")
    return await db.get_user_settings(sess["id"])


@app.patch("/user/settings")
async def patch_user_settings(
    body: UserSettingsPatchIn, request: Request
) -> dict[str, Any]:
    sess = await resolve_session(request)
    if sess is None:
        raise HTTPException(status_code=401, detail="login required")
    if not db.is_configured():
        raise HTTPException(status_code=503, detail="database not configured")
    patch: dict[str, Any] = {}
    if body.favicon_notify is not None:
        patch["favicon_notify"] = body.favicon_notify.model_dump(exclude_none=True)
    if body.replay_refusal_until is not None:
        patch["replay_refusal_until"] = body.replay_refusal_until
    try:
        return await db.update_user_settings(sess["id"], patch)
    except ValueError:
        raise HTTPException(status_code=404, detail="user not found")


@app.get("/auth/client/handoff")
async def auth_client_handoff(
    request: Request,
    port: int,
    force: bool = False,
) -> RedirectResponse:
    """ブラウザのクッキーセッションをクライアントへ引き渡す。

    クライアントは 127.0.0.1:{port} で待ち受けてからこの URL をブラウザで開く。
    Web 側でログイン済みならワンタイムコード付きで即 localhost へリダイレクトされ、
    未ログインなら Discord ログインを挟んでからここへ戻ってくる。
    force=1 のときは既存 Web セッションを破棄し Discord アカウント選択を表示する。
    """
    require_discord_configured()
    cleanup_handoff_codes()
    if not (1024 <= port <= 65535):
        raise HTTPException(status_code=400, detail="invalid port")

    if force:
        next_url = quote(f"/auth/client/handoff?port={port}", safe="")
        response = RedirectResponse(
            f"/auth/discord/web?next={next_url}&force=1", status_code=302
        )
        revoke_session_token(session_token_from_request(request))
        clear_session_cookie(response)
        return response

    sess = await resolve_session(request)
    if sess is None:
        next_url = quote(f"/auth/client/handoff?port={port}", safe="")
        return RedirectResponse(
            f"/auth/discord/web?next={next_url}", status_code=302
        )

    code = secrets.token_urlsafe(32)
    CLIENT_HANDOFF_CODES[code] = HandoffCode(
        user_id=sess["id"], created_at=time.time()
    )
    return RedirectResponse(
        f"http://127.0.0.1:{port}/auth?code={quote(code, safe='')}",
        status_code=302,
    )


@app.post("/auth/client/exchange")
async def auth_client_exchange(body: ClientExchangeIn, request: Request) -> dict[str, Any]:
    """ワンタイムコードをクライアントのセッショントークンに交換する。"""
    cleanup_handoff_codes()
    handoff = CLIENT_HANDOFF_CODES.pop(body.code, None)
    if handoff is None:
        raise HTTPException(status_code=404, detail="code expired")

    user_row = await db.get_user(handoff.user_id)
    if user_row is None:
        raise HTTPException(status_code=404, detail="user not found")

    # 交換リクエストはクライアント本体 (IPv4 強制) から来るので IP を記録する
    await db.touch_user(
        user_row.id,
        client_ip(request),
        client_version=client_version_from_request(request),
    )
    user = {"id": user_row.id, "name": user_row.name}
    return {
        "status": "ok",
        "session_token": make_session_token(user, user_row.token_version),
        "user": user,
    }


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
async def list_posts(request: Request) -> list[dict[str, Any]]:
    # 募集一覧 (カジュアル/ランクマとも) の閲覧はログイン必須
    if await resolve_session(request) is None:
        raise HTTPException(status_code=401, detail="login required")
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

    check_addr = probe_addr_for_hostcheck(body.addr, fallback_host=ip)
    direct_reachable, autopunch_effective, reachability_uncertain = await verify_hostable_or_raise(
        check_addr, autopunch=body.autopunch, giuroll=body.giuroll
    )

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
        autopunch=autopunch_effective,
        direct_reachable=direct_reachable,
        reachability_uncertain=reachability_uncertain,
        match_status=body.match_status,
        net_status=body.net_status,
        ping_warn_enabled=body.ping_warn_enabled,
        ping_warn_ms=body.ping_warn_ms,
        ping_warn_giuroll_ms=body.ping_warn_giuroll_ms,
        owner_name=owner_name,
        owner_avatar=owner_avatar,
        updated_at=now,
        created_at=now,
    )
    geoip.apply_country_from_addr(post, addr=post.addr)
    rec = PostRecord(
        post=post,
        owner_token=secrets.token_urlsafe(24),
        creator_ip=ip,
        owner_user_id=owner_user_id,
        last_hostcheck_at=now,
    )
    RECORDS[post.id] = rec

    await _persist_record(rec)
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
    now = now_ts()

    reverify = should_reverify_host_on_update(
        rec,
        addr=body.addr,
        net_status=body.net_status,
        now=now,
    )
    if reverify:
        addr_changed = body.addr != rec.post.addr
        giuroll = bool(body.giuroll or p.giuroll)
        was_ap_only = not p.direct_reachable and (p.autopunch or body.autopunch)
        check_addr = probe_addr_for_hostcheck(body.addr, fallback_host=rec.creator_ip)
        try:
            direct_reachable, autopunch_effective, reachability_uncertain = await verify_hostable_or_raise(
                check_addr,
                autopunch=body.autopunch,
                ap_check=addr_changed,
                consecutive=True,
                giuroll=giuroll,
            )
        except HTTPException as exc:
            # heartbeat 再検証の一時的な失敗 (giuroll 等) で募集を落とさない
            if exc.status_code == 409 and not addr_changed:
                pass  # direct_reachable / autopunch は前回値を維持
            else:
                raise
        else:
            if (
                was_ap_only
                and not addr_changed
                and direct_reachable
                and not autopunch_effective
            ):
                # heartbeat 再検証の direct 誤判定で AP-only 表示を消さない
                p.autopunch = True
                p.direct_reachable = False
                p.reachability_uncertain = (
                    reachability_uncertain or p.reachability_uncertain
                )
            else:
                p.direct_reachable = direct_reachable
                p.autopunch = autopunch_effective
                p.reachability_uncertain = reachability_uncertain
        rec.last_hostcheck_at = now

    if body.autopunch and not p.direct_reachable:
        p.autopunch = True
        p.reachability_uncertain = True

    p.post_type = body.post_type
    p.ping_warn_enabled = body.ping_warn_enabled
    p.ping_warn_ms = body.ping_warn_ms
    p.ping_warn_giuroll_ms = body.ping_warn_giuroll_ms
    p.addr = body.addr
    p.comment = body.comment
    p.stream_url = body.stream_url
    p.giuroll = body.giuroll
    p.match_status = body.match_status
    p.net_status = body.net_status
    p.updated_at = now
    geoip.apply_country_from_addr(p, addr=p.addr)
    if rec.guest_user_id:
        refresh_ranked_active(rec)

    messages = list(rec.pending_messages)
    rec.pending_messages.clear()
    ping_warnings = list(rec.pending_ping_warnings)
    rec.pending_ping_warnings.clear()
    data = asdict(p)
    data["messages"] = messages
    data["ping_warnings"] = ping_warnings
    await _persist_record(rec)
    await HUB.publish("upsert", asdict(p))
    return data


@app.post("/posts/{post_id}/message")
async def post_message(post_id: str, body: PostMessageIn, request: Request) -> dict[str, Any]:
    """Web ロビー閲覧者がホストへ定型メッセージを送る。"""
    sess = await resolve_session(request)
    if sess is None:
        raise HTTPException(status_code=401, detail="invalid or expired session")

    rec = RECORDS.get(post_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="post not found")

    if rec.owner_user_id == sess["id"]:
        raise HTTPException(status_code=400, detail="cannot message your own post")

    post = rec.post
    if body.type == "giuroll_request":
        if post.giuroll:
            raise HTTPException(status_code=409, detail="giuroll is already enabled")
    elif body.type == "casual_invite":
        if post.post_type != "ranked":
            raise HTTPException(status_code=409, detail="not a ranked post")

    now = now_ts()
    cooldown_key = (sess["id"], post_id)
    last_sent = MESSAGE_LAST_SENT.get(cooldown_key, 0.0)
    elapsed = now - last_sent
    if elapsed < MESSAGE_COOLDOWN_SEC:
        retry_after = int(MESSAGE_COOLDOWN_SEC - elapsed + 0.999)
        raise HTTPException(
            status_code=429,
            detail="please wait before sending another message",
            headers={"Retry-After": str(retry_after)},
        )

    message_id = uuid4().hex
    rec.pending_messages.append({
        "id": message_id,
        "type": body.type,
        "from_name": sess["name"],
        "from_user_id": sess["id"],
        "sent_at": now,
    })
    if len(rec.pending_messages) > MESSAGE_MAX_PENDING:
        rec.pending_messages = rec.pending_messages[-MESSAGE_MAX_PENDING:]

    if body.type in ("giuroll_request", "casual_invite"):
        rec.sent_log[message_id] = {
            "from_user_id": sess["id"],
            "from_name": sess["name"],
            "type": body.type,
            "replied": False,
        }
        while len(rec.sent_log) > MESSAGE_SENT_LOG_MAX:
            oldest = next(iter(rec.sent_log))
            del rec.sent_log[oldest]

    MESSAGE_LAST_SENT[cooldown_key] = now
    await _persist_record(rec)
    return {"ok": True, "cooldown_sec": int(MESSAGE_COOLDOWN_SEC)}


@app.post("/posts/{post_id}/ping-report")
async def report_high_ping(
    post_id: str,
    body: PingReportIn,
    request: Request,
) -> dict[str, Any]:
    """閲覧者のローカル Ping 計測が閾値以上のとき、ホストへ警告をキューする。"""
    sess = await resolve_session(request)
    if sess is None:
        raise HTTPException(status_code=401, detail="invalid or expired session")

    rec = RECORDS.get(post_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="post not found")

    if rec.owner_user_id == sess["id"]:
        raise HTTPException(status_code=400, detail="cannot report ping to your own post")

    if not rec.post.ping_warn_enabled:
        raise HTTPException(status_code=409, detail="ping warnings disabled for this post")

    if not rec.post.guest_connected:
        raise HTTPException(status_code=409, detail="host has no connected guest")

    if not viewer_may_report_ping(rec, sess["id"], client_ip(request)):
        raise HTTPException(status_code=403, detail="only the connected guest may report ping")

    threshold = post_ping_warn_threshold(rec.post)
    if body.rtt_ms < threshold:
        raise HTTPException(status_code=422, detail="rtt below warning threshold")

    now = now_ts()
    cooldown_key = (sess["id"], post_id)
    last_sent = PING_REPORT_LAST.get(cooldown_key, 0.0)
    elapsed = now - last_sent
    if elapsed < PING_REPORT_COOLDOWN_SEC:
        retry_after = int(PING_REPORT_COOLDOWN_SEC - elapsed + 0.999)
        raise HTTPException(
            status_code=429,
            detail="please wait before reporting ping again",
            headers={"Retry-After": str(retry_after)},
        )

    rec.pending_ping_warnings.append({
        "from_user_id": sess["id"],
        "from_name": sess["name"],
        "rtt_ms": body.rtt_ms,
        "threshold_ms": threshold,
        "sent_at": now,
    })
    if len(rec.pending_ping_warnings) > PING_WARN_MAX_PENDING:
        rec.pending_ping_warnings = rec.pending_ping_warnings[-PING_WARN_MAX_PENDING:]

    PING_REPORT_LAST[cooldown_key] = now
    await _persist_record(rec)
    return {"ok": True, "cooldown_sec": int(PING_REPORT_COOLDOWN_SEC)}


@app.post("/posts/reply")
async def reply_post_message(body: PostReplyIn) -> dict[str, Any]:
    """ホストが閲覧者からのリクエストメッセージへ承諾/拒否を返す。"""
    rec = get_record_or_raise(body.id, body.owner_token)
    entry = rec.sent_log.get(body.message_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="message not found")
    if entry.get("replied"):
        raise HTTPException(status_code=409, detail="already replied")

    entry["replied"] = True
    await _persist_record(rec)
    await HUB.publish(
        "message_reply",
        {
            "to_user_id": entry["from_user_id"],
            "from_name": rec.post.owner_name,
            "req_type": entry["type"],
            "reply": body.reply,
            "ts": now_ts(),
        },
    )
    return {"ok": True}


@app.post("/posts/close")
async def close_post(body: ClosePostIn) -> dict[str, Any]:
    _ = get_record_or_raise(body.id, body.owner_token)

    del RECORDS[body.id]
    cleanup_message_state_for_post(body.id)
    await _delete_persisted_post(body.id)
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
async def upload_replay(
    request: Request,
    battle_ts: float = 0,
    host_profile: str = "",
    guest_profile: str = "",
    winner: str = "",
    my_side: str = "",
) -> dict[str, Any]:
    """ログインユーザーの直近対戦リプレイを受け取る。

    battle_ts (unix 秒; 対戦終了時刻) が付いていればその時刻の周辺で
    match を照合する。時刻で絞らないと、リトライや同定遅れの際に
    別の古い match へ誤紐付けされる。"""
    sess = await resolve_session(request)
    if sess is None:
        raise HTTPException(status_code=401, detail="invalid or expired session")

    data = await request.body()
    if not data:
        raise HTTPException(status_code=422, detail="empty body")
    if len(data) > REPLAY_MAX_BYTES:
        raise HTTPException(status_code=413, detail="replay too large")

    content_sha256 = db.replay_content_sha256(data)
    if await db.find_replay_by_content_hash(content_sha256) is not None:
        return {"ok": True, "stored": False, "reason": "duplicate"}

    uploader_settings = await db.get_user_settings(sess["id"])
    if db.is_replay_refusal_active(uploader_settings["replay_refusal_until"]):
        return {"ok": True, "stored": False, "reason": "refused"}

    now = db.utcnow()
    around = now
    if battle_ts > 0:
        try:
            candidate = datetime.fromtimestamp(battle_ts, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            candidate = now
        # 過去24時間以内かつ未来でなければ採用
        if now - timedelta(hours=24) <= candidate <= now + timedelta(minutes=5):
            around = candidate

    # battle_ts なし (旧クライアント) はアップロード遅延分だけ窓を広げる
    window = 180 if battle_ts > 0 else 240
    match = await db.find_match_for_replay(sess["id"], around, window_sec=window)
    if match is None and host_profile and guest_profile and winner in ("host", "guest"):
        match = await db.find_match_for_replay_by_profiles(
            sess["id"],
            around,
            window_sec=window,
            host_profile=host_profile,
            guest_profile=guest_profile,
            winner=winner,
            my_side=my_side,
        )
    if match is None:
        recent = await db.find_match_for_replay(
            sess["id"], around, window_sec=window, require_no_replay=False
        )
        if recent is not None and await db.replay_count_for_match(recent.id) > 0:
            return {"ok": True, "stored": False, "reason": "duplicate"}
        return {"ok": True, "stored": False, "reason": "no_match"}

    if await db.match_replay_blocked(match):
        return {"ok": True, "stored": False, "reason": "refused"}

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
    played_at = _resolve_report_played_at(body.played_at)

    # ゲスト未同定の host 報告と重複しないようプロファイルでも照合する
    near = await _find_dedup_match(
        played_at,
        body.winner,
        body.host_profile,
        body.guest_profile,
        my_side="guest",
    )
    if near is not None:
        await db.claim_match_side(near.id, "guest", uid)
        return {
            "ok": True,
            "recorded": False,
            "reason": "duplicate",
            "match_id": near.id,
        }

    near_profiles = await db.find_near_match_profiles_only(
        played_at, body.host_profile, body.guest_profile
    )
    if near_profiles is not None:
        await db.claim_match_side(near_profiles.id, "guest", uid)
        return {
            "ok": True,
            "recorded": False,
            "reason": "duplicate",
            "match_id": near_profiles.id,
        }

    match_id = await db.insert_match_result(
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
        played_at=played_at,
    )
    ranked_session = _guest_ranked_session_rank(uid, client_ip(request))
    return {
        "ok": True,
        "recorded": True,
        "match_id": match_id,
        "ranked_session": ranked_session,
    }


@app.post("/posts/result")
async def report_result(body: ReportResultIn) -> dict[str, Any]:
    rec = get_record_or_raise(body.id, body.owner_token)
    if not db.is_configured():
        return {"ok": True, "recorded": False}

    played_at = _resolve_report_played_at(body.played_at)

    post = rec.post
    host_ip, _, _ = post.addr.partition(":")

    # 接続時点で同定できなかったゲストを再試行する
    # (対戦中にゲストがログインして last_ip が付いた場合など)
    await _assign_guest_from_ip(rec, guest_profile=body.guest_profile)

    near_for_gate = None
    if not rec.guest_user_id:
        near_for_gate = await _find_dedup_match(
            played_at,
            body.winner,
            body.host_profile,
            body.guest_profile,
            my_side="host",
        )
        if near_for_gate is not None and near_for_gate.guest_user_id:
            rec.guest_user_id = near_for_gate.guest_user_id
            guest_user = await db.get_user(near_for_gate.guest_user_id)
            if guest_user is not None:
                rec.guest_rank = guest_user.rank

    can_record = bool(
        rec.guest_ip
        or rec.guest_user_id
        or (
            near_for_gate is not None
            and near_for_gate.source in ("guest", "sync")
        )
    )
    if not can_record:
        return {"ok": True, "recorded": False}

    refresh_ranked_active(rec)
    limit_reached = await ranked_session_limit_reached(
        rec.owner_user_id,
        guest_user_id=rec.guest_user_id or None,
        host_profile=body.host_profile,
        guest_profile=body.guest_profile,
        match_rank=post.rank,
        played_at=played_at,
        session_games=rec.session_games,
    )
    is_ranked = post.ranked_active and not limit_reached
    match_rank = post.rank if is_ranked else None

    match_id: Optional[str] = None
    promoted = False
    newly_recorded = False
    if rec.guest_user_id:
        guest_match = await db.find_recent_guest_reported_match(
            rec.guest_user_id,
            winner=body.winner,
            host_profile=body.host_profile,
            guest_profile=body.guest_profile,
            played_at=played_at,
        )
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
                match_rank=match_rank,
                played_at=played_at,
            )
            match_id = guest_match.id
            promoted = True
            newly_recorded = True

    if not promoted:
        near = await _find_dedup_match(
            played_at,
            body.winner,
            body.host_profile,
            body.guest_profile,
            my_side="host",
        )
        if near is not None:
            if near.source in ("sync", "guest"):
                await db.promote_guest_match(
                    near.id,
                    host_user_id=rec.owner_user_id,
                    host_ip=host_ip,
                    winner=body.winner,
                    host_char=body.host_char,
                    guest_char=body.guest_char,
                    host_profile=body.host_profile,
                    guest_profile=body.guest_profile,
                    ranked=is_ranked,
                    match_rank=match_rank,
                    played_at=played_at,
                )
                if rec.guest_user_id:
                    await db.claim_match_side(near.id, "guest", rec.guest_user_id)
                match_id = near.id
                promoted = True
                newly_recorded = True
            elif near.host_user_id == rec.owner_user_id:
                if rec.guest_user_id:
                    await db.claim_match_side(near.id, "guest", rec.guest_user_id)
                match_id = near.id
                promoted = True

    if not promoted:
        match_id = await db.insert_match_result(
            host_user_id=rec.owner_user_id,
            guest_user_id=rec.guest_user_id or None,
            host_ip=host_ip,
            guest_ip=rec.guest_ip or "",
            winner=body.winner,
            host_char=body.host_char,
            guest_char=body.guest_char,
            host_profile=body.host_profile,
            guest_profile=body.guest_profile,
            ranked=is_ranked,
            match_rank=match_rank,
            source="host",
            played_at=played_at,
        )
        newly_recorded = True

    if newly_recorded:
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
                    body.host_char,
                    body.guest_char,
                )

    await _persist_record(rec)
    return {
        "ok": True,
        "recorded": True,
        "ranked": is_ranked,
        "match_id": match_id,
        "match_rank": match_rank,
        "duplicate": promoted and not newly_recorded,
    }


@app.post("/posts/upsert")
async def legacy_upsert() -> None:
    raise HTTPException(
        status_code=410,
        detail="this client version is no longer supported; please update asobby",
    )


@app.get("/lobby/players/suggest")
async def suggest_lobby_players(
    request: Request,
    q: str = "",
    limit: int = 10,
) -> dict[str, Any]:
    """ロビーチャットの @ メンション補完 (Discord ユーザー名)。"""
    if await resolve_session(request) is None:
        raise HTTPException(status_code=401, detail="invalid or expired session")
    if not db.is_configured():
        return {"ok": True, "suggestions": []}
    player_q = q.strip()
    if not player_q:
        return {"ok": True, "suggestions": []}
    page_limit = min(max(1, limit), 20)
    users = await db.suggest_users_by_name(player_q, page_limit)
    suggestions = [
        {
            "kind": "user",
            "name": user.name,
            "user_id": user.id,
            "avatar": user.avatar or None,
        }
        for user in users
    ]
    return {"ok": True, "suggestions": suggestions}


@app.post("/lobby/chat")
async def post_lobby_chat(body: ChatMessageIn, request: Request) -> dict[str, Any]:
    sess = await resolve_session(request)
    if sess is None:
        raise HTTPException(status_code=401, detail="invalid or expired session")

    text = _validate_chat_text(body.text)
    uid = sess["id"]
    now = time.time()
    if post_redis.is_configured():
        remaining = post_redis.chat_cooldown_remaining(uid)
        if remaining > 0:
            raise HTTPException(
                status_code=429,
                detail="please wait before sending another message",
                headers={"Retry-After": str(int(remaining) + 1)},
            )
    else:
        last = LOBBY_CHAT_LAST_SENT.get(uid, 0.0)
        if (now - last) < LOBBY_CHAT_COOLDOWN_SEC:
            retry_after = int(LOBBY_CHAT_COOLDOWN_SEC - (now - last) + 0.999)
            raise HTTPException(
                status_code=429,
                detail="please wait before sending another message",
                headers={"Retry-After": str(retry_after)},
            )

    lang = _normalize_chat_lang(body.lang)
    mentions: list[dict[str, str]] = []
    if db.is_configured():
        for user in await db.users_mentioned_in_text(text):
            mentions.append({"user_id": user.id, "name": user.name})

    msg = {
        "id": uuid4().hex,
        "user_id": uid,
        "name": sess["name"],
        "avatar": sess.get("avatar") or "",
        "text": text,
        "mentions": mentions,
        "lang": lang,
        "ts": now,
    }
    LOBBY_CHATS[lang].append(msg)
    if post_redis.is_configured():
        await asyncio.to_thread(
            post_redis.append_chat_message,
            msg,
            max_messages=LOBBY_CHAT_MAX_MESSAGES,
        )
        await asyncio.to_thread(post_redis.chat_cooldown_mark, uid, LOBBY_CHAT_COOLDOWN_SEC)
    else:
        LOBBY_CHAT_LAST_SENT[uid] = now
    if _trim_lobby_chat_by_age():
        await _sync_chat_to_redis()
    await HUB.publish("chat_message", msg)
    return {"ok": True, "message": msg}


@app.get("/sse/posts")
async def sse_posts(request: Request):
    # 募集一覧の閲覧はログイン必須
    if await resolve_session(request) is None:
        raise HTTPException(status_code=401, detail="login required")
    q = await HUB.subscribe()

    async def gen():
        try:
            # 購読開始後にスナップショットを送ることで、接続直後の
            # イベント取りこぼしをなくす（重複 upsert は冪等なので無害）。
            yield format_sse("snapshot", sorted_public_posts())
            yield format_sse("chat_snapshot", lobby_chat_snapshot())

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


def parse_probe_addr(post: Post) -> Optional[tuple[str, int]]:
    try:
        host, port_s = post.addr.rsplit(":", 1)
        port = int(port_s)
        if not (0 < port < 65536):
            return None
        socket.inet_aton(host)
    except (ValueError, OSError):
        return None
    return host, port


def host_connection_in_progress(
    rec: PostRecord,
    *,
    net_status: int | None = None,
) -> bool:
    """ゲスト接続〜対戦中は UDP プローブを避ける。"""
    post = rec.post
    ns = getattr(post, "net_status", NET_ALIVE) if net_status is None else net_status
    if ns in (NET_CHECKING, NET_BATTLE):
        return True
    return bool(getattr(post, "guest_connected", False) or getattr(rec, "guest_ip", ""))


def probe_target_records() -> list[PostRecord]:
    out: list[PostRecord] = []
    for rec in RECORDS.values():
        if RECORDS.get(rec.post.id) is not rec:
            continue
        if parse_probe_addr(rec.post) is None:
            continue
        if host_connection_in_progress(rec):
            continue
        out.append(rec)
    out.sort(key=lambda rec: rec.post.id)
    return out


def guest_probe_tick_sleep_sec(post_count: int) -> float:
    if post_count <= 0:
        return GUEST_PROBE_ROUND_SEC
    return max(GUEST_PROBE_MIN_TICK_SEC, GUEST_PROBE_ROUND_SEC / post_count)


def probe_post_status(
    host: str,
    port: int,
    autopunch: bool,
    *,
    timeout_sec: float = GUEST_PROBE_TIMEOUT_SEC,
) -> Optional[bytes]:
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
            timeout_sec=timeout_sec,
        )


async def apply_guest_probe(rec: PostRecord, reply: Optional[bytes]) -> None:
    """プローブ応答を PostRecord に反映し、必要なら SSE を更新する。"""
    if reply is None:
        return

    post = rec.post
    matched = parse_matched_client(reply)

    if matched is None:
        if (
            len(reply) >= 1
            and reply[0] == 0x07
            and rec.guest_ip
            and rec.post.net_status not in (NET_CHECKING, NET_BATTLE)
        ):
            rec.guest_ip = ""
            rec.guest_user_id = ""
            rec.guest_rank = ""
            rec.session_games = 0
            post.guest_name = ""
            post.guest_avatar = ""
            post.guest_user_id = ""
            post.guest_connected = False
            post.ranked_active = False
            await _persist_record(rec)
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
    if user is not None and user.id == rec.owner_user_id:
        # 同一 IP (同一 NAT 内など) でホスト自身に同定された場合は
        # 「自分対自分」を避けるため未同定として扱う
        user = None

    if user is not None:
        rec.guest_user_id = user.id
        rec.guest_rank = user.rank
        post.guest_user_id = user.id
        post.guest_name = user.name
        post.guest_avatar = discord_avatar_url(user.id, user.avatar)
    else:
        post.guest_user_id = ""
        post.guest_name = ""
        post.guest_avatar = ""

    post.guest_connected = True
    refresh_ranked_active(rec)

    await _persist_record(rec)
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


def check_hostable_once(
    host: str,
    port: int,
    *,
    should_match: bool = False,
    profile_name: str = "asobby",
    timeout_sec: float = 0.2,
) -> bool:
    """単発 soku echo で到達性を確認する (heartbeat 再検証向け)。"""
    packet = soku_echo_packet(
        should_match=should_match,
        profile_name=profile_name,
    )
    with _probe_lock:
        reply = probe_host_once(
            host,
            port,
            packet,
            timeout_sec=timeout_sec,
        )
    return reply is not None and is_valid_reply(reply)


def compute_reachability_uncertain(
    *,
    direct_reachable: bool,
    autopunch: bool,
    ap_verified: bool = True,
) -> bool:
    """AP 必須ホストで接続可否が保証できないか。

    ポート制限 NAT など直結不可の環境は AP 経由でも相手から繋がらないことがある。
    AP-only (direct 不可) の募集には ❓ を付ける。AP 検証をスキップした場合も ❓。
    """
    if direct_reachable or not autopunch:
        return False
    if not ap_verified:
        return True
    return True  # AP-only はポート制限 NAT 等を含め接続保証なし


def check_hostable_autopunch(host: str, port: int) -> tuple[bool, bool]:
    """AutoPunch リレー経由でホスト登録と soku echo を検証する。

    Returns (reachable, fully_verified). fail-open 時は (True, False)。
    """
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
                return True, False

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
                return True, False

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
                return False, True

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
                            return True, True
                except OSError:
                    pass

            return False, True
        except OSError:
            return False, True
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


def probe_addr_for_hostcheck(addr: str, *, fallback_host: str = "") -> str:
    """到達性プローブ用アドレス。0.0.0.0 はクライアント未取得時の placeholder。"""
    host, port = parse_ipv4_addr_or_raise(addr)
    fb = (fallback_host or "").strip()
    if host == "0.0.0.0" and fb:
        try:
            socket.inet_aton(fb)
        except OSError:
            return addr
        else:
            host = fb
    return f"{host}:{port}"


def should_reverify_host_on_update(
    rec: PostRecord,
    *,
    addr: str,
    net_status: int,
    now: float,
    interval_sec: float = HOSTCHECK_UPDATE_INTERVAL_SEC,
) -> bool:
    """募集 heartbeat 更新時に到達性を再検証すべきか。"""
    if not HOSTCHECK_ENABLED:
        return False
    if host_connection_in_progress(rec, net_status=net_status):
        return False
    if addr != rec.post.addr:
        return True
    if (
        SERVER_STARTED_AT > 0
        and (now - SERVER_STARTED_AT) < HOSTCHECK_STARTUP_GRACE_SEC
    ):
        return False
    return now - rec.last_hostcheck_at >= interval_sec


def host_probe_kwargs(*, giuroll: bool = False) -> dict[str, float | int]:
    """giuroll ホストは UDP echo が不安定になりやすいので probe を緩める。"""
    if giuroll:
        return {
            "attempts": 8,
            "interval_sec": 0.12,
            "timeout_sec": 0.35,
            "needed_consecutive": 1,
        }
    return {
        "attempts": 6,
        "interval_sec": 0.1,
        "timeout_sec": 0.25,
        "needed_consecutive": 2,
    }


async def verify_hostable_or_raise(
    addr: str,
    *,
    autopunch: bool = False,
    ap_check: bool = True,
    consecutive: bool = True,
    giuroll: bool = False,
) -> tuple[bool, bool, bool]:
    """到達性を検証する。

    戻り値は (direct_reachable, autopunch, reachability_uncertain)。
    直接 UDP で届かず AutoPunch リレー経由のみ到達できるホストは
    autopunch=True, direct_reachable=False になる。
    """
    host, port = parse_ipv4_addr_or_raise(addr)

    if not HOSTCHECK_ENABLED:
        uncertain = compute_reachability_uncertain(
            direct_reachable=True,
            autopunch=bool(autopunch),
            ap_verified=True,
        )
        return True, bool(autopunch), uncertain

    probe_kwargs = host_probe_kwargs(giuroll=giuroll)
    strict_kwargs = {
        **probe_kwargs,
        "needed_consecutive": 3,
        "attempts": max(8, int(probe_kwargs["attempts"])),
    }

    async def run_direct(kwargs: dict[str, float | int]) -> bool:
        if consecutive:
            return await asyncio.to_thread(
                check_hostable_consecutive,
                host,
                port,
                **kwargs,
            )
        return await asyncio.to_thread(
            check_hostable_once,
            host,
            port,
            timeout_sec=float(kwargs["timeout_sec"]),
        )

    direct_lenient = await run_direct(probe_kwargs)
    direct_strict = (
        await run_direct(strict_kwargs) if direct_lenient else False
    )
    ap_ok, ap_verified = await asyncio.to_thread(check_hostable_autopunch, host, port)

    if direct_strict:
        return True, False, False

    if ap_ok:
        uncertain = compute_reachability_uncertain(
            direct_reachable=False,
            autopunch=True,
            ap_verified=ap_verified,
        )
        return False, True, uncertain

    # lenient direct だけでは direct 到達と判定しない (AP 必須なのに REQUIRE 空になるのを防ぐ)
    if autopunch and ap_check:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "autopunch host not reachable (is autopunch running?)",
            },
        )

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


if DATA_DIR.is_dir():

    @app.get("/data", include_in_schema=False)
    async def data_redirect() -> RedirectResponse:
        return RedirectResponse("/data/", status_code=301)

    app.mount("/data", StaticFiles(directory=DATA_DIR, html=True), name="data")

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
