"""ホスト到達性検証と AP バッジ表示条件のテスト。"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

import db
import main

os.environ.setdefault("ASOBBY_HOSTCHECK", "off")
os.environ.setdefault(
    "DATABASE_URL",
    "sqlite+aiosqlite:////tmp/asobby_host_verification_test.db",
)
os.environ.setdefault("ASOBBY_DISCORD_CLIENT_ID", "t")
os.environ.setdefault("ASOBBY_DISCORD_CLIENT_SECRET", "t")
os.environ.setdefault("ASOBBY_SESSION_SECRET", "sec")


def bearer_token(user_id: str, name: str = "test") -> str:
    return main.make_session_token({"id": user_id, "name": name}, 1)


async def create_user(user_id: str, *, name: str = "user") -> None:
    async with db.session() as s:
        user = await s.get(db.User, user_id)
        if user is None:
            user = db.User(id=user_id, name=name)
            s.add(user)
        else:
            user.name = name
        await s.commit()


@pytest.fixture(autouse=True)
def clean_state(tmp_path):
    db_path = tmp_path / "asobby_host_verification_test.db"
    url = f"sqlite+aiosqlite:///{db_path}"
    os.environ["DATABASE_URL"] = url
    main.DATABASE_URL = url
    main.RECORDS.clear()
    main.LAST_CREATE_AT.clear()
    main.SERVER_STARTED_AT = 0.0
    yield
    main.RECORDS.clear()
    main.SERVER_STARTED_AT = 0.0


@asynccontextmanager
async def app_client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=main.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        async with main.app.router.lifespan_context(main.app):
            yield client


@pytest.mark.asyncio
async def test_verify_always_runs_ap_but_direct_strict_wins(monkeypatch):
    calls: list[str] = []

    def fake_direct(host: str, port: int, **kwargs):
        calls.append("direct")
        return True

    def fake_ap(host: str, port: int):
        calls.append("autopunch")
        return False

    monkeypatch.setattr(main, "HOSTCHECK_ENABLED", True)
    monkeypatch.setattr(main, "check_hostable_consecutive", fake_direct)
    monkeypatch.setattr(main, "check_hostable_autopunch", fake_ap)

    direct, autopunch = await main.verify_hostable_or_raise(
        "203.0.113.1:10800", autopunch=True
    )
    assert direct is True
    assert autopunch is False
    assert calls == ["direct", "direct", "autopunch"]


@pytest.mark.asyncio
async def test_verify_flaky_direct_with_autopunch_stays_ap_only(monkeypatch):
    call_n = 0

    def fake_direct(host: str, port: int, **kwargs):
        nonlocal call_n
        call_n += 1
        return call_n == 1

    monkeypatch.setattr(main, "HOSTCHECK_ENABLED", True)
    monkeypatch.setattr(main, "check_hostable_consecutive", fake_direct)
    monkeypatch.setattr(main, "check_hostable_autopunch", lambda *a, **k: True)

    direct, autopunch = await main.verify_hostable_or_raise(
        "203.0.113.1:10800", autopunch=True
    )
    assert direct is False
    assert autopunch is True


@pytest.mark.asyncio
async def test_verify_flaky_direct_without_client_flag_stays_ap_only(monkeypatch):
    """AP 必須だがクライアント未検出 (autopunch=False) でも AP 表示を維持。"""
    call_n = 0

    def fake_direct(host: str, port: int, **kwargs):
        nonlocal call_n
        call_n += 1
        return call_n == 1

    monkeypatch.setattr(main, "HOSTCHECK_ENABLED", True)
    monkeypatch.setattr(main, "check_hostable_consecutive", fake_direct)
    monkeypatch.setattr(main, "check_hostable_autopunch", lambda *a, **k: True)

    direct, autopunch = await main.verify_hostable_or_raise(
        "203.0.113.1:10800", autopunch=False
    )
    assert direct is False
    assert autopunch is True


@pytest.mark.asyncio
async def test_verify_autopunch_requires_ap_when_direct_fails(monkeypatch):
    calls: list[str] = []

    def fake_direct(host: str, port: int, **kwargs):
        calls.append("direct")
        return False

    def fake_ap(host: str, port: int):
        calls.append("autopunch")
        return True

    monkeypatch.setattr(main, "HOSTCHECK_ENABLED", True)
    monkeypatch.setattr(main, "check_hostable_consecutive", fake_direct)
    monkeypatch.setattr(main, "check_hostable_autopunch", fake_ap)

    direct, autopunch = await main.verify_hostable_or_raise(
        "203.0.113.1:10800", autopunch=True
    )
    assert direct is False
    assert autopunch is True
    assert calls == ["direct", "autopunch"]


@pytest.mark.asyncio
async def test_verify_autopunch_rejects_when_ap_unreachable(monkeypatch):
    monkeypatch.setattr(main, "HOSTCHECK_ENABLED", True)
    monkeypatch.setattr(main, "check_hostable_consecutive", lambda *a, **k: False)
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

    direct, autopunch = await main.verify_hostable_or_raise(
        "203.0.113.1:10800", autopunch=True
    )
    assert direct is False
    assert autopunch is True


@pytest.mark.asyncio
async def test_verify_detects_ap_only_when_client_flag_false(monkeypatch):
    monkeypatch.setattr(main, "HOSTCHECK_ENABLED", True)
    monkeypatch.setattr(main, "check_hostable_consecutive", lambda *a, **k: False)
    monkeypatch.setattr(main, "check_hostable_autopunch", lambda *a, **k: True)

    direct, autopunch = await main.verify_hostable_or_raise(
        "203.0.113.1:10800", autopunch=False
    )
    assert direct is False
    assert autopunch is True


@pytest.mark.asyncio
async def test_verify_non_autopunch_unreachable(monkeypatch):
    monkeypatch.setattr(main, "HOSTCHECK_ENABLED", True)
    monkeypatch.setattr(main, "check_hostable_consecutive", lambda *a, **k: False)
    monkeypatch.setattr(main, "check_hostable_autopunch", lambda *a, **k: False)

    with pytest.raises(main.HTTPException) as exc:
        await main.verify_hostable_or_raise("203.0.113.1:10800", autopunch=False)
    assert exc.value.status_code == 409


def test_should_reverify_on_addr_change(monkeypatch):
    monkeypatch.setattr(main, "HOSTCHECK_ENABLED", True)
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


def test_should_reverify_on_recruit_heartbeat_interval(monkeypatch):
    monkeypatch.setattr(main, "HOSTCHECK_ENABLED", True)
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


def test_should_not_reverify_during_connection():
    rec = main.PostRecord(
        post=main.Post(addr="203.0.113.1:10800"),
        owner_token="t",
        creator_ip="1.2.3.4",
        last_hostcheck_at=0.0,
    )
    assert not main.should_reverify_host_on_update(
        rec,
        addr="203.0.113.1:10800",
        net_status=main.NET_CHECKING,
        now=999.0,
    )


def test_should_not_reverify_when_guest_connected():
    rec = main.PostRecord(
        post=main.Post(addr="203.0.113.1:10800", guest_connected=True),
        owner_token="t",
        creator_ip="1.2.3.4",
        last_hostcheck_at=0.0,
    )
    assert not main.should_reverify_host_on_update(
        rec,
        addr="203.0.113.1:10800",
        net_status=main.NET_ALIVE,
        now=999.0,
    )


def test_probe_target_records_skips_connection_in_progress():
    quiet = main.PostRecord(
        post=main.Post(id="quiet", addr="203.0.113.1:10800"),
        owner_token="t",
        creator_ip="1.2.3.4",
    )
    busy = main.PostRecord(
        post=main.Post(
            id="busy",
            addr="203.0.113.2:10800",
            net_status=main.NET_CHECKING,
        ),
        owner_token="t2",
        creator_ip="1.2.3.5",
    )
    main.RECORDS.clear()
    main.RECORDS["quiet"] = quiet
    main.RECORDS["busy"] = busy
    try:
        targets = main.probe_target_records()
        assert [rec.post.id for rec in targets] == ["quiet"]
    finally:
        main.RECORDS.clear()


def test_host_probe_kwargs_relaxed_for_giuroll():
    relaxed = main.host_probe_kwargs(giuroll=True)
    normal = main.host_probe_kwargs(giuroll=False)
    assert relaxed["needed_consecutive"] < normal["needed_consecutive"]
    assert relaxed["timeout_sec"] > normal["timeout_sec"]


@pytest.mark.asyncio
async def test_verify_giuroll_uses_relaxed_probe(monkeypatch):
    seen: list[dict[str, object]] = []

    def fake_consecutive(host: str, port: int, **kwargs):
        seen.append(dict(kwargs))
        return True

    monkeypatch.setattr(main, "HOSTCHECK_ENABLED", True)
    monkeypatch.setattr(main, "check_hostable_consecutive", fake_consecutive)
    monkeypatch.setattr(main, "check_hostable_autopunch", lambda *a, **k: False)

    await main.verify_hostable_or_raise(
        "203.0.113.1:10800",
        autopunch=False,
        giuroll=True,
    )
    assert seen[0]["needed_consecutive"] == 1
    assert seen[0]["timeout_sec"] == 0.35
    assert seen[1]["needed_consecutive"] == 3


@pytest.mark.asyncio
async def test_update_soft_fails_reverify_when_addr_unchanged(monkeypatch):
    calls = 0

    async def fail_after_create(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return True, False
        raise main.HTTPException(status_code=409, detail={"message": "host not reachable"})

    monkeypatch.setattr(main, "HOSTCHECK_ENABLED", True)
    monkeypatch.setattr(main, "should_reverify_host_on_update", lambda *a, **k: True)
    monkeypatch.setattr(main, "verify_hostable_or_raise", fail_after_create)

    async with app_client() as client:
        await create_user("host1", name="host")
        token = bearer_token("host1", "host")
        create = await client.post(
            "/posts",
            json={"post_type": "casual", "addr": "203.0.113.1:10800", "giuroll": True},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert create.status_code == 200
        body = create.json()
        post = body["post"]
        owner_token = body["owner_token"]

        updated = await client.post(
            "/posts/update",
            json={
                "id": post["id"],
                "owner_token": owner_token,
                "post_type": "casual",
                "addr": "203.0.113.1:10800",
                "giuroll": True,
                "autopunch": False,
                "comment": "",
                "stream_url": "",
                "match_status": "",
                "net_status": 3,
                "challenge_upper": False,
                "ping_warn_enabled": False,
                "ping_warn_ms": 150,
                "ping_warn_giuroll_ms": 100,
            },
        )
        assert updated.status_code == 200
        data = updated.json()
        assert data["id"] == post["id"]
        assert data["direct_reachable"] is True
        assert data["autopunch"] is False

        listed = await client.get("/posts", headers={"Authorization": f"Bearer {token}"})
        assert any(p["id"] == post["id"] for p in listed.json())


@pytest.mark.asyncio
async def test_verify_autopunch_does_not_trust_lenient_direct(monkeypatch):
    """AP ホストは lenient direct だけでは direct 到達と判定しない。"""
    call_n = 0

    def fake_direct(host: str, port: int, **kwargs):
        nonlocal call_n
        call_n += 1
        needed = int(kwargs.get("needed_consecutive", 2))
        if needed >= 3:
            return False
        return True

    monkeypatch.setattr(main, "HOSTCHECK_ENABLED", True)
    monkeypatch.setattr(main, "check_hostable_consecutive", fake_direct)
    monkeypatch.setattr(main, "check_hostable_autopunch", lambda *a, **k: False)

    with pytest.raises(main.HTTPException) as exc:
        await main.verify_hostable_or_raise("203.0.113.1:10800", autopunch=True)
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_update_keeps_ap_only_when_client_sends_autopunch(monkeypatch):
    """hydrate 後サーバー側 autopunch=false でも、クライアント AP 送信なら降格しない。"""
    calls = 0

    async def flaky_direct(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return False, True
        return True, False

    monkeypatch.setattr(main, "HOSTCHECK_ENABLED", True)
    monkeypatch.setattr(main, "should_reverify_host_on_update", lambda *a, **k: True)
    monkeypatch.setattr(main, "verify_hostable_or_raise", flaky_direct)

    async with app_client() as client:
        await create_user("host3", name="host")
        token = bearer_token("host3", "host")
        create = await client.post(
            "/posts",
            json={
                "post_type": "casual",
                "addr": "203.0.113.1:10800",
                "autopunch": False,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert create.status_code == 200
        body = create.json()
        post = body["post"]
        owner_token = body["owner_token"]
        rec = main.RECORDS[post["id"]]
        rec.post.autopunch = False
        rec.post.direct_reachable = False

        updated = await client.post(
            "/posts/update",
            json={
                "id": post["id"],
                "owner_token": owner_token,
                "post_type": "casual",
                "addr": "203.0.113.1:10800",
                "autopunch": True,
                "comment": "",
                "stream_url": "",
                "match_status": "",
                "net_status": 3,
                "challenge_upper": False,
                "ping_warn_enabled": False,
                "ping_warn_ms": 150,
                "ping_warn_giuroll_ms": 100,
            },
        )
        assert updated.status_code == 200
        data = updated.json()
        assert data["direct_reachable"] is False
        assert data["autopunch"] is True


def test_should_not_reverify_during_startup_grace(monkeypatch):
    monkeypatch.setattr(main, "HOSTCHECK_ENABLED", True)
    main.SERVER_STARTED_AT = 1000.0
    rec = main.PostRecord(
        post=main.Post(addr="203.0.113.1:10800"),
        owner_token="t",
        creator_ip="1.2.3.4",
        last_hostcheck_at=0.0,
    )
    assert not main.should_reverify_host_on_update(
        rec,
        addr="203.0.113.1:10800",
        net_status=0,
        now=1030.0,
    )
    assert main.should_reverify_host_on_update(
        rec,
        addr="203.0.113.1:10801",
        net_status=0,
        now=1030.0,
    )


@pytest.mark.asyncio
async def test_update_keeps_ap_only_when_reverify_flaky_direct(monkeypatch):
    calls = 0

    async def flaky_direct(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return False, True
        return True, False

    monkeypatch.setattr(main, "HOSTCHECK_ENABLED", True)
    monkeypatch.setattr(main, "should_reverify_host_on_update", lambda *a, **k: True)
    monkeypatch.setattr(main, "verify_hostable_or_raise", flaky_direct)

    async with app_client() as client:
        await create_user("host2", name="host")
        token = bearer_token("host2", "host")
        create = await client.post(
            "/posts",
            json={
                "post_type": "casual",
                "addr": "203.0.113.1:10800",
                "autopunch": False,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert create.status_code == 200
        body = create.json()
        post = body["post"]
        owner_token = body["owner_token"]
        assert post["direct_reachable"] is False
        assert post["autopunch"] is True

        updated = await client.post(
            "/posts/update",
            json={
                "id": post["id"],
                "owner_token": owner_token,
                "post_type": "casual",
                "addr": "203.0.113.1:10800",
                "autopunch": False,
                "comment": "",
                "stream_url": "",
                "match_status": "",
                "net_status": 3,
                "challenge_upper": False,
                "ping_warn_enabled": False,
                "ping_warn_ms": 150,
                "ping_warn_giuroll_ms": 100,
            },
        )
        assert updated.status_code == 200
        data = updated.json()
        assert data["direct_reachable"] is False
        assert data["autopunch"] is True


def test_probe_addr_for_hostcheck_replaces_placeholder():
    assert main.probe_addr_for_hostcheck(
        "0.0.0.0:10800",
        fallback_host="203.0.113.50",
    ) == "203.0.113.50:10800"
    assert main.probe_addr_for_hostcheck(
        "203.0.113.1:10800",
        fallback_host="203.0.113.50",
    ) == "203.0.113.1:10800"


def test_ap_badge_hidden_when_direct_even_if_autopunch_flag():
    post = main.Post(autopunch=True, direct_reachable=True)
    show_ap = post.autopunch and not post.direct_reachable
    assert show_ap is False


def test_ap_badge_shown_when_ap_only():
    post = main.Post(autopunch=True, direct_reachable=False)
    show_ap = post.autopunch and not post.direct_reachable
    assert show_ap is True
