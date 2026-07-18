"""サイト閲覧者数 (presence) の結合テスト。"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

os.environ.setdefault("ASOBBY_HOSTCHECK", "off")
os.environ.setdefault(
    "DATABASE_URL",
    "sqlite+aiosqlite:////tmp/asobby_presence_test.db",
)
os.environ.setdefault("ASOBBY_DISCORD_CLIENT_ID", "t")
os.environ.setdefault("ASOBBY_DISCORD_CLIENT_SECRET", "t")
os.environ.setdefault("ASOBBY_SESSION_SECRET", "sec")

import main


@asynccontextmanager
async def app_client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=main.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        async with main.app.router.lifespan_context(main.app):
            yield client


@pytest.fixture(autouse=True)
def clear_presence() -> None:
    with main._PRESENCE_LOCK:
        main.PRESENCE_LOCAL.clear()


@pytest.mark.asyncio
async def test_presence_count_starts_at_zero() -> None:
    async with app_client() as client:
        res = await client.get("/presence/count")
        assert res.status_code == 200
        assert res.json() == {"ok": True, "count": 0}


@pytest.mark.asyncio
async def test_heartbeat_sets_cookie_and_counts_visitor() -> None:
    async with app_client() as client:
        res = await client.post("/presence/heartbeat")
        assert res.status_code == 200
        body = res.json()
        assert body["ok"] is True
        assert body["count"] == 1
        assert main.VISITOR_COOKIE in res.cookies

        res2 = await client.get("/presence/count")
        assert res2.json()["count"] == 1


@pytest.mark.asyncio
async def test_same_cookie_does_not_double_count() -> None:
    async with app_client() as client:
        first = await client.post("/presence/heartbeat")
        cookie = first.cookies[main.VISITOR_COOKIE]

        second = await client.post(
            "/presence/heartbeat",
            cookies={main.VISITOR_COOKIE: cookie},
        )
        assert second.json()["count"] == 1

        third = await client.get(
            "/presence/count",
            cookies={main.VISITOR_COOKIE: cookie},
        )
        assert third.json()["count"] == 1


@pytest.mark.asyncio
async def test_two_visitors_count_as_two() -> None:
    async with app_client() as client:
        a = await client.post("/presence/heartbeat")
        b = await client.post("/presence/heartbeat")
        assert a.cookies[main.VISITOR_COOKIE] != b.cookies[main.VISITOR_COOKIE]

        res = await client.get("/presence/count")
        assert res.json()["count"] == 2
