"""意見・報告 API のテスト。"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

os.environ.setdefault("ASOBBY_HOSTCHECK", "off")
os.environ.setdefault(
    "DATABASE_URL",
    "sqlite+aiosqlite:////tmp/asobby_feedback_test.db",
)
os.environ.setdefault("ASOBBY_DISCORD_CLIENT_ID", "t")
os.environ.setdefault("ASOBBY_DISCORD_CLIENT_SECRET", "t")
os.environ.setdefault("ASOBBY_SESSION_SECRET", "sec")

import db
import main
import post_redis


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
    client_version: str = "",
) -> None:
    async with db.session() as s:
        user = await s.get(db.User, user_id)
        if user is None:
            user = db.User(id=user_id, name=name, rank="normal", rank_locked=True)
            s.add(user)
        else:
            user.name = name
        if client_version:
            user.client_version = client_version
        await s.commit()


@pytest.fixture(autouse=True)
def clean_state(tmp_path, monkeypatch):
    store_dir = tmp_path / "feedback_store"
    store_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.delenv("ASOBBY_STORE", raising=False)
    monkeypatch.setenv("ASOBBY_STORE_DIR", str(store_dir))
    monkeypatch.setattr(main, "FEEDBACK_WEBHOOK_URL", "")

    db_path = tmp_path / "asobby_feedback_test.db"
    url = f"sqlite+aiosqlite:///{db_path}"
    os.environ["DATABASE_URL"] = url
    main.DATABASE_URL = url
    main.RECORDS.clear()
    main.RANKED_SESSIONS.clear()
    main.LAST_CREATE_AT.clear()
    main.LOGOUT_REVOKED.clear()
    main.ANNOUNCEMENT = None
    monkeypatch.setattr(main, "ADMIN_USER_IDS", {"admin1"})
    yield
    main.RECORDS.clear()
    main.RANKED_SESSIONS.clear()
    main.ANNOUNCEMENT = None


@pytest.mark.asyncio
async def test_feedback_page_served():
    async with app_client() as client:
        res = await client.get("/feedback")
        assert res.status_code == 200
        assert "feedback" in res.text.lower() or "意見" in res.text


@pytest.mark.asyncio
async def test_feedback_requires_login():
    async with app_client() as client:
        res = await client.post(
            "/feedback",
            json={"category": "bug", "text": "hello"},
        )
        assert res.status_code == 401


@pytest.mark.asyncio
async def test_feedback_rejects_empty_text():
    async with app_client() as client:
        await create_user("user1")
        token = bearer_token("user1")
        res = await client.post(
            "/feedback",
            json={"category": "bug", "text": "   "},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 422


@pytest.mark.asyncio
async def test_feedback_submit_and_admin_list():
    async with app_client() as client:
        await create_user("user1", name="alice", client_version="0.8.0")
        await create_user("admin1", name="midorist")
        user_headers = {"Authorization": f"Bearer {bearer_token('user1', 'alice')}"}
        admin_headers = {"Authorization": f"Bearer {bearer_token('admin1', 'midorist')}"}

        res = await client.post(
            "/feedback",
            json={"category": "feature", "text": "UI をもっと大きく"},
            headers=user_headers,
        )
        assert res.status_code == 200
        body = res.json()
        assert body["ok"] is True
        assert body["id"]
        assert body["cooldown_sec"] == main.FEEDBACK_COOLDOWN_SEC

        res = await client.get("/admin/feedback", headers=admin_headers)
        assert res.status_code == 200
        entries = res.json()["entries"]
        assert len(entries) == 1
        assert entries[0]["category"] == "feature"
        assert entries[0]["text"] == "UI をもっと大きく"
        assert entries[0]["user_id"] == "user1"
        assert entries[0]["user_name"] == "alice"
        assert entries[0]["client_version"] == "0.8.0"


@pytest.mark.asyncio
async def test_feedback_cooldown():
    async with app_client() as client:
        await create_user("user1")
        headers = {"Authorization": f"Bearer {bearer_token('user1')}"}
        payload = {"category": "other", "text": "first"}

        res = await client.post("/feedback", json=payload, headers=headers)
        assert res.status_code == 200

        res = await client.post("/feedback", json=payload, headers=headers)
        assert res.status_code == 429
        detail = res.json()["detail"]
        assert detail["reason"] == "cooldown"
        assert detail["retry_after_sec"] > 0


@pytest.mark.asyncio
async def test_admin_feedback_rejects_non_admin():
    async with app_client() as client:
        await create_user("user1")
        headers = {"Authorization": f"Bearer {bearer_token('user1')}"}
        res = await client.get("/admin/feedback", headers=headers)
        assert res.status_code == 403


@pytest.mark.asyncio
async def test_feedback_persisted_in_store():
    async with app_client() as client:
        await create_user("user1", name="bob")
        headers = {"Authorization": f"Bearer {bearer_token('user1', 'bob')}"}
        await client.post(
            "/feedback",
            json={"category": "bug", "text": "crash on start"},
            headers=headers,
        )

    entries = post_redis.load_feedback_entries(10)
    assert len(entries) == 1
    assert entries[0]["text"] == "crash on start"
    assert entries[0]["user_name"] == "bob"

    rem = post_redis.feedback_cooldown_remaining("user1")
    assert rem > 0
    assert rem <= main.FEEDBACK_COOLDOWN_SEC + 1
