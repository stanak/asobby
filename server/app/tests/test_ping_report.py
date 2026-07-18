"""高 Ping 警告 (viewer → host) の結合テスト。"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

os.environ.setdefault("ASOBBY_HOSTCHECK", "off")
os.environ.setdefault(
    "DATABASE_URL",
    "sqlite+aiosqlite:////tmp/asobby_ping_report_test.db",
)
os.environ.setdefault("ASOBBY_DISCORD_CLIENT_ID", "t")
os.environ.setdefault("ASOBBY_DISCORD_CLIENT_SECRET", "t")
os.environ.setdefault("ASOBBY_SESSION_SECRET", "sec")

import db
import main


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
    rank: str = "normal",
) -> None:
    async with db.session() as s:
        user = await s.get(db.User, user_id)
        if user is None:
            user = db.User(id=user_id, name=name, rank=rank)
            s.add(user)
        else:
            user.name = name
            user.rank = rank
        await s.commit()


async def create_post(
    client: AsyncClient,
    *,
    user_id: str = "host1",
    name: str = "host",
    ping_warn_ms: int = 60,
    ping_warn_giuroll_ms: int = 100,
    giuroll: bool = False,
) -> tuple[dict, str]:
    token = bearer_token(user_id, name)
    res = await client.post(
        "/posts",
        json={
            "post_type": "casual",
            "addr": "1.2.3.4:10800",
            "ping_warn_ms": ping_warn_ms,
            "ping_warn_giuroll_ms": ping_warn_giuroll_ms,
            "giuroll": giuroll,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    body = res.json()
    return body["post"], body["owner_token"]


def connect_guest(post_id: str, *, guest_user_id: str = "viewer1", name: str = "viewer") -> None:
    rec = main.RECORDS[post_id]
    rec.post.guest_connected = True
    rec.guest_user_id = guest_user_id
    rec.post.guest_name = name


@pytest.fixture(autouse=True)
def clean_state(tmp_path):
    db_path = tmp_path / "asobby_ping_report_test.db"
    url = f"sqlite+aiosqlite:///{db_path}"
    os.environ["DATABASE_URL"] = url
    main.DATABASE_URL = url
    main.RECORDS.clear()
    main.LAST_CREATE_AT.clear()
    main.PING_REPORT_LAST.clear()
    yield
    main.RECORDS.clear()
    main.PING_REPORT_LAST.clear()


@pytest.mark.asyncio
async def test_ping_report_delivered_via_update():
    async with app_client() as client:
        await create_user("host1", name="host")
        await create_user("viewer1", name="viewer")
        post, owner_token = await create_post(client, ping_warn_ms=60)
        connect_guest(post["id"], guest_user_id="viewer1", name="viewer")

        viewer_token = bearer_token("viewer1", "viewer")
        res = await client.post(
            f"/posts/{post['id']}/ping-report",
            json={"rtt_ms": 80},
            headers={"Authorization": f"Bearer {viewer_token}"},
        )
        assert res.status_code == 200
        assert res.json()["ok"] is True

        upd = await client.post(
            "/posts/update",
            json={
                "id": post["id"],
                "owner_token": owner_token,
                "post_type": "casual",
                "addr": post["addr"],
                "ping_warn_ms": 60,
                "ping_warn_giuroll_ms": 100,
            },
        )
        assert upd.status_code == 200
        body = upd.json()
        warnings = body.get("ping_warnings") or []
        assert len(warnings) == 1
        assert warnings[0]["from_name"] == "viewer"
        assert warnings[0]["rtt_ms"] == 80
        assert warnings[0]["threshold_ms"] == 60


@pytest.mark.asyncio
async def test_ping_report_uses_giuroll_threshold():
    async with app_client() as client:
        await create_user("host1", name="host")
        await create_user("viewer1", name="viewer")
        post, owner_token = await create_post(
            client,
            ping_warn_ms=60,
            ping_warn_giuroll_ms=100,
            giuroll=True,
        )
        connect_guest(post["id"], guest_user_id="viewer1", name="viewer")

        viewer_token = bearer_token("viewer1", "viewer")
        res = await client.post(
            f"/posts/{post['id']}/ping-report",
            json={"rtt_ms": 90},
            headers={"Authorization": f"Bearer {viewer_token}"},
        )
        assert res.status_code == 422

        res2 = await client.post(
            f"/posts/{post['id']}/ping-report",
            json={"rtt_ms": 120},
            headers={"Authorization": f"Bearer {viewer_token}"},
        )
        assert res2.status_code == 200

        upd = await client.post(
            "/posts/update",
            json={
                "id": post["id"],
                "owner_token": owner_token,
                "post_type": "casual",
                "addr": post["addr"],
                "giuroll": True,
                "ping_warn_ms": 60,
                "ping_warn_giuroll_ms": 100,
            },
        )
        warnings = upd.json().get("ping_warnings") or []
        assert len(warnings) == 1
        assert warnings[0]["threshold_ms"] == 100


@pytest.mark.asyncio
async def test_ping_report_below_threshold_rejected():
    async with app_client() as client:
        await create_user("host1", name="host")
        await create_user("viewer1", name="viewer")
        post, _ = await create_post(client, ping_warn_ms=60)
        connect_guest(post["id"], guest_user_id="viewer1", name="viewer")

        viewer_token = bearer_token("viewer1", "viewer")
        res = await client.post(
            f"/posts/{post['id']}/ping-report",
            json={"rtt_ms": 50},
            headers={"Authorization": f"Bearer {viewer_token}"},
        )
        assert res.status_code == 422


@pytest.mark.asyncio
async def test_ping_report_requires_connected_guest():
    async with app_client() as client:
        await create_user("host1", name="host")
        await create_user("viewer1", name="viewer")
        post, _ = await create_post(client, ping_warn_ms=60)

        viewer_token = bearer_token("viewer1", "viewer")
        res = await client.post(
            f"/posts/{post['id']}/ping-report",
            json={"rtt_ms": 80},
            headers={"Authorization": f"Bearer {viewer_token}"},
        )
        assert res.status_code == 409


@pytest.mark.asyncio
async def test_ping_report_only_connected_guest():
    async with app_client() as client:
        await create_user("host1", name="host")
        await create_user("viewer1", name="viewer")
        await create_user("viewer2", name="other")
        post, _ = await create_post(client, ping_warn_ms=60)
        connect_guest(post["id"], guest_user_id="viewer1", name="viewer")

        other_token = bearer_token("viewer2", "other")
        res = await client.post(
            f"/posts/{post['id']}/ping-report",
            json={"rtt_ms": 80},
            headers={"Authorization": f"Bearer {other_token}"},
        )
        assert res.status_code == 403


@pytest.mark.asyncio
async def test_ping_report_disabled():
    async with app_client() as client:
        await create_user("host1", name="host")
        await create_user("viewer1", name="viewer")
        token = bearer_token("host1", "host")
        res = await client.post(
            "/posts",
            json={
                "post_type": "casual",
                "addr": "1.2.3.4:10800",
                "ping_warn_enabled": False,
                "ping_warn_ms": 60,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        post = res.json()["post"]
        connect_guest(post["id"], guest_user_id="viewer1", name="viewer")

        viewer_token = bearer_token("viewer1", "viewer")
        res2 = await client.post(
            f"/posts/{post['id']}/ping-report",
            json={"rtt_ms": 80},
            headers={"Authorization": f"Bearer {viewer_token}"},
        )
        assert res2.status_code == 409


@pytest.mark.asyncio
async def test_ping_report_cannot_report_own_post():
    async with app_client() as client:
        await create_user("host1", name="host")
        post, _ = await create_post(client)

        host_token = bearer_token("host1", "host")
        res = await client.post(
            f"/posts/{post['id']}/ping-report",
            json={"rtt_ms": 200},
            headers={"Authorization": f"Bearer {host_token}"},
        )
        assert res.status_code == 400
