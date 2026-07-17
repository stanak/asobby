"""リプレイ収集 API の結合テスト。"""
from __future__ import annotations

import os
import re
import socket
import struct
from contextlib import asynccontextmanager
from typing import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

os.environ.setdefault("ASOBBY_HOSTCHECK", "off")
os.environ.setdefault(
    "DATABASE_URL",
    "sqlite+aiosqlite:////tmp/asobby_replay_test.db",
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


REPLAY_DATA = b"REPLAYDATA" * 100
FILENAME_RE = re.compile(
    r"^\d{14}_.+-(\w+)_vs_.+-(\w+)_(ox|xo|xx)\.rep$"
)


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
) -> None:
    async with db.session() as s:
        user = await s.get(db.User, user_id)
        if user is None:
            user = db.User(id=user_id, name=name, last_ip=last_ip)
            s.add(user)
        else:
            user.name = name
            user.last_ip = last_ip
        await s.commit()


@pytest.fixture(autouse=True)
def clean_state(tmp_path):
    db_path = tmp_path / "asobby_replay_test.db"
    url = f"sqlite+aiosqlite:///{db_path}"
    os.environ["DATABASE_URL"] = url
    main.DATABASE_URL = url
    main.RECORDS.clear()
    main.LAST_CREATE_AT.clear()
    main.LOGOUT_REVOKED.clear()
    yield
    main.RECORDS.clear()


@pytest.mark.asyncio
async def test_replay_upload_stored_and_duplicate():
    async with app_client() as client:
        await create_user("999", name="host", last_ip="1.2.3.4")
        await create_user("888", name="guest", last_ip="5.6.7.8")

        host_token = bearer_token("999", "host")
        res = await client.post(
            "/posts",
            json={"post_type": "casual", "addr": "1.2.3.4:10800"},
            headers={"Authorization": f"Bearer {host_token}"},
        )
        assert res.status_code == 200
        body = res.json()
        post = body["post"]
        owner_token = body["owner_token"]

        rec = main.RECORDS[post["id"]]
        await main.apply_guest_probe(rec, make_0x08_reply("5.6.7.8"))

        r = await client.post(
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
        assert r.status_code == 200
        assert r.json()["recorded"] is True

        up = await client.post(
            "/replays/upload",
            content=REPLAY_DATA,
            headers={
                "Authorization": f"Bearer {host_token}",
                "Content-Type": "application/octet-stream",
            },
        )
        assert up.status_code == 200
        data = up.json()
        assert data["ok"] is True
        assert data["stored"] is True
        assert FILENAME_RE.match(data["filename"])
        assert "Reimu" in data["filename"]
        assert "Marisa" in data["filename"]
        assert data["filename"].endswith("_ox.rep")

        async with db.session() as s:
            from sqlalchemy import select

            res_m = await s.execute(select(db.Match))
            match = list(res_m.scalars().all())[0]
            assert await db.replay_count_for_match(match.id) == 1

        guest_token = bearer_token("888", "guest")
        dup = await client.post(
            "/replays/upload",
            content=REPLAY_DATA,
            headers={
                "Authorization": f"Bearer {guest_token}",
                "Content-Type": "application/octet-stream",
            },
        )
        assert dup.status_code == 200
        dup_data = dup.json()
        assert dup_data["stored"] is False
        assert dup_data["reason"] == "duplicate"


@pytest.mark.asyncio
async def test_replay_upload_no_match():
    async with app_client() as client:
        await create_user("777", name="solo")
        token = bearer_token("777", "solo")
        res = await client.post(
            "/replays/upload",
            content=REPLAY_DATA,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/octet-stream",
            },
        )
        assert res.status_code == 200
        data = res.json()
        assert data["stored"] is False
        assert data["reason"] == "no_match"


@pytest.mark.asyncio
async def test_replay_upload_auth_and_size_errors():
    async with app_client() as client:
        res = await client.post(
            "/replays/upload",
            content=REPLAY_DATA,
            headers={"Content-Type": "application/octet-stream"},
        )
        assert res.status_code == 401

        await create_user("999", name="host")
        token = bearer_token("999", "host")

        empty = await client.post(
            "/replays/upload",
            content=b"",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/octet-stream",
            },
        )
        assert empty.status_code == 422

        too_big = await client.post(
            "/replays/upload",
            content=b"x" * (main.REPLAY_MAX_BYTES + 1),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/octet-stream",
            },
        )
        assert too_big.status_code == 413


@pytest.mark.asyncio
async def test_replay_filename_sanitized():
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

        await client.post(
            "/posts/result",
            json={
                "id": post["id"],
                "owner_token": owner_token,
                "winner": "guest",
                "host_char": 2,
                "guest_char": 3,
                "host_profile": "a/b:c",
                "guest_profile": 'd\\e"f',
            },
        )

        up = await client.post(
            "/replays/upload",
            content=REPLAY_DATA,
            headers={
                "Authorization": f"Bearer {host_token}",
                "Content-Type": "application/octet-stream",
            },
        )
        assert up.status_code == 200
        filename = up.json()["filename"]
        assert "a_b_c" in filename
        assert "d_e_f" in filename
        assert filename.endswith("_xo.rep")
        assert "/" not in filename
        assert "\\" not in filename
        assert ":" not in filename


@pytest.mark.asyncio
async def test_replay_upload_profile_fallback_for_host():
    """ゲスト報告のみの match に、プロファイル照合でホストリプレイを紐付ける。"""
    async with app_client() as client:
        await create_user("100", name="host", last_ip="1.2.3.4")
        await create_user("200", name="guest", last_ip="5.6.7.8")

        guest_token = bearer_token("200", "guest")
        gr = await client.post(
            "/matches/report",
            json={
                "winner": "guest",
                "host_char": 0,
                "guest_char": 1,
                "host_profile": "HostP",
                "guest_profile": "GuestP",
            },
            headers={"Authorization": f"Bearer {guest_token}"},
        )
        assert gr.status_code == 200
        assert gr.json()["recorded"] is True

        host_token = bearer_token("100", "host")
        up = await client.post(
            "/replays/upload",
            params={
                "battle_ts": main.time.time(),
                "host_profile": "HostP",
                "guest_profile": "GuestP",
                "winner": "guest",
                "my_side": "host",
            },
            content=REPLAY_DATA,
            headers={
                "Authorization": f"Bearer {host_token}",
                "Content-Type": "application/octet-stream",
            },
        )
        assert up.status_code == 200
        data = up.json()
        assert data["stored"] is True

        async with db.session() as s:
            from sqlalchemy import select

            res = await s.execute(select(db.Match))
            match = res.scalar_one()
            assert match.host_user_id == "100"
            assert await db.replay_count_for_match(match.id) == 1
