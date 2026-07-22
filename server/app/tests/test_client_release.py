"""クライアント最新版 API のテスト。"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

os.environ.setdefault("ASOBBY_HOSTCHECK", "off")

import client_release
import main


@asynccontextmanager
async def app_client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=main.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        async with main.app.router.lifespan_context(main.app):
            yield client


@pytest.fixture(autouse=True)
def reset_release_cache(monkeypatch):
    client_release._cache = None
    client_release._cache_at = 0.0
    monkeypatch.delenv("ASOBBY_CLIENT_LATEST_VERSION", raising=False)


def test_update_info_for_current():
    latest = {
        "tag": "v0.5.1",
        "version": "0.5.1",
        "html_url": "https://example.com",
    }
    fresh = client_release.update_info_for_current(latest, "0.5.1")
    assert fresh["update_available"] is False
    old = client_release.update_info_for_current(latest, "0.4.30")
    assert old["update_available"] is True
    assert old["outdated"] is True
    assert old["current"] == "0.4.30"
    assert old["latest"] == "0.5.1"


def test_parse_version():
    assert client_release.parse_version("v0.4.13") == (0, 4, 13)
    assert client_release.is_older("0.4.12", "0.4.13")
    assert not client_release.is_older("0.4.13", "0.4.13")


@pytest.mark.asyncio
async def test_client_latest_from_env(monkeypatch):
    monkeypatch.setenv("ASOBBY_CLIENT_LATEST_VERSION", "0.9.0")
    async with app_client() as client:
        res = await client.get("/client/latest")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["tag"] == "v0.9.0"
    assert body["version"] == "0.9.0"
    assert body["update_available"] is False
    assert body["outdated"] is False
    assert body["download_url"] == body["html_url"]
    assert "/download/" not in body["download_url"]


@pytest.mark.asyncio
async def test_client_latest_marks_outdated(monkeypatch):
    monkeypatch.setenv("ASOBBY_CLIENT_LATEST_VERSION", "0.9.0")
    async with app_client() as client:
        res = await client.get("/client/latest?current=0.4.12")
    body = res.json()
    assert body["ok"] is True
    assert body["current"] == "0.4.12"
    assert body["latest"] == "0.9.0"
    assert body["update_available"] is True
    assert body["outdated"] is True


@pytest.mark.asyncio
async def test_client_update_response_headers(monkeypatch):
    monkeypatch.setenv("ASOBBY_CLIENT_LATEST_VERSION", "1.0.0")
    async with app_client() as client:
        res = await client.get(
            "/myip",
            headers={"X-Asobby-Client-Version": "0.4.12"},
        )
    assert res.status_code == 200
    assert res.headers.get("X-Asobby-Client-Update") == "v1.0.0"
    assert "github.com" in res.headers.get("X-Asobby-Client-Download", "")
