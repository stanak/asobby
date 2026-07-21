"""ホスト到達性検証と AP バッジ表示条件のテスト。"""
from __future__ import annotations

import pytest

import main


@pytest.mark.asyncio
async def test_verify_autopunch_requires_ap_even_when_direct(monkeypatch):
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
    assert calls == ["direct", "autopunch"]


@pytest.mark.asyncio
async def test_verify_autopunch_rejects_when_ap_unreachable(monkeypatch):
    monkeypatch.setattr(main, "HOSTCHECK_ENABLED", True)
    monkeypatch.setattr(main, "check_hostable_consecutive", lambda *a, **k: True)
    monkeypatch.setattr(main, "check_hostable_autopunch", lambda *a, **k: False)

    with pytest.raises(main.HTTPException) as exc:
        await main.verify_hostable_or_raise("203.0.113.1:10800", autopunch=True)
    assert exc.value.status_code == 409


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


def test_should_reverify_on_addr_change():
    rec = main.PostRecord(
        post=main.Post(addr="203.0.113.1:10800"),
        owner_token="t",
        creator_ip="1.2.3.4",
        last_hostcheck_at=100.0,
    )
    assert main.should_reverify_host_on_update(
        rec,
        addr="203.0.113.1:10801",
        net_status=0,
        now=110.0,
    )


def test_should_reverify_on_recruit_heartbeat_interval():
    rec = main.PostRecord(
        post=main.Post(addr="203.0.113.1:10800"),
        owner_token="t",
        creator_ip="1.2.3.4",
        last_hostcheck_at=100.0,
    )
    assert main.should_reverify_host_on_update(
        rec,
        addr="203.0.113.1:10800",
        net_status=0,
        now=121.0,
        interval_sec=20.0,
    )
    assert not main.should_reverify_host_on_update(
        rec,
        addr="203.0.113.1:10800",
        net_status=0,
        now=115.0,
        interval_sec=20.0,
    )


def test_should_not_reverify_during_battle():
    rec = main.PostRecord(
        post=main.Post(addr="203.0.113.1:10800"),
        owner_token="t",
        creator_ip="1.2.3.4",
        last_hostcheck_at=0.0,
    )
    assert not main.should_reverify_host_on_update(
        rec,
        addr="203.0.113.1:10800",
        net_status=main.NET_BATTLE,
        now=999.0,
    )
