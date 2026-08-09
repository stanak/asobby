"""天則観 (tsk) 戦績 DB インポートの結合テスト。"""
from __future__ import annotations

import os
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

os.environ.setdefault("ASOBBY_HOSTCHECK", "off")
os.environ.setdefault(
    "DATABASE_URL",
    "sqlite+aiosqlite:////tmp/asobby_tsk_import_test.db",
)
os.environ.setdefault("ASOBBY_DISCORD_CLIENT_ID", "t")
os.environ.setdefault("ASOBBY_DISCORD_CLIENT_SECRET", "t")
os.environ.setdefault("ASOBBY_SESSION_SECRET", "sec")

import db
import main


def bearer_token(user_id: str, name: str = "test", token_version: int = 1) -> str:
    return main.make_session_token({"id": user_id, "name": name}, token_version)


def jst_to_filetime(year: int, month: int, day: int, hour: int, minute: int, second: int) -> int:
    """JST 壁時計を天則観 FILETIME に変換する。"""
    unix_like = datetime(
        year, month, day, hour, minute, second, tzinfo=timezone.utc
    ).timestamp()
    return int((unix_like + 11644473600) * 10_000_000)


def create_tsk_db(path: str, rows: list[tuple]) -> bytes:
    """天則観形式の SQLite DB を作成し、バイト列を返す。"""
    if os.path.exists(path):
        os.unlink(path)
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE trackrecord123("
        "timestamp INTEGER NOT NULL,"
        "p1name TEXT,"
        "p1id INTEGER NOT NULL,"
        "p1win INTEGER NOT NULL,"
        "p2name TEXT,"
        "p2id INTEGER NOT NULL,"
        "p2win INTEGER NOT NULL,"
        "PRIMARY KEY (timestamp))"
    )
    for row in rows:
        conn.execute(
            "INSERT INTO trackrecord123 VALUES (?,?,?,?,?,?,?)",
            row,
        )
    conn.commit()
    conn.close()
    with open(path, "rb") as f:
        return f.read()


@asynccontextmanager
async def app_client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=main.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        async with main.app.router.lifespan_context(main.app):
            yield client


async def create_user(user_id: str, *, name: str = "user") -> None:
    async with db.session() as s:
        user = await s.get(db.User, user_id)
        if user is None:
            user = db.User(id=user_id, name=name)
            s.add(user)
        else:
            user.name = name
        await s.commit()


def sample_rows() -> list[tuple]:
    """有効行 2 件 + 不正行 2 件。"""
    ft1 = jst_to_filetime(2025, 1, 2, 12, 0, 0)
    ft2 = jst_to_filetime(2025, 1, 3, 18, 30, 0)
    ft3 = jst_to_filetime(2025, 1, 4, 10, 0, 0)
    ft4 = jst_to_filetime(2025, 1, 5, 11, 0, 0)
    return [
        (ft1, "てすと".encode("cp932"), 0, 2, b"opp1", 1, 0),
        (ft2, b"me2", 5, 2, "相手".encode("cp932"), 3, 1),
        (ft3, b"x", 0, 1, b"y", 1, 1),  # どちらも 2 未満
        (ft4, b"x", 99, 2, b"y", 1, 0),  # char id 範囲外
    ]


@pytest.fixture(autouse=True)
def clean_state(tmp_path):
    db_path = tmp_path / "asobby_tsk_import_test.db"
    url = f"sqlite+aiosqlite:///{db_path}"
    os.environ["DATABASE_URL"] = url
    main.DATABASE_URL = url
    main.RECORDS.clear()
    main.RANKED_SESSIONS.clear()
    main.LAST_CREATE_AT.clear()
    main.LOGOUT_REVOKED.clear()
    yield
    main.RECORDS.clear()
    main.RANKED_SESSIONS.clear()


@pytest.mark.asyncio
async def test_tsk_import_success(tmp_path):
    async with app_client() as client:
        await create_user("111", name="importer")
        token = bearer_token("111", "importer")

        tsk_path = str(tmp_path / "tsk.db")
        db_bytes = create_tsk_db(tsk_path, sample_rows())

        res = await client.post(
            "/import/tensokukan",
            content=db_bytes,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 200
        body = res.json()
        assert body["ok"] is True
        assert body["imported"] == 2
        assert body["skipped_invalid"] == 2
        assert body["total"] == 4

        stats = await client.get(
            "/stats/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert stats.status_code == 200
        assert stats.json()["total"]["games"] == 2

        async with db.session() as s:
            res_m = await s.execute(
                select(db.Match).where(db.Match.host_user_id == "111")
            )
            matches = list(res_m.scalars().all())
            assert len(matches) == 2
            by_profile = {m.host_profile: m for m in matches}
            assert by_profile["てすと"].source == "import"
            assert by_profile["てすと"].ranked is False
            assert by_profile["てすと"].winner == "host"
            assert by_profile["me2"].guest_profile == "相手"

            expected_utc = datetime(2025, 1, 2, 3, 0, 0)
            played = by_profile["てすと"].played_at
            if played.tzinfo is not None:
                played = played.replace(tzinfo=None)
            assert played == expected_utc


@pytest.mark.asyncio
async def test_tsk_import_reupload_skips_duplicates(tmp_path):
    async with app_client() as client:
        await create_user("111")
        token = bearer_token("111")

        tsk_path = str(tmp_path / "tsk_re.db")
        rows = sample_rows()[:2]
        db_bytes = create_tsk_db(tsk_path, rows)

        first = await client.post(
            "/import/tensokukan",
            content=db_bytes,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert first.json()["imported"] == 2

        second = await client.post(
            "/import/tensokukan",
            content=db_bytes,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert second.status_code == 200
        body = second.json()
        assert body["imported"] == 0
        assert body["skipped_duplicate"] == 2


@pytest.mark.asyncio
async def test_tsk_import_invalid_body(tmp_path):
    async with app_client() as client:
        await create_user("111")
        token = bearer_token("111")

        empty = await client.post(
            "/import/tensokukan",
            content=b"",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert empty.status_code == 422

        bad = await client.post(
            "/import/tensokukan",
            content=b"not sqlite",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert bad.status_code == 422

        unauth = await client.post(
            "/import/tensokukan",
            content=b"SQLite format 3\x00",
        )
        assert unauth.status_code == 401


@pytest.mark.asyncio
async def test_tsk_import_skips_near_existing_match(tmp_path):
    async with app_client() as client:
        await create_user("111")
        token = bearer_token("111")

        played_at = datetime(2025, 1, 2, 3, 0, 0, tzinfo=timezone.utc)
        await db.bulk_insert_matches([{
            "id": "existingmatch00000000000001",
            "host_user_id": "111",
            "guest_user_id": None,
            "host_ip": "1.2.3.4",
            "guest_ip": "5.6.7.8",
            "winner": "host",
            "host_char": 0,
            "guest_char": 1,
            "host_profile": "hp",
            "guest_profile": "gp",
            "ranked": False,
            "source": "host",
            "played_at": played_at,
        }])

        ft = jst_to_filetime(2025, 1, 2, 12, 0, 30)  # ±30 秒以内
        tsk_path = str(tmp_path / "tsk_near.db")
        db_bytes = create_tsk_db(tsk_path, [
            (ft, b"me", 0, 2, b"opp", 1, 0),
        ])

        res = await client.post(
            "/import/tensokukan",
            content=db_bytes,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 200
        body = res.json()
        assert body["imported"] == 0
        assert body["skipped_duplicate"] == 1
