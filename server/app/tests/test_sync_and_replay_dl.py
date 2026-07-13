"""戦績同期・一覧・リプレイ DL API の結合テスト。"""
from __future__ import annotations

import hashlib
import os
import socket
import struct
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

os.environ.setdefault("ASOBBY_HOSTCHECK", "off")
os.environ.setdefault(
    "DATABASE_URL",
    "sqlite+aiosqlite:////tmp/asobby_sync_test.db",
)
os.environ.setdefault("ASOBBY_DISCORD_CLIENT_ID", "t")
os.environ.setdefault("ASOBBY_DISCORD_CLIENT_SECRET", "t")
os.environ.setdefault("ASOBBY_SESSION_SECRET", "sec")

import db
import main

REPLAY_DATA = b"REPLAYDATA" * 100


def make_0x08_reply(ip: str, port: int = 10800) -> bytes:
    return bytes([0x08]) + b"\x00" * 6 + struct.pack("!H", port) + socket.inet_aton(ip)


def bearer_token(user_id: str, name: str = "test", token_version: int = 1) -> str:
    return main.make_session_token({"id": user_id, "name": name}, token_version)


def sync_id(user_id: str, client_id: str) -> str:
    return hashlib.md5(f"sync:{user_id}:{client_id}".encode()).hexdigest()


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


async def create_match_via_probe(
    client: AsyncClient,
    *,
    host_id: str = "999",
    guest_id: str = "888",
    host_ip: str = "1.2.3.4",
    guest_ip: str = "5.6.7.8",
) -> tuple[str, str]:
    """probe + result で match を 1 件作り (match_id, host_token) を返す。"""
    await create_user(host_id, name="host", last_ip=host_ip)
    await create_user(guest_id, name="guest", last_ip=guest_ip)

    host_token = bearer_token(host_id, "host")
    res = await client.post(
        "/posts",
        json={"post_type": "casual", "addr": f"{host_ip}:10800"},
        headers={"Authorization": f"Bearer {host_token}"},
    )
    assert res.status_code == 200
    body = res.json()
    post = body["post"]
    owner_token = body["owner_token"]

    rec = main.RECORDS[post["id"]]
    await main.apply_guest_probe(rec, make_0x08_reply(guest_ip))

    r = await client.post(
        "/posts/result",
        json={
            "id": post["id"],
            "owner_token": owner_token,
            "winner": "host",
            "host_char": 0,
            "guest_char": 5,
            "host_profile": "hp",
            "guest_profile": "gp",
        },
    )
    assert r.status_code == 200
    assert r.json()["recorded"] is True

    async with db.session() as s:
        res_m = await s.execute(select(db.Match))
        match_id = list(res_m.scalars().all())[0].id
    return match_id, host_token


@pytest.fixture(autouse=True)
def clean_state(tmp_path):
    db_path = tmp_path / "asobby_sync_test.db"
    url = f"sqlite+aiosqlite:///{db_path}"
    os.environ["DATABASE_URL"] = url
    main.DATABASE_URL = url
    main.RECORDS.clear()
    main.LAST_CREATE_AT.clear()
    main.LOGOUT_REVOKED.clear()
    yield
    main.RECORDS.clear()


@pytest.mark.asyncio
async def test_stats_me_matches_host_and_guest_view():
    async with app_client() as client:
        match_id, host_token = await create_match_via_probe(client)

        host_res = await client.get(
            "/stats/me/matches",
            headers={"Authorization": f"Bearer {host_token}"},
        )
        assert host_res.status_code == 200
        host_data = host_res.json()
        assert host_data["ok"] is True
        assert len(host_data["matches"]) == 1
        hm = host_data["matches"][0]
        assert hm["id"] == match_id
        assert hm["my_side"] == "host"
        assert hm["host_char"] == 0
        assert hm["guest_char"] == 5
        assert hm["has_replay"] is False

        guest_token = bearer_token("888", "guest")
        guest_res = await client.get(
            "/stats/me/matches",
            headers={"Authorization": f"Bearer {guest_token}"},
        )
        assert guest_res.status_code == 200
        gm = guest_res.json()["matches"][0]
        assert gm["my_side"] == "guest"
        assert gm["id"] == match_id


@pytest.mark.asyncio
async def test_stats_me_matches_since_limit_and_replay():
    async with app_client() as client:
        match_id, host_token = await create_match_via_probe(client)

        async with db.session() as s:
            match = await s.get(db.Match, match_id)
            played_ts = main._dt_ts(match.played_at)

        future = await client.get(
            "/stats/me/matches",
            params={"since": played_ts + 10},
            headers={"Authorization": f"Bearer {host_token}"},
        )
        assert future.json()["matches"] == []

        limited = await client.get(
            "/stats/me/matches",
            params={"limit": 1},
            headers={"Authorization": f"Bearer {host_token}"},
        )
        assert len(limited.json()["matches"]) == 1

        up = await client.post(
            "/replays/upload",
            content=REPLAY_DATA,
            headers={
                "Authorization": f"Bearer {host_token}",
                "Content-Type": "application/octet-stream",
            },
        )
        assert up.json()["stored"] is True

        after = await client.get(
            "/stats/me/matches",
            headers={"Authorization": f"Bearer {host_token}"},
        )
        assert after.json()["matches"][0]["has_replay"] is True


