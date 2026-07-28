"""ユーザー設定 API と favicon 通知判定のテスト。"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

os.environ.setdefault("ASOBBY_HOSTCHECK", "off")
os.environ.setdefault(
    "DATABASE_URL",
    "sqlite+aiosqlite:////tmp/asobby_settings_test.db",
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


async def create_user(
    user_id: str,
    *,
    name: str = "user",
    rank: str = "normal",
) -> None:
    async with db.session() as s:
        user = await s.get(db.User, user_id)
        if user is None:
            user = db.User(id=user_id, name=name, rank=rank, rank_locked=True)
            s.add(user)
        else:
            user.name = name
            user.rank = rank
        await s.commit()


def add_post(
    *,
    owner_user_id: str,
    owner_name: str,
    post_type: str = "casual",
    rank: str = "normal",
    guest_connected: bool = False,
    net_status: int = 0,
) -> str:
    now = main.now_ts()
    post = main.Post(
        post_type=post_type,
        rank=rank,
        addr="1.2.3.4:10800",
        owner_name=owner_name,
        guest_connected=guest_connected,
        net_status=net_status,
        created_at=now,
        updated_at=now,
    )
    rec = main.PostRecord(
        post=post,
        owner_token="tok",
        creator_ip="9.9.9.9",
        owner_user_id=owner_user_id,
    )
    main.RECORDS[post.id] = rec
    return post.id


@pytest.fixture(autouse=True)
def clean_state(tmp_path):
    db_path = tmp_path / "asobby_settings_test.db"
    url = f"sqlite+aiosqlite:///{db_path}"
    os.environ["DATABASE_URL"] = url
    main.DATABASE_URL = url
    main.RECORDS.clear()
    main.LAST_CREATE_AT.clear()
    main.LOGOUT_REVOKED.clear()
    yield
    main.RECORDS.clear()


@pytest.mark.asyncio
async def test_get_and_patch_user_settings():
    async with app_client() as client:
        await create_user("u1", name="viewer", rank="normal")
        token = bearer_token("u1", "viewer")

        res = await client.get(
            "/user/settings",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 200
        assert res.json()["favicon_notify"]["max_ping_ms"] == 60
        assert res.json()["replay_refusal_until"] == 0

        patch = await client.patch(
            "/user/settings",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "favicon_notify": {
                    "ranked_enabled": False,
                    "max_ping_ms": 45,
                    "require_ping": True,
                }
            },
        )
        assert patch.status_code == 200
        body = patch.json()["favicon_notify"]
        assert body["ranked_enabled"] is False
        assert body["casual_enabled"] is True
        assert body["max_ping_ms"] == 45
        assert body["require_ping"] is True

        refusal = await client.patch(
            "/user/settings",
            headers={"Authorization": f"Bearer {token}"},
            json={"replay_refusal_until": -1},
        )
        assert refusal.status_code == 200
        assert refusal.json()["replay_refusal_until"] == -1

        clear = await client.patch(
            "/user/settings",
            headers={"Authorization": f"Bearer {token}"},
            json={"replay_refusal_until": 0},
        )
        assert clear.status_code == 200
        assert clear.json()["replay_refusal_until"] == 0


@pytest.mark.asyncio
async def test_auth_me_includes_settings_and_badges():
    async with app_client() as client:
        await create_user("u1", name="viewer", rank="normal")
        await create_user("u2", name="host", rank="normal")
        add_post(owner_user_id="u2", owner_name="host", post_type="ranked", rank="normal")
        token = bearer_token("u1", "viewer")

        res = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert res.status_code == 200
        body = res.json()
        assert "settings" in body
        assert body["favicon_badges"]["ranked"] is True
        assert body["favicon_badges"]["casual"] is False


@pytest.mark.asyncio
async def test_auth_me_records_client_version():
    async with app_client() as client:
        await create_user("u1", name="viewer", rank="normal")
        token = bearer_token("u1", "viewer")
        headers = {
            "Authorization": f"Bearer {token}",
            "X-Asobby-Client-Version": "0.5.9",
        }

        res = await client.get("/auth/me", headers=headers)
        assert res.status_code == 200

        async with db.session() as s:
            user = await s.get(db.User, "u1")
            assert user is not None
            assert user.client_version == "0.5.9"

        res2 = await client.get(
            "/auth/me",
            headers={
                "Authorization": f"Bearer {token}",
                "X-Asobby-Client-Version": "0.6.0",
            },
        )
        assert res2.status_code == 200
        async with db.session() as s:
            user = await s.get(db.User, "u1")
            assert user is not None
            assert user.client_version == "0.6.0"


def test_classify_post_notify_rank_band_and_battle():
    prefs = db.normalize_favicon_notify(
        {"ranked_same_band_only": True, "exclude_in_battle": True}
    )
    ranked_ok, casual_ok = main.classify_post_notify(
        main.Post(post_type="ranked", rank="normal"),
        prefs=prefs,
        viewer_rank="normal",
    )
    assert ranked_ok is True
    assert casual_ok is False

    ranked_wrong, _ = main.classify_post_notify(
        main.Post(post_type="ranked", rank="ex"),
        prefs=prefs,
        viewer_rank="normal",
    )
    assert ranked_wrong is False

    ranked_battle, _ = main.classify_post_notify(
        main.Post(post_type="ranked", rank="normal", guest_connected=True),
        prefs=prefs,
        viewer_rank="normal",
    )
    assert ranked_battle is False

    _, casual_ok = main.classify_post_notify(
        main.Post(post_type="casual"),
        prefs=prefs,
        viewer_rank="normal",
    )
    assert casual_ok is True


@pytest.mark.asyncio
async def test_list_posts_returns_all_rank_bands():
    """GET /posts must not filter ranked listings by viewer rank (UI shows all bands)."""
    async with app_client() as client:
        await create_user("u1", name="viewer", rank="normal")
        await create_user("u2", name="host_n", rank="normal")
        await create_user("u3", name="host_ex", rank="ex")
        add_post(owner_user_id="u2", owner_name="host_n", post_type="ranked", rank="normal")
        add_post(owner_user_id="u3", owner_name="host_ex", post_type="ranked", rank="ex")
        token = bearer_token("u1", "viewer")

        res = await client.get("/posts", headers={"Authorization": f"Bearer {token}"})
        assert res.status_code == 200
        ranks = sorted(
            (p.get("rank") or "").lower()
            for p in res.json()
            if (p.get("post_type") or "casual") == "ranked"
        )
        assert ranks == ["ex", "normal"]


def test_compute_favicon_badges_excludes_own_post():
    main.RECORDS.clear()
    add_post(owner_user_id="u1", owner_name="me", post_type="casual")
    add_post(owner_user_id="u2", owner_name="other", post_type="casual")
    prefs = db.normalize_favicon_notify({})
    badges = main.compute_favicon_badges("u1", "me", "normal", prefs)
    assert badges == {"ranked": False, "casual": True}


def test_ping_filter_require_and_optional():
    strict = db.normalize_favicon_notify({"require_ping": True, "max_ping_ms": 60})
    assert main.ping_passes_notify(strict, None) is False
    assert main.ping_passes_notify(strict, 40) is True
    assert main.ping_passes_notify(strict, 80) is False

    relaxed = db.normalize_favicon_notify({"require_ping": False, "max_ping_ms": 60})
    assert main.ping_passes_notify(relaxed, None) is True
    assert main.ping_passes_notify(relaxed, 80) is False
