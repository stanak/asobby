"""初回開始ランク選択の結合テスト。"""
from __future__ import annotations

import os
import socket
import struct
from contextlib import asynccontextmanager
from typing import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

os.environ.setdefault("ASOBBY_HOSTCHECK", "off")
os.environ.setdefault(
    "DATABASE_URL",
    "sqlite+aiosqlite:////tmp/asobby_initrank_test.db",
)
os.environ.setdefault("ASOBBY_DISCORD_CLIENT_ID", "t")
os.environ.setdefault("ASOBBY_DISCORD_CLIENT_SECRET", "t")
os.environ.setdefault("ASOBBY_SESSION_SECRET", "sec")

import db
import main


def make_0x08_reply(ip: str, port: int = 10800) -> bytes:
    return bytes([0x08]) + b"\x00" * 6 + struct.pack("!H", port) + socket.inet_aton(ip)


def bearer_token(user_id: str, name: str = "test", token_version: int = 1) -> str:
    return main.make_session_token({"id": user_id, "name": name}, token_version)


@asynccontextmanager
async def app_client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=main.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        async with main.app.router.lifespan_context(main.app):
            yield client


async def create_user(
    user_id: str,
    *,
    name: str = "user",
    last_ip: str = "",
    rank: str | None = None,
    rank_locked: bool = False,
) -> None:
    async with db.session() as s:
        user = await s.get(db.User, user_id)
        if user is None:
            kwargs: dict = {
                "id": user_id,
                "name": name,
                "last_ip": last_ip,
                "rank_locked": rank_locked,
            }
            if rank is not None:
                kwargs["rank"] = rank
            user = db.User(**kwargs)
            s.add(user)
        else:
            user.name = name
            user.last_ip = last_ip
            user.rank_locked = rank_locked
            if rank is not None:
                user.rank = rank
        await s.commit()


@pytest.fixture(autouse=True)
def clean_state(tmp_path):
    db_path = tmp_path / "asobby_initrank_test.db"
    url = f"sqlite+aiosqlite:///{db_path}"
    os.environ["DATABASE_URL"] = url
    main.DATABASE_URL = url
    main.RECORDS.clear()
    main.LAST_CREATE_AT.clear()
    main.LOGOUT_REVOKED.clear()
    yield
    main.RECORDS.clear()


@pytest.mark.asyncio
async def test_new_user_default_normal_and_can_choose():
    async with app_client() as client:
        await create_user("111", name="newbie")
        token = bearer_token("111", "newbie")

        async with db.session() as s:
            user = await s.get(db.User, "111")
            assert user.rank == "normal"
            assert user.rank_locked is False

        me = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200
        body = me.json()
        assert body["rank"] == "normal"
        assert body["can_choose_rank"] is True


@pytest.mark.asyncio
async def test_choose_initial_rank_luna():
    async with app_client() as client:
        await create_user("111", name="picker")
        token = bearer_token("111", "picker")

        res = await client.post(
            "/rank/initial",
            json={"rank": "luna"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 200
        assert res.json() == {"ok": True, "rank": "luna"}

        async with db.session() as s:
            user = await s.get(db.User, "111")
            assert user.rank == "luna"
            assert user.rank_locked is True
            assert user.rank_changed_at is not None

        me = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me.json()["can_choose_rank"] is False


@pytest.mark.asyncio
async def test_choose_initial_rank_twice_conflict():
    async with app_client() as client:
        await create_user("111", name="picker")
        token = bearer_token("111", "picker")

        first = await client.post(
            "/rank/initial",
            json={"rank": "ex"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert first.status_code == 200

        second = await client.post(
            "/rank/initial",
            json={"rank": "hard"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert second.status_code == 409


@pytest.mark.asyncio
async def test_choose_initial_rank_ph_rejected():
    async with app_client() as client:
        await create_user("111", name="picker")
        token = bearer_token("111", "picker")

        res = await client.post(
            "/rank/initial",
            json={"rank": "ph"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 422


@pytest.mark.asyncio
async def test_choose_initial_rank_unauthorized():
    async with app_client() as client:
        res = await client.post("/rank/initial", json={"rank": "normal"})
        assert res.status_code == 401


@pytest.mark.asyncio
async def test_ranked_game_locks_both_users():
    async with app_client() as client:
        await create_user("999", name="host", last_ip="1.2.3.4")
        await create_user("888", name="guest", last_ip="5.6.7.8")

        host_token = bearer_token("999", "host")
        guest_token = bearer_token("888", "guest")

        res = await client.post(
            "/posts",
            json={"post_type": "ranked", "addr": "1.2.3.4:10800"},
            headers={"Authorization": f"Bearer {host_token}"},
        )
        post = res.json()["post"]
        owner_token = res.json()["owner_token"]
        rec = main.RECORDS[post["id"]]
        await main.apply_guest_probe(rec, make_0x08_reply("5.6.7.8"))
        assert rec.post.ranked_active is True

        r = await client.post(
            "/posts/result",
            json={
                "id": post["id"],
                "owner_token": owner_token,
                "winner": "host",
            },
        )
        assert r.status_code == 200
        assert r.json()["ranked"] is True

        async with db.session() as s:
            host = await s.get(db.User, "999")
            guest = await s.get(db.User, "888")
            assert host.rank_locked is True
            assert guest.rank_locked is True

        for token in (host_token, guest_token):
            locked = await client.post(
                "/rank/initial",
                json={"rank": "easy"},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert locked.status_code == 409
