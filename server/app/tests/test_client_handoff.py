"""ブラウザセッション→クライアントのハンドオフと last_ip の IPv4 制限のテスト。"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import AsyncIterator
from urllib.parse import parse_qs, urlsplit

import pytest
from httpx import ASGITransport, AsyncClient

os.environ.setdefault("ASOBBY_HOSTCHECK", "off")
os.environ.setdefault(
    "DATABASE_URL",
    "sqlite+aiosqlite:////tmp/asobby_handoff_test.db",
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


async def create_user(user_id: str, *, name: str = "user", last_ip: str = "") -> None:
    async with db.session() as s:
        user = await s.get(db.User, user_id)
        if user is None:
            user = db.User(id=user_id, name=name, last_ip=last_ip)
            s.add(user)
        else:
            user.name = name
            user.last_ip = last_ip
        await s.commit()


async def get_last_ip(user_id: str) -> str:
    async with db.session() as s:
        user = await s.get(db.User, user_id)
        assert user is not None
        return user.last_ip


@pytest.fixture(autouse=True)
def clean_state(tmp_path):
    db_path = tmp_path / "asobby_handoff_test.db"
    url = f"sqlite+aiosqlite:///{db_path}"
    os.environ["DATABASE_URL"] = url
    main.DATABASE_URL = url
    main.RECORDS.clear()
    main.LOGOUT_REVOKED.clear()
    main.CLIENT_HANDOFF_CODES.clear()
    yield
    main.RECORDS.clear()
    main.CLIENT_HANDOFF_CODES.clear()


@pytest.mark.asyncio
async def test_handoff_requires_login_redirects_to_web_login():
    async with app_client() as client:
        res = await client.get("/auth/client/handoff", params={"port": 12345})
        assert res.status_code == 302
        loc = res.headers["location"]
        assert loc.startswith("/auth/discord/web?next=")
        assert "port%3D12345" in loc


@pytest.mark.asyncio
async def test_handoff_force_redirects_to_account_picker():
    async with app_client() as client:
        await create_user("u1", name="alice")
        token = bearer_token("u1", "alice")
        res = await client.get(
            "/auth/client/handoff",
            params={"port": 12345, "force": "1"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 302
        loc = res.headers["location"]
        assert loc.startswith("/auth/discord/web?next=")
        assert "force=1" in loc
        assert "port%3D12345" in loc
        set_cookie = res.headers.get("set-cookie", "")
        assert "asobby_session=" in set_cookie
        assert "Max-Age=0" in set_cookie or "max-age=0" in set_cookie.lower()


@pytest.mark.asyncio
async def test_auth_logout_api_revokes_bearer():
    async with app_client() as client:
        await create_user("u1", name="alice")
        token = bearer_token("u1", "alice")
        res = await client.post(
            "/auth/logout",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 200
        assert res.json() == {"ok": True}
        me = await client.get(
            "/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert me.status_code == 401


@pytest.mark.asyncio
async def test_auth_logout_api_unauthorized():
    async with app_client() as client:
        res = await client.post("/auth/logout")
        assert res.status_code == 401


@pytest.mark.asyncio
async def test_handoff_invalid_port():
    async with app_client() as client:
        await create_user("u1", name="alice")
        token = bearer_token("u1", "alice")
        res = await client.get(
            "/auth/client/handoff",
            params={"port": 80},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 400


@pytest.mark.asyncio
async def test_handoff_and_exchange_flow():
    async with app_client() as client:
        await create_user("u1", name="alice")
        token = bearer_token("u1", "alice")

        # ログイン済みセッションでハンドオフ → localhost へのリダイレクト
        res = await client.get(
            "/auth/client/handoff",
            params={"port": 43210},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 302
        loc = res.headers["location"]
        assert loc.startswith("http://127.0.0.1:43210/auth?code=")
        code = parse_qs(urlsplit(loc).query)["code"][0]

        # コードをトークンに交換
        ex = await client.post("/auth/client/exchange", json={"code": code})
        assert ex.status_code == 200
        body = ex.json()
        assert body["status"] == "ok"
        assert body["user"] == {"id": "u1", "name": "alice"}

        # 交換で得たトークンは /auth/me に通る
        me = await client.get(
            "/auth/me",
            headers={"Authorization": f"Bearer {body['session_token']}"},
        )
        assert me.status_code == 200
        assert me.json()["id"] == "u1"

        # コードはワンショット
        again = await client.post("/auth/client/exchange", json={"code": code})
        assert again.status_code == 404


@pytest.mark.asyncio
async def test_exchange_unknown_code():
    async with app_client() as client:
        res = await client.post("/auth/client/exchange", json={"code": "nope"})
        assert res.status_code == 404


@pytest.mark.asyncio
async def test_last_ip_rejects_ipv6():
    async with app_client():
        # 新規作成時: IPv6 は保存しない
        user = await db.upsert_user_on_login("u2", "bob", ip="2001:db8::1")
        assert user.last_ip == ""

        # IPv4 は保存する
        await db.touch_user("u2", "5.6.7.8")
        assert await get_last_ip("u2") == "5.6.7.8"

        # IPv6 で touch しても既存の IPv4 を保持する
        await db.touch_user("u2", "2001:db8::2")
        assert await get_last_ip("u2") == "5.6.7.8"

        # ログイン時も同様
        await db.upsert_user_on_login("u2", "bob", ip="2001:db8::3")
        assert await get_last_ip("u2") == "5.6.7.8"
        await db.upsert_user_on_login("u2", "bob", ip="9.8.7.6")
        assert await get_last_ip("u2") == "9.8.7.6"
