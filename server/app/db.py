"""永続化レイヤー (PostgreSQL / SQLAlchemy 2.0 async)。

永続化するのはユーザー・戦績・リプレイのみ。募集投稿は TTL 20 秒の
揮発データなので従来どおりインメモリ (main.py) に置く。
"""
from __future__ import annotations

import hashlib
import ipaddress
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, LargeBinary, SmallInteger, String, and_, exists, func, or_, select, union
from sqlalchemy.orm import aliased
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# プロファイル一致による近傍 dedup の時刻窓 (秒)
MATCH_DEDUP_WINDOW_SEC = 45


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    # Discord のユーザー ID (snowflake)。名前変更でも不変なので主キーにする。
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), default="")
    avatar: Mapped[str] = mapped_column(String(64), default="")
    # トークン失効管理: この値を上げると発行済みセッションが全て無効になる
    token_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    # 最後に確認したクライアント IP (echo パケットでの対戦相手照合に使う)
    last_ip: Mapped[str] = mapped_column(String(64), default="", index=True)
    # ランクマッチ: easy / normal / ex / hard / luna / ph
    rank: Mapped[str] = mapped_column(String(8), default="normal", nullable=False)
    rank_changed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # 初回開始ランク選択済み、またはランクマ 1 戦目以降は True
    rank_locked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    ts_mu: Mapped[float] = mapped_column(Float, default=25.0, nullable=False)
    ts_sigma: Mapped[float] = mapped_column(Float, default=8.333333333333334, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    last_login_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # 最後に確認した asobby クライアント版 (X-Asobby-Client-Version)
    client_version: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    settings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


PH_CHAR_IDS: tuple[int, ...] = tuple(range(21))  # 0–19 + Random (20)
DEFAULT_TS_MU = 25.0
DEFAULT_TS_SIGMA = 8.333333333333334

REPLAY_REFUSAL_INACTIVE = 0.0
REPLAY_REFUSAL_PERMANENT = -1.0

DEFAULT_FAVICON_NOTIFY: dict[str, Any] = {
    "ranked_enabled": True,
    "casual_enabled": True,
    "ranked_same_band_only": True,
    "max_ping_ms": 60,
    "require_ping": False,
    "exclude_in_battle": True,
}


def normalize_favicon_notify(raw: Any) -> dict[str, Any]:
    src = raw if isinstance(raw, dict) else {}
    out = dict(DEFAULT_FAVICON_NOTIFY)
    for key in DEFAULT_FAVICON_NOTIFY:
        if key not in src:
            continue
        val = src[key]
        if key == "max_ping_ms":
            try:
                out[key] = max(1, min(999, int(val)))
            except (TypeError, ValueError):
                pass
        elif key in (
            "ranked_enabled",
            "casual_enabled",
            "ranked_same_band_only",
            "require_ping",
            "exclude_in_battle",
        ):
            out[key] = bool(val)
    return out


def normalize_replay_refusal_until(raw: Any) -> float:
    if raw is None:
        return REPLAY_REFUSAL_INACTIVE
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return REPLAY_REFUSAL_INACTIVE
    if value == REPLAY_REFUSAL_PERMANENT:
        return REPLAY_REFUSAL_PERMANENT
    if value <= 0:
        return REPLAY_REFUSAL_INACTIVE
    return value


def is_replay_refusal_active(until: float, *, now: float | None = None) -> bool:
    value = normalize_replay_refusal_until(until)
    if value == REPLAY_REFUSAL_INACTIVE:
        return False
    if value == REPLAY_REFUSAL_PERMANENT:
        return True
    now = time.time() if now is None else now
    return now < value


def normalize_user_settings(raw: Any) -> dict[str, Any]:
    src = raw if isinstance(raw, dict) else {}
    favicon_raw = src.get("favicon_notify")
    return {
        "favicon_notify": normalize_favicon_notify(favicon_raw),
        "replay_refusal_until": normalize_replay_refusal_until(
            src.get("replay_refusal_until")
        ),
    }


class UserCharRating(Base):
    __tablename__ = "user_char_ratings"

    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id"), primary_key=True
    )
    char_id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    ts_mu: Mapped[float] = mapped_column(Float, default=DEFAULT_TS_MU, nullable=False)
    ts_sigma: Mapped[float] = mapped_column(
        Float, default=DEFAULT_TS_SIGMA, nullable=False
    )


class Match(Base):
    __tablename__ = "matches"

    id: Mapped[str] = mapped_column(
        String(32), primary_key=True, default=lambda: uuid4().hex
    )
    host_user_id: Mapped[Optional[str]] = mapped_column(
        String(32), ForeignKey("users.id"), nullable=True, index=True
    )
    guest_user_id: Mapped[Optional[str]] = mapped_column(
        String(32), ForeignKey("users.id"), nullable=True, index=True
    )
    # 対戦時の両者の IP。echo パケットで得たゲスト IP を users.last_ip と
    # 照合して guest_user_id を同定する際の根拠として残す。
    host_ip: Mapped[str] = mapped_column(String(64), default="")
    guest_ip: Mapped[str] = mapped_column(String(64), default="")
    # "host" / "guest" / "draw" / ""(未確定)
    winner: Mapped[str] = mapped_column(String(8), default="")
    host_char: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True)
    guest_char: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True)
    host_profile: Mapped[str] = mapped_column(String(64), default="")
    guest_profile: Mapped[str] = mapped_column(String(64), default="")
    ranked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # ランクマ成立時のランク帯 (easy / normal / ex / hard / luna / ph)
    match_rank: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    # "host" = ホスト報告 (正)、 "guest" = ゲスト補完報告
    source: Mapped[str] = mapped_column(String(8), default="host", server_default="host")
    played_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class Replay(Base):
    __tablename__ = "replays"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    match_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("matches.id", ondelete="CASCADE"), nullable=False, index=True
    )
    filename: Mapped[str] = mapped_column(String(255), default="")
    size: Mapped[int] = mapped_column(Integer, default=0)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    # リプレイは 100KB 程度なので bytea で DB に直接持つ
    data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


