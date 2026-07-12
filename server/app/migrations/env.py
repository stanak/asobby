from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from alembic import context
from sqlalchemy.engine import Connection

# app ディレクトリを import パスに追加して db モジュールを読めるようにする
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db  # noqa: E402

config = context.config
target_metadata = db.Base.metadata


def _database_url() -> str:
    url = config.get_main_option("sqlalchemy.url") or os.environ.get("DATABASE_URL", "")
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    return url


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # sqlite (テスト用) では ALTER TABLE 制約のため batch モードを使う
        render_as_batch=connection.dialect.name == "sqlite",
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    engine = db.make_engine(_database_url())
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


def run_migrations_offline() -> None:
    sa_url, _ = db.normalize_db_url(_database_url())
    context.configure(url=sa_url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_async_migrations())
