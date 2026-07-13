"""ゲスト側対戦結果報告の結合テスト。"""
from __future__ import annotations

import os
import socket
import struct
from contextlib import asynccontextmanager
from typing import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

os.environ.setdefault("ASOBBY_HOSTCHECK", "off")
os.environ.setdefault(
    "DATABASE_URL",
    "sqlite+aiosqlite:////tmp/asobby_guest_report_test.db",
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


GUEST_REPORT_BODY = {
    "winner": "guest",
    "host_char": 0,
    "guest_char": 1,
    "host_profile": "hostp",
    "guest_profile": "guestp",
}


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
    rank: str = "normal",
) -> None:
    async with db.session() as s:
        user = await s.get(db.User, user_id)
        if user is None:
            user = db.User(id=user_id, name=name, last_ip=last_ip, rank=rank)
            s.add(user)
        else:
            user.name = name
            user.last_ip = last_ip
            user.rank = rank
        await s.commit()


@pytest.fixture(autouse=True)
def clean_state(tmp_path):
    db_path = tmp_path / "asobby_guest_report_test.db"
    url = f"sqlite+aiosqlite:///{db_path}"
    os.environ["DATABASE_URL"] = url
    main.DATABASE_URL = url
    main.RECORDS.clear()
    main.LAST_CREATE_AT.clear()
    main.LOGOUT_REVOKED.clear()
    yield
    main.RECORDS.clear()


@pytest.mark.asyncio
async def test_guest_report_recorded_and_stats():
    async with app_client() as client:
        await create_user("888", name="guest", last_ip="5.6.7.8")
        guest_token = bearer_token("888", "guest")

        res = await client.post(
            "/matches/report",
            json=GUEST_REPORT_BODY,
            headers={"Authorization": f"Bearer {guest_token}"},
        )
        assert res.status_code == 200
        assert res.json() == {"ok": True, "recorded": True}

        async with db.session() as s:
            res_m = await s.execute(select(db.Match))
            match = list(res_m.scalars().all())[0]
            assert match.source == "guest"
            assert match.ranked is False
            assert match.host_user_id is None
            assert match.guest_user_id == "888"
            assert match.winner == "guest"

        stats = await client.get(
            "/stats/me",
            headers={"Authorization": f"Bearer {guest_token}"},
        )
        assert stats.status_code == 200
        data = stats.json()
        assert data["total"]["games"] == 1
        assert data["total"]["wins"] == 1


@pytest.mark.asyncio
async def test_guest_report_duplicate():
    async with app_client() as client:
        await create_user("888", name="guest")
        guest_token = bearer_token("888", "guest")

        first = await client.post(
            "/matches/report",
            json=GUEST_REPORT_BODY,
            headers={"Authorization": f"Bearer {guest_token}"},
        )
        assert first.json()["recorded"] is True

        second = await client.post(
            "/matches/report",
            json=GUEST_REPORT_BODY,
            headers={"Authorization": f"Bearer {guest_token}"},
        )
        assert second.status_code == 200
        assert second.json() == {
            "ok": True,
            "recorded": False,
            "reason": "duplicate",
        }

        async with db.session() as s:
            res_m = await s.execute(select(db.Match))
            assert len(list(res_m.scalars().all())) == 1


@pytest.mark.asyncio
async def test_host_first_guest_duplicate():
    async with app_client() as client:
        await create_user("999", name="host", last_ip="1.2.3.4")
        await create_user("888", name="guest", last_ip="5.6.7.8")

        host_token = bearer_token("999", "host")
        res = await client.post(
            "/posts",
            json={"post_type": "casual", "addr": "1.2.3.4:10800"},
            headers={"Authorization": f"Bearer {host_token}"},
        )
        post = res.json()["post"]
        owner_token = res.json()["owner_token"]
        rec = main.RECORDS[post["id"]]
        await main.apply_guest_probe(rec, make_0x08_reply("5.6.7.8"))

        host_result = await client.post(
            "/posts/result",
            json={
                "id": post["id"],
                "owner_token": owner_token,
                "winner": "host",
                "host_char": 0,
                "guest_char": 1,
                "host_profile": "hp",
                "guest_profile": "gp",
            },
        )
        assert host_result.json()["recorded"] is True

        guest_token = bearer_token("888", "guest")
        guest_res = await client.post(
            "/matches/report",
            json=GUEST_REPORT_BODY,
            headers={"Authorization": f"Bearer {guest_token}"},
        )
        assert guest_res.json() == {
            "ok": True,
            "recorded": False,
            "reason": "duplicate",
        }

        async with db.session() as s:
            res_m = await s.execute(select(db.Match))
            matches = list(res_m.scalars().all())
            assert len(matches) == 1
            assert matches[0].source == "host"


@pytest.mark.asyncio
async def test_guest_first_host_promotes():
    async with app_client() as client:
        await create_user("999", name="host", last_ip="1.2.3.4")
        await create_user("888", name="guest", last_ip="5.6.7.8")

        guest_token = bearer_token("888", "guest")
        guest_res = await client.post(
            "/matches/report",
            json=GUEST_REPORT_BODY,
            headers={"Authorization": f"Bearer {guest_token}"},
        )
        assert guest_res.json()["recorded"] is True

        # ゲスト報告時の touch_user で last_ip がテストクライアント IP に上書きされるため復元
        await create_user("888", name="guest", last_ip="5.6.7.8")

        async with db.session() as s:
            res_m = await s.execute(select(db.Match))
            promoted_id = list(res_m.scalars().all())[0].id

        host_token = bearer_token("999", "host")
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

        host_result = await client.post(
            "/posts/result",
            json={
                "id": post["id"],
                "owner_token": owner_token,
                "winner": "host",
                "host_char": 2,
                "guest_char": 3,
                "host_profile": "hp2",
                "guest_profile": "gp2",
            },
        )
        assert host_result.status_code == 200
        data = host_result.json()
        assert data["recorded"] is True
        assert data["ranked"] is True

        async with db.session() as s:
            res_m = await s.execute(select(db.Match))
            matches = list(res_m.scalars().all())
            assert len(matches) == 1
            m = matches[0]
            assert m.id == promoted_id
            assert m.source == "host"
            assert m.ranked is True
            assert m.host_user_id == "999"
            assert m.guest_user_id == "888"
            assert m.winner == "host"
            assert m.host_char == 2
            assert m.guest_char == 3
            assert m.host_profile == "hp2"
            assert m.guest_profile == "gp2"

            host = await s.get(db.User, "999")
            guest = await s.get(db.User, "888")
            assert host.rank_locked is True
            assert guest.rank_locked is True


@pytest.mark.asyncio
async def test_guest_report_unauthorized():
    async with app_client() as client:
        res = await client.post("/matches/report", json=GUEST_REPORT_BODY)
        assert res.status_code == 401
