"""ホスト到達性検証と AP バッジ表示条件のテスト。"""
from __future__ import annotations

import pytest

import main


@pytest.mark.asyncio
async def test_verify_direct_first_when_autopunch(monkeypatch):
    calls: list[str] = []

    def fake_direct(host: str, port: int, **kwargs):
        calls.append("direct")
        return True

    def fake_ap(host: str, port: int):
        calls.append("autopunch")
        return True

    monkeypatch.setattr(main, "HOSTCHECK_ENABLED", True)
    monkeypatch.setattr(main, "check_hostable_consecutive", fake_direct)
    monkeypatch.setattr(main, "check_hostable_autopunch", fake_ap)

    direct = await main.verify_hostable_or_raise("203.0.113.1:10800", autopunch=True)
    assert direct is True
    assert calls == ["direct"]


@pytest.mark.asyncio
async def test_verify_autopunch_only_marks_not_direct(monkeypatch):
    def fake_direct(host: str, port: int, **kwargs):
        return False

    def fake_ap(host: str, port: int):
        return True

    monkeypatch.setattr(main, "HOSTCHECK_ENABLED", True)
    monkeypatch.setattr(main, "check_hostable_consecutive", fake_direct)
    monkeypatch.setattr(main, "check_hostable_autopunch", fake_ap)

    direct = await main.verify_hostable_or_raise("203.0.113.1:10800", autopunch=True)
    assert direct is False


@pytest.mark.asyncio
async def test_verify_non_autopunch_unreachable(monkeypatch):
    monkeypatch.setattr(main, "HOSTCHECK_ENABLED", True)
    monkeypatch.setattr(main, "check_hostable_consecutive", lambda *a, **k: False)
    monkeypatch.setattr(main, "check_hostable_autopunch", lambda *a, **k: True)

    with pytest.raises(main.HTTPException) as exc:
        await main.verify_hostable_or_raise("203.0.113.1:10800", autopunch=False)
    assert exc.value.status_code == 409
