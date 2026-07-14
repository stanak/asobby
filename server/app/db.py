"""永続化レイヤー (PostgreSQL / SQLAlchemy 2.0 async)。

永続化するのはユーザー・戦績・リプレイのみ。募集投稿は TTL 20 秒の
揮発データなので従来どおりインメモリ (main.py) に置く。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, LargeBinary, SmallInteger, String, and_, exists, func, or_, select
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
async def upsert_user_on_login(
    user_id: str, name: str, ip: str, avatar: str = ""
) -> User:
    """ログイン完了時のユーザー登録/更新。IP が変わっていれば更新する。"""
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


async def touch_user(user_id: str, ip: str) -> None:
    """認証済みリクエスト時に last_seen と IP を最新化する。"""
    async with session() as s:
        user = await s.get(User, user_id)
        if user is None:
            return
        user.last_seen_at = utcnow()
        if ip and user.last_ip != ip:
            user.last_ip = ip
        await s.commit()


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
    async with session() as s:
        user = await s.get(User, user_id)
        if user is None:
            return
        user.rank = new_rank
        user.rank_changed_at = utcnow()
        await s.commit()


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
    source: str = "host",
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
            source=source,
            played_at=utcnow(),
        )
        s.add(match)
        await s.commit()
        return match.id


async def find_recent_match_as_guest(
    guest_user_id: str, within_sec: int = 60
) -> Optional[Match]:
    """guest_user_id 一致 & played_at が直近 within_sec 秒以内の match を 1 件返す。"""
    cutoff = utcnow() - timedelta(seconds=within_sec)
    async with session() as s:
        res = await s.execute(
            select(Match)
            .where(
                (Match.guest_user_id == guest_user_id)
                & (Match.winner != "")
                & Match.played_at.is_not(None)
                & (Match.played_at >= cutoff)
            )
            .order_by(Match.played_at.desc())
            .limit(1)
        )
        return res.scalar_one_or_none()


async def find_recent_guest_reported_match(
    guest_user_id: str, within_sec: int = 60
) -> Optional[Match]:
    """直近のゲスト報告 (source=='guest') match を 1 件返す。"""
    cutoff = utcnow() - timedelta(seconds=within_sec)
    async with session() as s:
        res = await s.execute(
            select(Match)
            .where(
                (Match.guest_user_id == guest_user_id)
                & (Match.source == "guest")
                & (Match.winner != "")
                & Match.played_at.is_not(None)
                & (Match.played_at >= cutoff)
            )
            .order_by(Match.played_at.desc())
            .limit(1)
        )
        return res.scalar_one_or_none()


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
        match.source = "host"
        await s.commit()
        return match


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
    user_id: str, limit: int = 50
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


async def find_recent_match_for_user(
    user_id: str, within_sec: int = 900, *, require_no_replay: bool = True
) -> Optional[Match]:
    """直近の対戦を 1 件返す (played_at 降順)。

    require_no_replay=True なら replays 未紐付けの match のみ。
    """
    cutoff = utcnow() - timedelta(seconds=within_sec)
    replay_exists = exists().where(Replay.match_id == Match.id)
    async with session() as s:
        q = (
            select(Match)
            .where(
                ((Match.host_user_id == user_id) | (Match.guest_user_id == user_id))
                & (Match.winner != "")
                & Match.played_at.is_not(None)
                & (Match.played_at >= cutoff)
            )
            .order_by(Match.played_at.desc())
            .limit(1)
        )
        if require_no_replay:
            q = q.where(~replay_exists)
        res = await s.execute(q)
        return res.scalar_one_or_none()


async def insert_replay(match_id: str, filename: str, data: bytes) -> bool:
    """リプレイを insert する。unique 制約違反時は False。"""
    async with session() as s:
        try:
            replay = Replay(
                match_id=match_id,
                filename=filename,
                size=len(data),
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
) -> list[tuple[Match, bool]]:
    """ユーザーの確定済み対戦を played_at 昇順で返す。(Match, has_replay) のリスト。"""
    since_dt = datetime.fromtimestamp(since_ts, tz=timezone.utc)
    replay_exists = exists().where(Replay.match_id == Match.id)
    async with session() as s:
        res = await s.execute(
            select(Match, replay_exists.label("has_replay"))
            .where(
                ((Match.host_user_id == user_id) | (Match.guest_user_id == user_id))
                & (Match.winner != "")
                & Match.played_at.is_not(None)
                & (Match.played_at > since_dt)
            )
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
    user_id: str, exclude_source: str = "sync"
) -> list[tuple[float, str]]:
    """ユーザーの確定済み対戦 (played_at 昇順) を (unix_ts, match_id) で返す。"""
    async with session() as s:
        res = await s.execute(
            select(Match.played_at, Match.id)
            .where(
                ((Match.host_user_id == user_id) | (Match.guest_user_id == user_id))
                & (Match.winner != "")
                & Match.played_at.is_not(None)
                & (Match.source != exclude_source)
            )
            .order_by(Match.played_at.asc())
        )
        out: list[tuple[float, str]] = []
        for played_at, match_id in res.all():
            if played_at is None:
                continue
            ts = played_at.timestamp()
            if played_at.tzinfo is None:
                ts = played_at.replace(tzinfo=timezone.utc).timestamp()
            out.append((ts, match_id))
        return out
