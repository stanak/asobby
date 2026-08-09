"""ゲスト誤同定 (無関係 presence による募集汚染) の回帰テスト。"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

os.environ.setdefault("ASOBBY_HOSTCHECK", "off")
os.environ.setdefault(
    "DATABASE_URL",
    "sqlite+aiosqlite:////tmp/asobby_guest_misid_test.db",
)
os.environ.setdefault("ASOBBY_DISCORD_CLIENT_ID", "t")
os.environ.setdefault("ASOBBY_DISCORD_CLIENT_SECRET", "t")
os.environ.setdefault("ASOBBY_SESSION_SECRET", "sec")

import db
import main
from test_ranked import bearer_token, create_user, make_0x08_reply


@pytest.fixture(autouse=True)
def reset_lobby_state() -> None:
    main.LAST_CREATE_AT.clear()
    main.RECORDS.clear()
    main.RANKED_SESSIONS.clear()
    yield
    main.RECORDS.clear()
    main.RANKED_SESSIONS.clear()


@asynccontextmanager
async def app_client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=main.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        async with main.app.router.lifespan_context(main.app):
            yield client


@pytest.mark.asyncio
async def test_presence_does_not_pollute_unrelated_casual_post():
    """別対戦中の presence が guest_ip 未設定の募集を汚染しない。"""
    async with app_client() as client:
        await create_user("100", name="host_a", last_ip="1.1.1.1")
        await create_user("200", name="bystander_c", last_ip="9.9.9.9")

        host_token = bearer_token("100", "host_a")
        res = await client.post(
            "/posts",
            json={"post_type": "casual", "addr": "1.1.1.1:10800"},
            headers={"Authorization": f"Bearer {host_token}"},
        )
        assert res.status_code == 200, res.text
        rec = main.RECORDS[res.json()["post"]["id"]]
        assert rec.guest_ip == ""
        assert rec.guest_user_id == ""

        c_token = bearer_token("200", "bystander_c")
        pres = await client.post(
            "/matches/presence",
            headers={
                "Authorization": f"Bearer {c_token}",
                "X-Forwarded-For": "9.9.9.9",
            },
        )
        assert pres.status_code == 200
        assert rec.guest_ip == ""
        assert rec.guest_user_id == ""
        assert rec.post.guest_name == ""


@pytest.mark.asyncio
async def test_presence_links_after_echo_probe_sets_guest_ip():
    """echo プローブで guest_ip 確定後は presence が user_id を補完する。"""
    async with app_client() as client:
        await create_user("100", name="host_a", last_ip="1.1.1.1")
        await create_user("300", name="real_guest", last_ip="5.5.5.5")

        host_token = bearer_token("100", "host_a")
        res = await client.post(
            "/posts",
            json={"post_type": "casual", "addr": "1.1.1.1:10800"},
            headers={"Authorization": f"Bearer {host_token}"},
        )
        assert res.status_code == 200, res.text
        rec = main.RECORDS[res.json()["post"]["id"]]
        await main.apply_guest_probe(rec, make_0x08_reply("5.5.5.5"))
        rec.guest_user_id = ""
        rec.post.guest_user_id = ""
        rec.post.guest_name = ""

        guest_token = bearer_token("300", "real_guest")
        pres = await client.post(
            "/matches/presence",
            headers={
                "Authorization": f"Bearer {guest_token}",
                "X-Forwarded-For": "5.5.5.5",
            },
        )
        assert pres.status_code == 200
        assert rec.guest_user_id == "300"
        assert rec.guest_ip == "5.5.5.5"


@pytest.mark.asyncio
async def test_battle_start_reprobe_overwrites_wrong_guest_ip(monkeypatch):
    """キャラセレ突入時の再プローブで誤った guest_ip を echo 結果で上書きする。"""
    async with app_client() as client:
        await create_user("100", name="host_a", last_ip="1.1.1.1")
        await create_user("200", name="wrong_c", last_ip="9.9.9.9")

        host_token = bearer_token("100", "host_a")
        res = await client.post(
            "/posts",
            json={"post_type": "casual", "addr": "1.1.1.1:10800"},
            headers={"Authorization": f"Bearer {host_token}"},
        )
        assert res.status_code == 200, res.text
        post = res.json()["post"]
        owner_token = res.json()["owner_token"]
        rec = main.RECORDS[post["id"]]

        rec.guest_ip = "9.9.9.9"
        main._apply_guest_identity(rec, await db.get_user("200"))
        assert rec.guest_user_id == "200"

        async def fake_probe(record: main.PostRecord, *, force: bool = False) -> None:
            assert force is True
            await main.apply_guest_probe(record, make_0x08_reply("8.8.8.8"))

        monkeypatch.setattr(main, "probe_guest_for_record", fake_probe)

        upd = await client.post(
            "/posts/update",
            json={
                "id": post["id"],
                "owner_token": owner_token,
                "post_type": "casual",
                "addr": "1.1.1.1:10800",
                "net_status": main.NET_CHECKING,
            },
            headers={"Authorization": f"Bearer {host_token}"},
        )
        assert upd.status_code == 200
        assert rec.guest_ip == "8.8.8.8"
        assert rec.guest_user_id == ""


@pytest.mark.asyncio
async def test_casual_result_not_saved_as_bystander(monkeypatch):
    """非導入ゲスト (8.8.8.8) 対戦後、傍観者 C を guest_user_id にしない。"""
    async with app_client() as client:
        await create_user("100", name="host_a", last_ip="1.1.1.1")
        await create_user("200", name="bystander_c", last_ip="9.9.9.9")

        host_token = bearer_token("100", "host_a")
        res = await client.post(
            "/posts",
            json={"post_type": "casual", "addr": "1.1.1.1:10800"},
            headers={"Authorization": f"Bearer {host_token}"},
        )
        assert res.status_code == 200, res.text
        post = res.json()["post"]
        owner_token = res.json()["owner_token"]
        rec = main.RECORDS[post["id"]]

        c_token = bearer_token("200", "bystander_c")
        await client.post(
            "/matches/presence",
            headers={
                "Authorization": f"Bearer {c_token}",
                "X-Forwarded-For": "9.9.9.9",
            },
        )
        assert rec.guest_user_id == ""

        await main.apply_guest_probe(rec, make_0x08_reply("8.8.8.8"))
        assert rec.guest_ip == "8.8.8.8"
        assert rec.guest_user_id == ""

        r = await client.post(
            "/posts/result",
            json={
                "id": post["id"],
                "owner_token": owner_token,
                "winner": "host",
                "host_char": 0,
                "guest_char": 1,
                "host_profile": "host_prof_misid",
                "guest_profile": "b_guest_prof_misid",
            },
        )
        assert r.status_code == 200
        assert r.json()["recorded"] is True

        async with db.session() as s:
            res_m = await s.execute(
                select(db.Match).where(db.Match.guest_profile == "b_guest_prof_misid")
            )
            matches = list(res_m.scalars().all())
            assert len(matches) == 1
            assert matches[0].guest_user_id in (None, "")
            assert matches[0].guest_ip == "8.8.8.8"
