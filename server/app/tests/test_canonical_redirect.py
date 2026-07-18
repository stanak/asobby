"""カスタムドメインへのリダイレクト。"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

os.environ.setdefault("ASOBBY_HOSTCHECK", "off")
os.environ.setdefault("ASOBBY_BASE_URL", "https://asobby.com")

import main


@asynccontextmanager
async def app_client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=main.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        async with main.app.router.lifespan_context(main.app):
            yield client


@pytest.mark.asyncio
async def test_fly_dev_redirects_to_canonical():
    async with app_client() as client:
        res = await client.get(
            "/replays",
            headers={"Host": "asobby.fly.dev"},
            follow_redirects=False,
        )
    assert res.status_code == 301
    assert res.headers["location"] == "https://asobby.com/replays"


@pytest.mark.asyncio
async def test_canonical_host_not_redirected():
    async with app_client() as client:
        res = await client.get(
            "/myip",
            headers={"Host": "asobby.com"},
            follow_redirects=False,
        )
    assert res.status_code == 200


@pytest.mark.asyncio
async def test_local_test_host_not_redirected():
    async with app_client() as client:
        res = await client.get("/myip", follow_redirects=False)
    assert res.status_code == 200
