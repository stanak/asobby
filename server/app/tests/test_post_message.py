"""Web ロビーからホストへの定型メッセージの結合テスト。"""
from __future__ import annotations

import os
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

os.environ.setdefault("ASOBBY_HOSTCHECK", "off")
os.environ.setdefault(
    "DATABASE_URL",
    "sqlite+aiosqlite:////tmp/asobby_post_message_test.db",
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
    post_type: str = "casual",
    giuroll: bool = False,
) -> tuple[dict, str]:
    token = bearer_token(user_id, name)
    res = await client.post(
        "/posts",
        json={
            "post_type": post_type,
            "addr": "1.2.3.4:10800",
            "giuroll": giuroll,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    body = res.json()
    return body["post"], body["owner_token"]


@pytest.fixture(autouse=True)
def clean_state(tmp_path):
    db_path = tmp_path / "asobby_post_message_test.db"
    url = f"sqlite+aiosqlite:///{db_path}"
    os.environ["DATABASE_URL"] = url
    main.DATABASE_URL = url
    main.RECORDS.clear()
    main.LAST_CREATE_AT.clear()
    main.LOGOUT_REVOKED.clear()
    main.MESSAGE_LAST_SENT.clear()
    yield
    main.RECORDS.clear()
    main.MESSAGE_LAST_SENT.clear()


@pytest.mark.asyncio
async def test_message_unauthorized():
    async with app_client() as client:
        await create_user("host1", name="host")
        post, _ = await create_post(client)
        res = await client.post(
            f"/posts/{post['id']}/message",
            json={"type": "thanks"},
        )
        assert res.status_code == 401


@pytest.mark.asyncio
async def test_thanks_delivered_via_update():
    async with app_client() as client:
        await create_user("host1", name="host")
        await create_user("viewer1", name="viewer")
        post, owner_token = await create_post(client)

        viewer_token = bearer_token("viewer1", "viewer")
        res = await client.post(
            f"/posts/{post['id']}/message",
            json={"type": "thanks"},
            headers={"Authorization": f"Bearer {viewer_token}"},
        )
        assert res.status_code == 200
        assert res.json() == {"ok": True, "cooldown_sec": 60}

        upd = await client.post(
            "/posts/update",
            json={
                "id": post["id"],
                "owner_token": owner_token,
                "post_type": "casual",
                "addr": "1.2.3.4:10800",
            },
        )
        assert upd.status_code == 200
        data = upd.json()
        assert len(data["messages"]) == 1
        assert data["messages"][0]["type"] == "thanks"
        assert data["messages"][0]["from_name"] == "viewer"

        upd2 = await client.post(
            "/posts/update",
            json={
                "id": post["id"],
                "owner_token": owner_token,
                "post_type": "casual",
                "addr": "1.2.3.4:10800",
            },
        )
        assert upd2.status_code == 200
        assert upd2.json()["messages"] == []


@pytest.mark.asyncio
async def test_giuroll_request_validation():
    async with app_client() as client:
        await create_user("host1", name="host1")
        await create_user("host2", name="host2")
        await create_user("viewer1", name="viewer")
        viewer_token = bearer_token("viewer1", "viewer")

        post_on, _ = await create_post(client, user_id="host1", name="host1", giuroll=True)
        res = await client.post(
            f"/posts/{post_on['id']}/message",
            json={"type": "giuroll_request"},
            headers={"Authorization": f"Bearer {viewer_token}"},
        )
        assert res.status_code == 409

        main.LAST_CREATE_AT.clear()
        post_off, _ = await create_post(
            client, user_id="host2", name="host2", giuroll=False
        )
        res = await client.post(
            f"/posts/{post_off['id']}/message",
            json={"type": "giuroll_request"},
            headers={"Authorization": f"Bearer {viewer_token}"},
        )
        assert res.status_code == 200


@pytest.mark.asyncio
async def test_casual_invite_validation():
    async with app_client() as client:
        await create_user("host1", name="host1")
        await create_user("host2", name="host2")
        await create_user("viewer1", name="viewer")
        viewer_token = bearer_token("viewer1", "viewer")

        post_casual, _ = await create_post(
            client, user_id="host1", name="host1", post_type="casual"
        )
        res = await client.post(
            f"/posts/{post_casual['id']}/message",
            json={"type": "casual_invite"},
            headers={"Authorization": f"Bearer {viewer_token}"},
        )
        assert res.status_code == 409

        main.LAST_CREATE_AT.clear()
        post_ranked, _ = await create_post(
            client, user_id="host2", name="host2", post_type="ranked"
        )
        res = await client.post(
            f"/posts/{post_ranked['id']}/message",
            json={"type": "casual_invite"},
            headers={"Authorization": f"Bearer {viewer_token}"},
        )
        assert res.status_code == 200


@pytest.mark.asyncio
async def test_message_cooldown():
    async with app_client() as client:
        await create_user("host1", name="host")
        await create_user("viewer1", name="viewer")
        post, _ = await create_post(client)
        viewer_token = bearer_token("viewer1", "viewer")

        res1 = await client.post(
            f"/posts/{post['id']}/message",
            json={"type": "thanks"},
            headers={"Authorization": f"Bearer {viewer_token}"},
        )
        assert res1.status_code == 200

        main.MESSAGE_LAST_SENT[("viewer1", post["id"])] = time.time()

        res2 = await client.post(
            f"/posts/{post['id']}/message",
            json={"type": "thanks"},
            headers={"Authorization": f"Bearer {viewer_token}"},
        )
        assert res2.status_code == 429
        assert "Retry-After" in res2.headers


@pytest.mark.asyncio
async def test_message_not_found_and_own_post():
    async with app_client() as client:
        await create_user("host1", name="host")
        await create_user("viewer1", name="viewer")
        post, _ = await create_post(client, user_id="host1", name="host")

        viewer_token = bearer_token("viewer1", "viewer")
        res = await client.post(
            "/posts/nonexistent/message",
            json={"type": "thanks"},
            headers={"Authorization": f"Bearer {viewer_token}"},
        )
        assert res.status_code == 404

        host_token = bearer_token("host1", "host")
        res = await client.post(
            f"/posts/{post['id']}/message",
            json={"type": "thanks"},
            headers={"Authorization": f"Bearer {host_token}"},
        )
        assert res.status_code == 400