# ----------------------------
# Engine / session
# ----------------------------
def normalize_db_url(url: str) -> tuple[str, dict[str, Any]]:
    """DATABASE_URL を SQLAlchemy + asyncpg 用に正規化する。

    - postgres:// / postgresql:// → postgresql+asyncpg://
    - Neon 等が付ける sslmode / channel_binding クエリは asyncpg が
      受け付けないので connect_args の ssl に変換する。
    """
    parts = urlsplit(url)
    scheme = parts.scheme
    connect_args: dict[str, Any] = {}

    if scheme in ("postgres", "postgresql"):
        scheme = "postgresql+asyncpg"
    if not scheme.startswith("postgresql+asyncpg"):
        # sqlite+aiosqlite 等はそのまま
        return url, connect_args

    query = dict(parse_qsl(parts.query))
    sslmode = query.pop("sslmode", "")
    query.pop("channel_binding", None)
    if sslmode and sslmode != "disable":
        connect_args["ssl"] = sslmode  # asyncpg は 'require' 等の文字列を受け付ける

    return (
        urlunsplit((scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)),
        connect_args,
    )


def make_engine(url: str) -> AsyncEngine:
    sa_url, connect_args = normalize_db_url(url)
    return create_async_engine(sa_url, connect_args=connect_args, pool_pre_ping=True)


_engine: Optional[AsyncEngine] = None
_sessionmaker: Optional[async_sessionmaker[AsyncSession]] = None


def init_engine(url: str) -> AsyncEngine:
    global _engine, _sessionmaker
    _engine = make_engine(url)
    _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def is_configured() -> bool:
    return _sessionmaker is not None


def session() -> AsyncSession:
    assert _sessionmaker is not None, "db.init_engine() not called"
    return _sessionmaker()


async def dispose() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None


# ----------------------------
# User helpers
# ----------------------------
def _ipv4_or_empty(ip: str) -> str:
    """IPv4 のみ受け付ける。last_ip は th123 の echo プローブで得た
    ゲスト IPv4 と照合するため、IPv6 (Web ブラウザ経由で混入しうる) は
    保存せず既存値を保持する。"""
    try:
        ipaddress.IPv4Address(ip)
        return ip
    except (ipaddress.AddressValueError, ValueError):
        return ""


async def upsert_user_on_login(
    user_id: str, name: str, ip: str, avatar: str = ""
) -> User:
    """ログイン完了時のユーザー登録/更新。IP が変わっていれば更新する。"""
    ip = _ipv4_or_empty(ip)
    async with session() as s:
        user = await s.get(User, user_id)
        now = utcnow()
        if user is None:
            user = User(
                id=user_id,
                name=name,
                avatar=avatar,
                token_version=1,
                last_ip=ip,
                created_at=now,
                last_login_at=now,
                last_seen_at=now,
            )
            s.add(user)
        else:
            user.name = name
            user.avatar = avatar
            if ip and user.last_ip != ip:
                user.last_ip = ip
            user.last_login_at = now
            user.last_seen_at = now
        await s.commit()
        return user


async def get_user_if_token_valid(user_id: str, token_version: int) -> Optional[User]:
    """トークンの ver が DB と一致するユーザーを返す。無効なら None。"""
    async with session() as s:
        user = await s.get(User, user_id)
        if user is None or user.token_version != token_version:
            return None
        return user


async def touch_user(
    user_id: str, ip: str, *, client_version: str = ""
) -> None:
    """認証済みリクエスト時に last_seen / IP / クライアント版を最新化する。"""
    ip = _ipv4_or_empty(ip)
    ver = (client_version or "").strip()[:32]
    async with session() as s:
        user = await s.get(User, user_id)
        if user is None:
            return
        user.last_seen_at = utcnow()
        if ip and user.last_ip != ip:
            user.last_ip = ip
        if ver:
            user.client_version = ver
        await s.commit()


async def get_user(user_id: str) -> Optional[User]:
    async with session() as s:
        return await s.get(User, user_id)


async def get_user_settings(user_id: str) -> dict[str, Any]:
    async with session() as s:
        user = await s.get(User, user_id)
        if user is None:
            return normalize_user_settings({})
        return normalize_user_settings(user.settings or {})


async def match_replay_blocked(match: Match) -> bool:
    """Either participant refusing replay upload blocks storage for both."""
    async with session() as s:
        checked: set[str] = set()
        candidate_ids: list[str] = []
        for uid in (match.host_user_id, match.guest_user_id):
            if uid:
                candidate_ids.append(uid)
        for ip in (match.host_ip, match.guest_ip):
            if not ip:
                continue
            res = await s.execute(
                select(User)
                .where(User.last_ip == ip)
                .order_by(User.last_seen_at.desc())
                .limit(1)
            )
            user = res.scalar_one_or_none()
            if user is not None and user.id not in candidate_ids:
                candidate_ids.append(user.id)
        for uid in candidate_ids:
            if uid in checked:
                continue
            checked.add(uid)
            user = await s.get(User, uid)
            if user is None:
                continue
            settings = normalize_user_settings(user.settings or {})
            if is_replay_refusal_active(settings["replay_refusal_until"]):
                return True
    return False


