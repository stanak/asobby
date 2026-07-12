"""永続化レイヤー (PostgreSQL / SQLAlchemy 2.0 async)。

永続化するのはユーザー・戦績・リプレイのみ。募集投稿は TTL 20 秒の
揮発データなので従来どおりインメモリ (main.py) に置く。
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, LargeBinary, String, select
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
    # トークン失効管理: この値を上げると発行済みセッションが全て無効になる
    token_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    # 最後に確認したクライアント IP (echo パケットでの対戦相手照合に使う)
    last_ip: Mapped[str] = mapped_column(String(64), default="", index=True)
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
    host_user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id"), nullable=False, index=True
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
async def upsert_user_on_login(user_id: str, name: str, ip: str) -> User:
    """ログイン完了時のユーザー登録/更新。IP が変わっていれば更新する。"""
    async with session() as s:
        user = await s.get(User, user_id)
        now = utcnow()
        if user is None:
            user = User(
                id=user_id,
                name=name,
                token_version=1,
                last_ip=ip,
                created_at=now,
                last_login_at=now,
                last_seen_at=now,
            )
            s.add(user)
        else:
            user.name = name
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
