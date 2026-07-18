"""ロビーチャット API の結合テスト。"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

os.environ.setdefault("ASOBBY_HOSTCHECK", "off")
os.environ.setdefault(
    "DATABASE_URL",
    "sqlite+aiosqlite:////tmp/asobby_lobby_chat_test.db",
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


@pytest.fixture(autouse=True)
def clean_state(tmp_path):
    db_path = tmp_path / "asobby_lobby_chat_test.db"
    url = f"sqlite+aiosqlite:///{db_path}"
    os.environ["DATABASE_URL"] = url
    main.DATABASE_URL = url
    main.LOBBY_CHAT.clear()
    main.LOBBY_CHAT_LAST_SENT.clear()
    yield
    main.LOBBY_CHAT.clear()
    main.LOBBY_CHAT_LAST_SENT.clear()


@pytest.mark.asyncio
async def test_chat_unauthorized():
    async with app_client() as client:
        res = await client.post("/lobby/chat", json={"text": "hello"})
        assert res.status_code == 401


@pytest.mark.asyncio
async def test_chat_post_and_snapshot():
    async with app_client() as client:
        await create_user("u1", name="Alice")
        token = bearer_token("u1", "Alice")
        res = await client.post(
            "/lobby/chat",
            json={"text": "hello lobby"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 200
        body = res.json()
        assert body["ok"] is True
        msg = body["message"]
        assert msg["name"] == "Alice"
        assert msg["user_id"] == "u1"
        assert msg["text"] == "hello lobby"
        assert msg["mentions"] == []
        assert "id" in msg
        assert "ts" in msg

        snap = main.lobby_chat_snapshot()
        assert len(snap) == 1
        assert snap[0]["id"] == msg["id"]


@pytest.mark.asyncio
async def test_chat_mention_resolved():
    async with app_client() as client:
        await create_user("alice", name="Alice")
        await create_user("bob", name="Bob")
        token = bearer_token("bob", "Bob")
        res = await client.post(
            "/lobby/chat",
            json={"text": "hey @Alice are you there?"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 200
        mentions = res.json()["message"]["mentions"]
        assert len(mentions) == 1
        assert mentions[0]["user_id"] == "alice"
        assert mentions[0]["name"] == "Alice"


@pytest.mark.asyncio
async def test_chat_too_many_lines():
    async with app_client() as client:
        await create_user("u1", name="Alice")
        token = bearer_token("u1", "Alice")
        text = "\n".join(f"line{i}" for i in range(9))
        res = await client.post(
            "/lobby/chat",
            json={"text": text},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 422


@pytest.mark.asyncio
async def test_chat_too_long():
    async with app_client() as client:
        await create_user("u1", name="Alice")
        token = bearer_token("u1", "Alice")
        res = await client.post(
            "/lobby/chat",
            json={"text": "x" * 501},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 422


@pytest.mark.asyncio
async def test_chat_cooldown():
    async with app_client() as client:
        await create_user("u1", name="Alice")
        token = bearer_token("u1", "Alice")
        headers = {"Authorization": f"Bearer {token}"}
        res1 = await client.post("/lobby/chat", json={"text": "one"}, headers=headers)
        assert res1.status_code == 200
        res2 = await client.post("/lobby/chat", json={"text": "two"}, headers=headers)
        assert res2.status_code == 429


@pytest.mark.asyncio
async def test_chat_max_messages_ring_buffer():
    async with app_client() as client:
        await create_user("u1", name="Alice")
        token = bearer_token("u1", "Alice")
        headers = {"Authorization": f"Bearer {token}"}
        first_id = None
        for i in range(main.LOBBY_CHAT_MAX_MESSAGES + 1):
            main.LOBBY_CHAT_LAST_SENT.clear()
            res = await client.post(
                "/lobby/chat",
                json={"text": f"msg{i}"},
                headers=headers,
            )
            assert res.status_code == 200
            if i == 0:
                first_id = res.json()["message"]["id"]
        snap = main.lobby_chat_snapshot()
        assert len(snap) == main.LOBBY_CHAT_MAX_MESSAGES
        ids = [m["id"] for m in snap]
        assert first_id not in ids


@pytest.mark.asyncio
async def test_lobby_players_suggest_unauthorized():
    async with app_client() as client:
        res = await client.get("/lobby/players/suggest", params={"q": "a"})
        assert res.status_code == 401


@pytest.mark.asyncio
async def test_lobby_players_suggest_matches_users():
    async with app_client() as client:
        await create_user("u1", name="Alice")
        await create_user("u2", name="Alicia")
        await create_user("u3", name="Bob")
        token = bearer_token("u3", "Bob")
        res = await client.get(
            "/lobby/players/suggest",
            params={"q": "ali"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 200
        body = res.json()
        assert body["ok"] is True
        names = [s["name"] for s in body["suggestions"]]
        assert "Alice" in names
        assert "Alicia" in names
        assert "Bob" not in names
        for item in body["suggestions"]:
            assert item["kind"] == "user"
            assert "user_id" in item