async def update_user_settings(user_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    async with session() as s:
        user = await s.get(User, user_id)
        if user is None:
            raise ValueError("user not found")
        current = normalize_user_settings(user.settings or {})
        favicon_patch = patch.get("favicon_notify")
        if isinstance(favicon_patch, dict):
            merged = dict(current["favicon_notify"])
            for key in DEFAULT_FAVICON_NOTIFY:
                if key in favicon_patch:
                    merged[key] = favicon_patch[key]
            current["favicon_notify"] = normalize_favicon_notify(merged)
        if "replay_refusal_until" in patch:
            current["replay_refusal_until"] = normalize_replay_refusal_until(
                patch["replay_refusal_until"]
            )
        user.settings = current
        await s.commit()
        return current


async def find_user_by_ip(ip: str) -> Optional[User]:
    """IP からユーザーを引く (echo パケットで得た対戦相手 IP の照合用)。
    複数一致した場合は最後に見かけたユーザーを返す。"""
    async with session() as s:
        res = await s.execute(
            select(User)
            .where(User.last_ip == ip)
            .order_by(User.last_seen_at.desc())
            .limit(1)
        )
        return res.scalar_one_or_none()


async def find_user_by_ip_and_rank(ip: str, rank: str) -> Optional[User]:
    """IP とランク帯の両方が一致するユーザーを返す (共有 NAT 向け)。"""
    async with session() as s:
        res = await s.execute(
            select(User)
            .where(User.last_ip == ip, User.rank == rank)
            .order_by(User.last_seen_at.desc())
            .limit(1)
        )
        return res.scalar_one_or_none()


async def get_user_rank(user_id: str) -> tuple[str, float, float] | None:
    """ユーザーの (rank, ts_mu, ts_sigma) を返す。"""
    async with session() as s:
        user = await s.get(User, user_id)
        if user is None:
            return None
        return user.rank, user.ts_mu, user.ts_sigma


async def choose_initial_rank(user_id: str, rank: str) -> bool:
    """初回のみ開始ランクを設定する。rank_locked が False のときだけ成功。"""
    async with session() as s:
        user = await s.get(User, user_id)
        if user is None or user.rank_locked:
            return False
        user.rank = rank
        user.rank_changed_at = utcnow()
        user.rank_locked = True
        await s.commit()
        return True


async def lock_user_rank(user_id: str) -> None:
    """開始ランク選択をロックする（冪等）。"""
    async with session() as s:
        user = await s.get(User, user_id)
        if user is None:
            return
        user.rank_locked = True
        await s.commit()


async def set_user_rank(user_id: str, new_rank: str) -> None:
    """ランクを更新し rank_changed_at を現在時刻にセットする。"""
    mu = DEFAULT_TS_MU
    sigma = DEFAULT_TS_SIGMA
    async with session() as s:
        user = await s.get(User, user_id)
        if user is None:
            return
        user.rank = new_rank
        user.rank_changed_at = utcnow()
        mu = user.ts_mu
        sigma = user.ts_sigma
        await s.commit()
    if new_rank == "ph":
        await init_ph_char_ratings(user_id, mu, sigma)


async def init_ph_char_ratings(
    user_id: str,
    mu: float = DEFAULT_TS_MU,
    sigma: float = DEFAULT_TS_SIGMA,
) -> None:
    """Ph 到達時に全キャラ (0–19 + Random) へ TrueSkill 初期値を付与する。"""
    async with session() as s:
        for char_id in PH_CHAR_IDS:
            row = await s.get(UserCharRating, (user_id, char_id))
            if row is None:
                s.add(
                    UserCharRating(
                        user_id=user_id,
                        char_id=char_id,
                        ts_mu=mu,
                        ts_sigma=sigma,
                    )
                )
        await s.commit()


async def get_char_rating(user_id: str, char_id: int) -> tuple[float, float] | None:
    async with session() as s:
        row = await s.get(UserCharRating, (user_id, char_id))
        if row is None:
            return None
        return row.ts_mu, row.ts_sigma


async def set_char_rating(user_id: str, char_id: int, mu: float, sigma: float) -> None:
    async with session() as s:
        row = await s.get(UserCharRating, (user_id, char_id))
        if row is None:
            s.add(
                UserCharRating(
                    user_id=user_id,
                    char_id=char_id,
                    ts_mu=mu,
                    ts_sigma=sigma,
                )
            )
        else:
            row.ts_mu = mu
            row.ts_sigma = sigma
        await s.commit()


async def list_char_ratings(user_id: str) -> list[tuple[int, float, float]]:
    async with session() as s:
        res = await s.execute(
            select(UserCharRating)
            .where(UserCharRating.user_id == user_id)
            .order_by(UserCharRating.char_id.asc())
        )
        return [(r.char_id, r.ts_mu, r.ts_sigma) for r in res.scalars().all()]


async def sync_user_aggregate_rating(user_id: str) -> None:
    """全キャラレートの平均を users.ts_mu / ts_sigma に反映する (ロビー表示用)。"""
    ratings = await list_char_ratings(user_id)
    if not ratings:
        return
    n = len(ratings)
    avg_mu = sum(mu for _cid, mu, _sigma in ratings) / n
    avg_sigma = sum(sigma for _cid, _mu, sigma in ratings) / n
    await set_user_rating(user_id, avg_mu, avg_sigma)


async def set_user_rating(user_id: str, mu: float, sigma: float) -> None:
    """TrueSkill レート (mu, sigma) を更新する。"""
    async with session() as s:
        user = await s.get(User, user_id)
        if user is None:
            return
        user.ts_mu = mu
        user.ts_sigma = sigma
        await s.commit()


async def insert_match_result(
    host_user_id: Optional[str],
    guest_user_id: Optional[str],
    host_ip: str,
    guest_ip: str,
    winner: str,
    *,
    host_char: Optional[int] = None,
    guest_char: Optional[int] = None,
    host_profile: str = "",
    guest_profile: str = "",
    ranked: bool = False,
    match_rank: Optional[str] = None,
    source: str = "host",
    played_at: Optional[datetime] = None,
) -> str:
    """対戦結果を matches に新規 insert する。insert した行の id を返す。"""
    async with session() as s:
        match = Match(
            host_user_id=host_user_id,
            guest_user_id=guest_user_id,
            host_ip=host_ip,
            guest_ip=guest_ip,
            winner=winner,
            host_char=host_char,
            guest_char=guest_char,
            host_profile=host_profile,
            guest_profile=guest_profile,
            ranked=ranked,
            match_rank=match_rank,
            source=source,
            played_at=played_at if played_at is not None else utcnow(),
        )
        s.add(match)
        await s.commit()
        return match.id


async def ip_guest_identification_ok(
    user_id: str, guest_profile: str
) -> bool:
    """IP 同定した user が guest_profile として整合するか (別名プロファイル歴があれば拒否)。"""
    if not guest_profile:
        return True
    async with session() as s:
        res = await s.execute(
            select(Match.id)
            .where(
                (Match.guest_user_id == user_id)
                & (Match.guest_profile == guest_profile)
            )
            .limit(1)
        )
        if res.scalar_one_or_none() is not None:
            return True
        res = await s.execute(
            select(Match.id)
            .where(
                (Match.guest_user_id == user_id)
                & (Match.guest_profile != "")
                & (Match.guest_profile != guest_profile)
            )
            .limit(1)
        )
        return res.scalar_one_or_none() is None


async def find_near_match_by_profiles(
    played_at: datetime,
    winner: str,
    host_profile: str,
    guest_profile: str,
    window_sec: int = MATCH_DEDUP_WINDOW_SEC,
) -> Optional[Match]:
    """勝敗・両プロファイル・時刻 (±window_sec) が一致する match を 1 件返す。

    ゲストが echo プローブで同定できなかった場合、host 報告にはゲストの
    user_id が付かず、user_id ベースの重複排除をすり抜けて同一対戦が
    二重登録される。その保険としてプロファイルで照合する。"""
    if not host_profile or not guest_profile:
        return None
    lo = played_at - timedelta(seconds=window_sec)
    hi = played_at + timedelta(seconds=window_sec)
    async with session() as s:
        res = await s.execute(
            select(Match)
            .where(
                (Match.winner == winner)
                & (Match.host_profile == host_profile)
                & (Match.guest_profile == guest_profile)
                & Match.played_at.is_not(None)
                & (Match.played_at >= lo)
                & (Match.played_at <= hi)
            )
            .order_by(Match.played_at.desc())
            .limit(1)
        )
        return res.scalar_one_or_none()


async def find_near_match_profiles_only(
    played_at: datetime,
    host_profile: str,
    guest_profile: str,
    window_sec: int = MATCH_DEDUP_WINDOW_SEC,
) -> Optional[Match]:
    """勝敗を問わずプロファイルと時刻が近い match を返す (ゲスト補完報告用)。"""
    if not host_profile or not guest_profile:
        return None
    lo = played_at - timedelta(seconds=window_sec)
    hi = played_at + timedelta(seconds=window_sec)
    async with session() as s:
        res = await s.execute(
            select(Match)
            .where(
                (Match.host_profile == host_profile)
                & (Match.guest_profile == guest_profile)
                & Match.played_at.is_not(None)
                & (Match.played_at >= lo)
                & (Match.played_at <= hi)
            )
            .order_by(Match.played_at.desc())
            .limit(1)
        )
        return res.scalar_one_or_none()


async def find_mergeable_profile_match(
    winner: str,
    host_profile: str,
    guest_profile: str,
    *,
    within_sec: int = 600,
    missing_side: Literal["host", "guest"],
) -> Optional[Match]:
    """played_at がズレた guest/sync 行をプロファイルで照合する (時刻非依存フォールバック)。

    ホストとゲストで KO 時刻が異なる・guest 報告が utcnow() になる等で
    find_near_match_by_profiles が外れた場合の保険。source=guest/sync のみ対象。
    missing_side は今回の報告で埋めようとしている側 (未同定の側のみ照合)。"""
    if not host_profile or not guest_profile:
        return None
    side_null = (
        Match.host_user_id.is_(None)
        if missing_side == "host"
        else Match.guest_user_id.is_(None)
    )
    cutoff = utcnow() - timedelta(seconds=within_sec)
    async with session() as s:
        res = await s.execute(
            select(Match)
            .where(
                (Match.winner == winner)
                & (Match.host_profile == host_profile)
                & (Match.guest_profile == guest_profile)
                & (Match.source.in_(("guest", "sync")))
                & Match.played_at.is_not(None)
                & (Match.played_at >= cutoff)
                & side_null
            )
            .order_by(Match.played_at.desc())
            .limit(1)
        )
        return res.scalar_one_or_none()


async def claim_match_side(match_id: str, side: str, user_id: str) -> bool:
    """match の host/guest が未同定なら user_id を紐付ける。

    プロファイル照合で重複と判定した場合、相手側報告 (ゲスト未同定など)
    に自分の user_id を補完して戦績ページに反映させる。"""
    async with session() as s:
        match = await s.get(Match, match_id)
        if match is None:
            return False
        if side == "host":
            if match.host_user_id or match.guest_user_id == user_id:
                return False
            match.host_user_id = user_id
        else:
            if match.guest_user_id or match.host_user_id == user_id:
                return False
            match.guest_user_id = user_id
        await s.commit()
        return True


async def find_recent_guest_reported_match(
    guest_user_id: str,
    *,
    winner: str,
    host_profile: str,
    guest_profile: str,
    played_at: Optional[datetime] = None,
    within_sec: int = 180,
) -> Optional[Match]:
    """直近のゲスト報告 (source=='guest') を返す。プロファイル一致優先、単一候補のみフォールバック。"""
    cutoff = utcnow() - timedelta(seconds=within_sec)
    async with session() as s:
        res = await s.execute(
            select(Match)
            .where(
                (Match.guest_user_id == guest_user_id)
                & (Match.source == "guest")
                & Match.played_at.is_not(None)
                & (Match.played_at >= cutoff)
            )
            .order_by(Match.played_at.desc())
        )
        candidates = list(res.scalars().all())

    if not candidates:
        return None

    if winner:
        winner_matches = [m for m in candidates if m.winner == winner]
        if winner_matches:
            candidates = winner_matches

    if host_profile and guest_profile:
        for match in candidates:
            if (
                match.host_profile == host_profile
                and match.guest_profile == guest_profile
            ):
                return match
        if played_at is not None:
            lo = played_at - timedelta(seconds=MATCH_DEDUP_WINDOW_SEC)
            hi = played_at + timedelta(seconds=MATCH_DEDUP_WINDOW_SEC)
            for match in candidates:
                if match.played_at is None:
                    continue
                pa = match.played_at
                if pa.tzinfo is None:
                    pa = pa.replace(tzinfo=timezone.utc)
                if lo <= pa <= hi:
                    return match
        if len(candidates) == 1:
            return candidates[0]
        return None

    return candidates[0]


async def promote_guest_match(
    match_id: str,
    *,
    host_user_id: str,
    host_ip: str,
    winner: str,
    host_char: Optional[int],
    guest_char: Optional[int],
    host_profile: str,
    guest_profile: str,
    ranked: bool,
    match_rank: Optional[str] = None,
    played_at: Optional[datetime] = None,
) -> Optional[Match]:
    """ゲスト報告行をホスト報告に昇格する (delete+insert 禁止)。更新後の Match を返す。"""
    async with session() as s:
        match = await s.get(Match, match_id)
        if match is None:
            return None
        match.host_user_id = host_user_id
        match.host_ip = host_ip
        match.winner = winner
        match.host_char = host_char
        match.guest_char = guest_char
        match.host_profile = host_profile
        match.guest_profile = guest_profile
        match.ranked = ranked
        match.match_rank = match_rank
        match.source = "host"
        if played_at is not None:
            match.played_at = played_at
        await s.commit()
        return match


def _ranked_streak_from_matches(
    matches: list[Match], *, gap_minutes: int
) -> int:
    """played_at 降順の ranked 試合から、gap 超で区切った連続本数を数える。"""
    gap_sec = gap_minutes * 60
    count = 0
    prev_ts: Optional[float] = None
    for m in matches:
        if m.played_at is None:
            break
        ts = m.played_at.timestamp()
        if m.played_at.tzinfo is None:
            ts = m.played_at.replace(tzinfo=timezone.utc).timestamp()
        if prev_ts is not None and prev_ts - ts > gap_sec:
            break
        count += 1
        prev_ts = ts
    return count


async def count_ranked_pair_streak_before(
    user_a: str,
    user_b: str,
    *,
    before: datetime,
    match_rank: Optional[str] = None,
    gap_minutes: int = 30,
) -> int:
    """ログイン済み 2 人の連続ランクマ (ホスト/クライアント入れ替え含む)。"""
    if not user_a or not user_b or user_a == user_b:
        return 0
    pair = (
        ((Match.host_user_id == user_a) & (Match.guest_user_id == user_b))
        | ((Match.host_user_id == user_b) & (Match.guest_user_id == user_a))
    )
    clauses = [
        pair,
        Match.winner != "",
        Match.ranked.is_(True),
        Match.played_at.is_not(None),
        Match.played_at < before,
    ]
    if match_rank:
        clauses.append(Match.match_rank == match_rank)

    async with session() as s:
        res = await s.execute(
            select(Match)
            .where(*clauses)
            .order_by(Match.played_at.desc())
            .limit(10)
        )
        matches = list(res.scalars().all())

    return _ranked_streak_from_matches(matches, gap_minutes=gap_minutes)


async def count_ranked_streak_before(
    host_user_id: str,
    *,
    host_profile: str,
    guest_profile: str,
    guest_user_id: Optional[str],
    match_rank: Optional[str],
    before: datetime,
    gap_minutes: int = 30,
) -> int:
    """anchor より前の同一対戦相手との連続ランクマ試合数 (30 分超の空白で区切る)。"""
    if guest_user_id:
        return await count_ranked_pair_streak_before(
            host_user_id,
            guest_user_id,
            before=before,
            match_rank=match_rank,
            gap_minutes=gap_minutes,
        )

    profile_pair = (
        ((Match.host_profile == host_profile) & (Match.guest_profile == guest_profile))
        | ((Match.host_profile == guest_profile) & (Match.guest_profile == host_profile))
    )
    clauses = [
        Match.winner != "",
        Match.ranked.is_(True),
        Match.played_at.is_not(None),
        Match.played_at < before,
        profile_pair,
        (Match.host_user_id == host_user_id) | (Match.guest_user_id == host_user_id),
    ]
    if match_rank:
        clauses.append(Match.match_rank == match_rank)

    async with session() as s:
        res = await s.execute(
            select(Match)
            .where(*clauses)
            .order_by(Match.played_at.desc())
            .limit(10)
        )
        matches = list(res.scalars().all())

    return _ranked_streak_from_matches(matches, gap_minutes=gap_minutes)


async def find_guest_user_id_by_profiles(
    host_profile: str,
    guest_profile: str,
    *,
    within_sec: int = 3600,
) -> Optional[str]:
    """プロファイル一致の直近 match から guest_user_id を推定する (向き反転・時間窓付き)。"""
    if not host_profile or not guest_profile:
        return None
    cutoff = utcnow() - timedelta(seconds=within_sec)
    profile_pair = (
        ((Match.host_profile == host_profile) & (Match.guest_profile == guest_profile))
        | ((Match.host_profile == guest_profile) & (Match.guest_profile == host_profile))
    )
    async with session() as s:
        res = await s.execute(
            select(Match)
            .where(
                profile_pair,
                Match.winner != "",
                Match.played_at.is_not(None),
                Match.played_at >= cutoff,
            )
            .order_by(Match.played_at.desc())
            .limit(1)
        )
        match = res.scalar_one_or_none()
        if match is None:
            return None
        if match.host_profile == host_profile and match.guest_profile == guest_profile:
            return str(match.guest_user_id) if match.guest_user_id else None
        if match.host_profile == guest_profile and match.guest_profile == host_profile:
            return str(match.host_user_id) if match.host_user_id else None
        return None


async def fetch_user_matches(user_id: str, limit: int = 1000) -> list[Match]:
    """ユーザーの確定済み対戦を played_at 降順で返す。"""
    async with session() as s:
        res = await s.execute(
            select(Match)
            .where(
                ((Match.host_user_id == user_id) | (Match.guest_user_id == user_id))
                & (Match.winner != "")
            )
            .order_by(Match.played_at.desc())
            .limit(limit)
        )
        return list(res.scalars().all())


async def fetch_user_ranked_matches(user_id: str, limit: int = 1000) -> list[Match]:
    """ユーザーの確定済みランクマ対戦を played_at 降順で返す。"""
    async with session() as s:
        res = await s.execute(
            select(Match)
            .where(
                ((Match.host_user_id == user_id) | (Match.guest_user_id == user_id))
                & (Match.winner != "")
                & Match.ranked.is_(True)
            )
            .order_by(Match.played_at.desc())
            .limit(limit)
        )
        return list(res.scalars().all())


async def fetch_ranked_matches_at_current_rank(
    user_id: str, limit: int = 30
) -> list[Match]:
    """現ランク期間中のランクマ対戦 (直近 limit 件) を played_at 降順で返す。"""
    async with session() as s:
        user = await s.get(User, user_id)
        if user is None:
            return []
        q = (
            select(Match)
            .where(
                ((Match.host_user_id == user_id) | (Match.guest_user_id == user_id))
                & (Match.winner != "")
                & Match.ranked.is_(True)
            )
            .order_by(Match.played_at.desc())
            .limit(limit)
        )
        if user.rank_changed_at is not None:
            q = q.where(Match.played_at >= user.rank_changed_at)
        res = await s.execute(q)
        return list(res.scalars().all())


async def find_match_for_replay(
    user_id: str,
    around_ts: datetime,
    window_sec: int = 90,
    *,
    require_no_replay: bool = True,
) -> Optional[Match]:
    """リプレイの対戦終了時刻に最も近い match を返す。

    リプレイは対戦終了直後にアップロードされるため、時刻で照合しないと
    古い match (直前の対戦など) に誤紐付けされる。"""
    lo = around_ts - timedelta(seconds=window_sec)
    hi = around_ts + timedelta(seconds=window_sec)
    replay_exists = exists().where(Replay.match_id == Match.id)
    async with session() as s:
        q = (
            select(Match)
            .where(
                ((Match.host_user_id == user_id) | (Match.guest_user_id == user_id))
                & (Match.winner != "")
                & Match.played_at.is_not(None)
                & (Match.played_at >= lo)
                & (Match.played_at <= hi)
            )
            .order_by(Match.played_at.desc())
            .limit(1)
        )
        if require_no_replay:
            q = q.where(~replay_exists)
        res = await s.execute(q)
        return res.scalar_one_or_none()


async def find_match_for_replay_by_profiles(
    user_id: str,
    around_ts: datetime,
    window_sec: int,
    *,
    host_profile: str,
    guest_profile: str,
    winner: str,
    my_side: str,
) -> Optional[Match]:
    """user_id 一致で見つからない場合、プロファイル一致の未リプレイ match を返す。

    ゲスト報告が先行し host_user_id が未設定の行に、ホスト側リプレイを
    紐付けるためのフォールバック。"""
    near = await find_near_match_by_profiles(
        around_ts, winner, host_profile, guest_profile, window_sec=window_sec
    )
    if near is None:
        return None
    if await replay_count_for_match(near.id) > 0:
        return None
    if near.host_user_id == user_id or near.guest_user_id == user_id:
        return near
    if my_side == "host" and not near.host_user_id:
        if await claim_match_side(near.id, "host", user_id):
            async with session() as s:
                return await s.get(Match, near.id)
    if my_side == "client" and not near.guest_user_id:
        if await claim_match_side(near.id, "guest", user_id):
            async with session() as s:
                return await s.get(Match, near.id)
    return None


def replay_content_sha256(data: bytes) -> str:
    """リプレイ生データの SHA-256 (hex)。"""
    return hashlib.sha256(data).hexdigest()


async def find_replay_by_content_hash(content_sha256: str) -> Optional[Replay]:
    """同一内容のリプレイが既に登録されていれば返す。"""
    async with session() as s:
        res = await s.execute(
            select(Replay).where(Replay.content_sha256 == content_sha256).limit(1)
        )
        return res.scalar_one_or_none()


async def insert_replay(match_id: str, filename: str, data: bytes) -> bool:
    """リプレイを insert する。unique 制約違反時は False。"""
    content_sha256 = replay_content_sha256(data)
    async with session() as s:
        try:
            replay = Replay(
                match_id=match_id,
                filename=filename,
                size=len(data),
                content_sha256=content_sha256,
                data=data,
            )
            s.add(replay)
            await s.commit()
            return True
        except IntegrityError:
            await s.rollback()
            return False


async def replay_count_for_match(match_id: str) -> int:
    """テスト用: match に紐づくリプレイ件数。"""
    async with session() as s:
        res = await s.execute(
            select(Replay).where(Replay.match_id == match_id)
        )
        return len(list(res.scalars().all()))


async def filter_existing_match_ids(ids: list[str]) -> set[str]:
    """matches に既に存在する id を一括取得する (IN 句は 1000 件ずつ)。"""
    if not ids:
        return set()
    found: set[str] = set()
    async with session() as s:
        for i in range(0, len(ids), 1000):
            chunk = ids[i : i + 1000]
            res = await s.execute(select(Match.id).where(Match.id.in_(chunk)))
            found.update(res.scalars().all())
    return found


async def fetch_user_match_times(
    user_id: str, exclude_source: str = "import"
) -> list[datetime]:
    """ユーザーの確定済み対戦の played_at を昇順で返す (天則観インポートの重複判定用)。"""
    async with session() as s:
        res = await s.execute(
            select(Match.played_at)
            .where(
                ((Match.host_user_id == user_id) | (Match.guest_user_id == user_id))
                & (Match.winner != "")
                & Match.played_at.is_not(None)
                & (Match.source != exclude_source)
            )
            .order_by(Match.played_at.asc())
        )
        return [t for t in res.scalars().all() if t is not None]


async def bulk_insert_matches(rows: list[dict]) -> int:
    """matches を一括 insert する (500 件ずつ commit)。挿入件数を返す。"""
    if not rows:
        return 0
    inserted = 0
    async with session() as s:
        for i in range(0, len(rows), 500):
            chunk = rows[i : i + 500]
            s.add_all(Match(**row) for row in chunk)
            await s.commit()
            inserted += len(chunk)
    return inserted


async def fetch_user_matches_since(
    user_id: str,
    since_ts: float = 0.0,
    limit: int = 500,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> list[tuple[Match, bool]]:
    """ユーザーの確定済み対戦を played_at 昇順で返す。(Match, has_replay) のリスト。"""
    since_dt = datetime.fromtimestamp(since_ts, tz=timezone.utc)
    replay_exists = exists().where(Replay.match_id == Match.id)
    clauses = [
        ((Match.host_user_id == user_id) | (Match.guest_user_id == user_id)),
        (Match.winner != ""),
        Match.played_at.is_not(None),
        (Match.played_at > since_dt),
    ]
    if date_from is not None:
        clauses.append(Match.played_at >= date_from)
    if date_to is not None:
        clauses.append(Match.played_at <= date_to)
    async with session() as s:
        res = await s.execute(
            select(Match, replay_exists.label("has_replay"))
            .where(*clauses)
            .order_by(Match.played_at.asc())
            .limit(limit)
        )
        return [(row[0], bool(row[1])) for row in res.all()]


async def count_user_matches(user_id: str) -> int:
    """ユーザーの確定済み対戦数 (キャッシュ整合性チェック用)。"""
    from sqlalchemy import func

    async with session() as s:
        res = await s.execute(
            select(func.count())
            .select_from(Match)
            .where(
                ((Match.host_user_id == user_id) | (Match.guest_user_id == user_id))
                & (Match.winner != "")
                & Match.played_at.is_not(None)
            )
        )
        return int(res.scalar_one())


async def get_match_by_id(match_id: str) -> Optional[Match]:
    """match id で 1 件取得する。"""
    async with session() as s:
        return await s.get(Match, match_id)


async def get_replay_for_match(match_id: str) -> Optional[Replay]:
    """match に紐づくリプレイを 1 件返す (unique 想定)。"""
    async with session() as s:
        res = await s.execute(
            select(Replay).where(Replay.match_id == match_id).limit(1)
        )
        return res.scalar_one_or_none()


async def suggest_users_by_name(q: str, limit: int = 10) -> list[User]:
    """Discord ログイン済みユーザー名の部分一致候補 (ロビーチャットメンション用)。"""
    needle = q.strip().lower()
    if not needle:
        return []
    async with session() as s:
        res = await s.execute(
            select(User)
            .where(func.lower(User.name).contains(needle))
            .order_by(User.name.asc())
            .limit(limit)
        )
        return list(res.scalars().all())


async def users_mentioned_in_text(text: str) -> list[User]:
    """テキスト中の @Name を users.name と照合する (長い名前を優先)。"""
    if "@" not in text:
        return []
    lower = text.lower()
    async with session() as s:
        res = await s.execute(select(User).order_by(func.length(User.name).desc()))
        users = list(res.scalars().all())
    matched: list[User] = []
    seen: set[str] = set()
    for user in users:
        if not user.name:
            continue
        needle = f"@{user.name}".lower()
        if needle in lower and user.id not in seen:
            matched.append(user)
            seen.add(user.id)
    return matched


async def suggest_replay_players(
    q: str,
    limit: int = 10,
) -> tuple[list[User], list[str]]:
    """リプレイ付き match に登場するプレイヤー名候補を返す。

    (users, profile_names) のタプル。それぞれ最大 limit 件、名前昇順。
    """
    needle = q.lower()

    replay_user_ids = (
        select(Match.host_user_id.label("uid"))
        .join(Replay, Replay.match_id == Match.id)
        .where(Match.host_user_id.is_not(None))
        .union(
            select(Match.guest_user_id.label("uid"))
            .join(Replay, Replay.match_id == Match.id)
            .where(Match.guest_user_id.is_not(None))
        )
    ).subquery()

    async with session() as s:
        user_res = await s.execute(
            select(User)
            .where(
                User.id.in_(select(replay_user_ids.c.uid)),
                func.lower(User.name).contains(needle),
            )
            .order_by(User.name.asc())
            .limit(limit)
        )
        users = list(user_res.scalars().all())

        host_profiles = (
            select(Match.host_profile.label("name"))
            .join(Replay, Replay.match_id == Match.id)
            .where(
                Match.played_at.is_not(None),
                Match.host_profile != "",
                func.lower(Match.host_profile).contains(needle),
            )
        )
        guest_profiles = (
            select(Match.guest_profile.label("name"))
            .join(Replay, Replay.match_id == Match.id)
            .where(
                Match.played_at.is_not(None),
                Match.guest_profile != "",
                func.lower(Match.guest_profile).contains(needle),
            )
        )
        profiles_union = union(host_profiles, guest_profiles).subquery()
        profile_res = await s.execute(
            select(profiles_union.c.name)
            .distinct()
            .order_by(profiles_union.c.name.asc())
            .limit(limit)
        )
        profiles = [row[0] for row in profile_res.all()]

    return users, profiles


async def search_replay_matches(
    *,
    player: Optional[str] = None,
    char1: Optional[int] = None,
    char2: Optional[int] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    limit: int = 1000,
) -> list[tuple[Match, str, Optional[User], Optional[User]]]:
    """リプレイ付き match を検索する。(Match, filename, host_user, guest_user) のリスト。"""
    HostUser = aliased(User)
    GuestUser = aliased(User)

    q = (
        select(Match, Replay.filename, HostUser, GuestUser)
        .join(Replay, Replay.match_id == Match.id)
        .outerjoin(HostUser, Match.host_user_id == HostUser.id)
        .outerjoin(GuestUser, Match.guest_user_id == GuestUser.id)
        .where(Match.played_at.is_not(None))
    )

    if player:
        needle = player.lower()
        q = q.where(
            or_(
                func.lower(Match.host_profile).contains(needle),
                func.lower(Match.guest_profile).contains(needle),
                func.lower(HostUser.name).contains(needle),
                func.lower(GuestUser.name).contains(needle),
            )
        )

    if char1 is not None:
        if char2 is not None:
            q = q.where(
                or_(
                    and_(Match.host_char == char1, Match.guest_char == char2),
                    and_(Match.host_char == char2, Match.guest_char == char1),
                )
            )
        else:
            q = q.where(
                or_(Match.host_char == char1, Match.guest_char == char1)
            )

    if date_from is not None:
        q = q.where(Match.played_at >= date_from)
    if date_to is not None:
        q = q.where(Match.played_at <= date_to)

    q = q.order_by(Match.played_at.desc()).limit(limit)

    async with session() as s:
        res = await s.execute(q)
        return [(row[0], row[1], row[2], row[3]) for row in res.all()]


async def fetch_user_match_times_with_ids(
    user_id: str, *, exclude_source: Optional[str] = None
) -> list[tuple[float, str]]:
    """ユーザーの確定済み対戦 (played_at 昇順) を (unix_ts, match_id) で返す。"""
    async with session() as s:
        q = (
            select(Match.played_at, Match.id)
            .where(
                ((Match.host_user_id == user_id) | (Match.guest_user_id == user_id))
                & (Match.winner != "")
                & Match.played_at.is_not(None)
            )
            .order_by(Match.played_at.asc())
        )
        if exclude_source is not None:
            q = q.where(Match.source != exclude_source)
        res = await s.execute(q)
        out: list[tuple[float, str]] = []
        for played_at, match_id in res.all():
            if played_at is None:
                continue
            ts = played_at.timestamp()
            if played_at.tzinfo is None:
                ts = played_at.replace(tzinfo=timezone.utc).timestamp()
            out.append((ts, match_id))
        return out
