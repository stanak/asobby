"""お知らせ API・管理者権限・クライアント最低バージョンゲートのテスト。"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

os.environ.setdefault("ASOBBY_HOSTCHECK", "off")
os.environ.setdefault(
    "DATABASE_URL",
    "sqlite+aiosqlite:////tmp/asobby_announcement_test.db",
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


async def create_user(user_id: str, *, name: str = "user") -> None:
    async with db.session() as s:
        user = await s.get(db.User, user_id)
        if user is None:
            user = db.User(id=user_id, name=name, rank="normal", rank_locked=True)
            s.add(user)
        else:
            user.name = name
        await s.commit()


@pytest.fixture(autouse=True)
def clean_state(tmp_path, monkeypatch):
    db_path = tmp_path / "asobby_announcement_test.db"
    url = f"sqlite+aiosqlite:///{db_path}"
    os.environ["DATABASE_URL"] = url
    main.DATABASE_URL = url
    main.RECORDS.clear()
    main.RANKED_SESSIONS.clear()
    main.LAST_CREATE_AT.clear()
    main.LOGOUT_REVOKED.clear()
    main.ANNOUNCEMENT = None
    monkeypatch.setattr(main, "ADMIN_USER_IDS", {"admin1"})
    monkeypatch.setattr(main, "MIN_CLIENT_VERSION", "")
    monkeypatch.setattr(main, "MIN_CLIENT_ENFORCE_AT", "")
    yield
    main.RECORDS.clear()
    main.RANKED_SESSIONS.clear()
    main.ANNOUNCEMENT = None


# ----------------------------
# お知らせ API
# ----------------------------
@pytest.mark.asyncio
async def test_announcement_default_empty():
    async with app_client() as client:
        res = await client.get("/announcement")
        assert res.status_code == 200
        assert res.json() == {"announcement": None}


@pytest.mark.asyncio
async def test_announcement_requires_login():
    async with app_client() as client:
        res = await client.post("/admin/announcement", json={"text": "hi"})
        assert res.status_code == 401


@pytest.mark.asyncio
async def test_announcement_rejects_non_admin():
    async with app_client() as client:
        await create_user("user1")
        token = bearer_token("user1", "user1")
        res = await client.post(
            "/admin/announcement",
            json={"text": "hi"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 403


@pytest.mark.asyncio
async def test_admin_can_set_and_clear_announcement():
    async with app_client() as client:
        await create_user("admin1", name="midorist")
        token = bearer_token("admin1", "midorist")
        headers = {"Authorization": f"Bearer {token}"}

        res = await client.post(
            "/admin/announcement",
            json={"text": "メンテのお知らせ", "level": "warn"},
            headers=headers,
        )
        assert res.status_code == 200
        ann = res.json()["announcement"]
        assert ann["text"] == "メンテのお知らせ"
        assert ann["level"] == "warn"
        assert ann["updated_by"] == "midorist"
        assert ann["id"]

        # 公開エンドポイントに反映される
        res = await client.get("/announcement")
        assert res.json()["announcement"]["text"] == "メンテのお知らせ"

        # 空テキストで削除
        res = await client.post(
            "/admin/announcement",
            json={"text": ""},
            headers=headers,
        )
        assert res.status_code == 200
        assert res.json()["announcement"] is None
        res = await client.get("/announcement")
        assert res.json()["announcement"] is None


@pytest.mark.asyncio
async def test_auth_me_reports_is_admin():
    async with app_client() as client:
        await create_user("admin1", name="midorist")
        await create_user("user1", name="user1")

        res = await client.get(
            "/auth/me",
            headers={"Authorization": f"Bearer {bearer_token('admin1', 'midorist')}"},
        )
        assert res.json()["is_admin"] is True

        res = await client.get(
            "/auth/me",
            headers={"Authorization": f"Bearer {bearer_token('user1', 'user1')}"},
        )
        assert res.json()["is_admin"] is False


# ----------------------------
# クライアント最低バージョンゲート
# ----------------------------
async def try_create(client: AsyncClient, token: str, *, version: str | None):
    headers = {"Authorization": f"Bearer {token}"}
    if version is not None:
        headers["X-Asobby-Client-Version"] = version
    return await client.post(
        "/posts",
        json={"post_type": "casual", "addr": "203.0.113.1:10800", "giuroll": True},
        headers=headers,
    )


@pytest.mark.asyncio
async def test_gate_disabled_by_default():
    async with app_client() as client:
        await create_user("host1")
        res = await try_create(client, bearer_token("host1"), version="0.1.0")
        assert res.status_code == 200


@pytest.mark.asyncio
async def test_gate_warn_phase_does_not_block(monkeypatch):
    """期限 (ENFORCE_AT) 未設定または未来なら旧バージョンでも作成できる。"""
    monkeypatch.setattr(main, "MIN_CLIENT_VERSION", "1.0.0")
    async with app_client() as client:
        await create_user("host1")
        res = await try_create(client, bearer_token("host1"), version="0.7.24")
        assert res.status_code == 200

    monkeypatch.setattr(main, "MIN_CLIENT_ENFORCE_AT", "2999-01-01T00:00:00+00:00")
    main.LAST_CREATE_AT.clear()
    async with app_client() as client:
        await create_user("host2")
        res = await try_create(client, bearer_token("host2"), version="0.7.24")
        assert res.status_code == 200


@pytest.mark.asyncio
async def test_gate_blocks_old_client_after_deadline(monkeypatch):
    monkeypatch.setattr(main, "MIN_CLIENT_VERSION", "1.0.0")
    monkeypatch.setattr(main, "MIN_CLIENT_ENFORCE_AT", "2020-01-01T00:00:00+00:00")
    async with app_client() as client:
        await create_user("host1")
        token = bearer_token("host1")

        # 旧バージョン → 426
        res = await try_create(client, token, version="0.7.24")
        assert res.status_code == 426
        detail = res.json()["detail"]
        assert detail["reason"] == "client_outdated"
        assert detail["min_version"] == "1.0.0"

        # バージョンヘッダーなし (対応前の旧クライアント) → 426
        res = await try_create(client, token, version=None)
        assert res.status_code == 426

        # 最新バージョン → OK
        res = await try_create(client, token, version="1.0.0")
        assert res.status_code == 200

        # より新しいバージョン → OK (IP レート制限をリセットしてから)
        main.LAST_CREATE_AT.clear()
        res = await try_create(client, token, version="1.2.3")
        assert res.status_code == 200