@pytest.mark.asyncio
async def test_replay_download_access_control():
    async with app_client() as client:
        match_id, host_token = await create_match_via_probe(client)
        guest_token = bearer_token("888", "guest")
        other_token = bearer_token("777", "other")

        await create_user("777", name="other")

        await client.post(
            "/replays/upload",
            content=REPLAY_DATA,
            headers={
                "Authorization": f"Bearer {host_token}",
                "Content-Type": "application/octet-stream",
            },
        )

        no_auth = await client.get(f"/replays/{match_id}")
        assert no_auth.status_code == 401

        host_dl = await client.get(
            f"/replays/{match_id}",
            headers={"Authorization": f"Bearer {host_token}"},
        )
        assert host_dl.status_code == 200
        assert host_dl.content == REPLAY_DATA
        assert "attachment" in host_dl.headers.get("content-disposition", "")

        guest_dl = await client.get(
            f"/replays/{match_id}",
            headers={"Authorization": f"Bearer {guest_token}"},
        )
        assert guest_dl.status_code == 200

        other_dl = await client.get(
            f"/replays/{match_id}",
            headers={"Authorization": f"Bearer {other_token}"},
        )
        assert other_dl.status_code == 404

        no_replay_id = "0" * 32
        missing = await client.get(
            f"/replays/{no_replay_id}",
            headers={"Authorization": f"Bearer {host_token}"},
        )
        assert missing.status_code == 404


@pytest.mark.asyncio
async def test_matches_sync_import_duplicate_invalid():
    async with app_client() as client:
        await create_user("555", name="syncuser")
        token = bearer_token("555", "syncuser")
        client_id = "a" * 32
        played_at = time.time() - 3600

        body = {
            "matches": [{
                "client_id": client_id,
                "played_at": played_at,
                "my_side": "host",
                "winner": "host",
                "my_char": 0,
                "opp_char": 5,
                "my_profile": "me",
                "opp_profile": "opp",
            }],
        }
        first = await client.post(
            "/matches/sync",
            json=body,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert first.status_code == 200
        r0 = first.json()["results"][0]
        assert r0["status"] == "imported"
        assert r0["server_id"] == sync_id("555", client_id)

        second = await client.post(
            "/matches/sync",
            json=body,
            headers={"Authorization": f"Bearer {token}"},
        )
        r1 = second.json()["results"][0]
        assert r1["status"] == "duplicate"
        assert r1["server_id"] == sync_id("555", client_id)

        invalid = await client.post(
            "/matches/sync",
            json={
                "matches": [{
                    "client_id": "b" * 32,
                    "played_at": datetime(2000, 1, 1, tzinfo=timezone.utc).timestamp(),
                    "my_side": "host",
                    "winner": "host",
                }],
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert invalid.json()["results"][0]["status"] == "invalid"

        too_many = {
            "matches": [
                {
                    "client_id": f"{i:032x}",
                    "played_at": played_at - i,
                    "my_side": "host",
                    "winner": "host",
                }
                for i in range(501)
            ],
        }
        over = await client.post(
            "/matches/sync",
            json=too_many,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert over.status_code == 422

        unauth = await client.post("/matches/sync", json=body)
        assert unauth.status_code == 401


@pytest.mark.asyncio
async def test_matches_sync_near_duplicate_and_guest_mapping():
    async with app_client() as client:
        match_id, host_token = await create_match_via_probe(client)

        async with db.session() as s:
            existing = await s.get(db.Match, match_id)
            existing_ts = main._dt_ts(existing.played_at)

        guest_token = bearer_token("888", "guest")
        near = await client.post(
            "/matches/sync",
            json={
                "matches": [{
                    "client_id": "c" * 32,
                    "played_at": existing_ts + 30,
                    "my_side": "guest",
                    "winner": "guest",
                    "my_char": 5,
                    "opp_char": 0,
                    "my_profile": "gp",
                    "opp_profile": "hp",
                }],
            },
            headers={"Authorization": f"Bearer {guest_token}"},
        )
        nr = near.json()["results"][0]
        assert nr["status"] == "duplicate"
        assert nr["server_id"] == match_id

        sync_token = bearer_token("777", "solo")
        await create_user("777", name="solo")
        solo_id = "d" * 32
        solo_ts = time.time() - 7200
        imported = await client.post(
            "/matches/sync",
            json={
                "matches": [{
                    "client_id": solo_id,
                    "played_at": solo_ts,
                    "my_side": "guest",
                    "winner": "guest",
                    "my_char": 3,
                    "opp_char": 7,
                    "my_profile": "myprof",
                    "opp_profile": "oppprof",
                }],
            },
            headers={"Authorization": f"Bearer {sync_token}"},
        )
        assert imported.json()["results"][0]["status"] == "imported"

        async with db.session() as s:
            m = await s.get(db.Match, sync_id("777", solo_id))
            assert m is not None
            assert m.guest_user_id == "777"
            assert m.host_user_id is None
            assert m.host_profile == "oppprof"
            assert m.guest_profile == "myprof"
            assert m.host_char == 7
            assert m.guest_char == 3
            assert m.source == "sync"
