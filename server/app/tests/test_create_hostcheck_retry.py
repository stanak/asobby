"""create 時の到達性検証リトライ。"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("ASOBBY_DISCORD_CLIENT_ID", "t")
os.environ.setdefault("ASOBBY_DISCORD_CLIENT_SECRET", "t")
os.environ.setdefault("ASOBBY_SESSION_SECRET", "sec")

import main


@pytest.mark.asyncio
async def test_verify_hostable_for_create_retries_before_success(monkeypatch):
    calls = {"n": 0}

    async def flaky_verify(*_args, **_kwargs):
        calls["n"] += 1
        if calls["n"] < 3:
            raise main.HTTPException(
                status_code=409,
                detail={"message": "host not reachable"},
            )
        return True, False, False

    sleeps: list[float] = []

    async def fake_sleep(sec: float) -> None:
        sleeps.append(sec)

    monkeypatch.setattr(main, "verify_hostable_or_raise", flaky_verify)
    monkeypatch.setattr(main.asyncio, "sleep", fake_sleep)

    direct, ap, uncertain = await main.verify_hostable_for_create(
        "203.0.113.1:10800", autopunch=False
    )
    assert direct is True
    assert ap is False
    assert uncertain is False
    assert calls["n"] == 3
    assert sleeps == [main.CREATE_HOSTCHECK_RETRY_SEC, main.CREATE_HOSTCHECK_RETRY_SEC]


@pytest.mark.asyncio
async def test_verify_hostable_for_create_raises_after_retries(monkeypatch):
    async def always_fail(*_args, **_kwargs):
        raise main.HTTPException(
            status_code=409,
            detail={"message": "host not reachable"},
        )

    monkeypatch.setattr(main, "verify_hostable_or_raise", always_fail)

    async def fake_sleep(_sec: float) -> None:
        return None

    monkeypatch.setattr(main.asyncio, "sleep", fake_sleep)

    with pytest.raises(main.HTTPException) as exc:
        await main.verify_hostable_for_create("203.0.113.1:10800")
    assert exc.value.status_code == 409
