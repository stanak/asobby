"""ランクマッチ機能の結合テスト。"""
from __future__ import annotations

import os
import socket
import struct
from contextlib import asynccontextmanager
from typing import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

# テスト用環境変数 (import 前に設定)
os.environ.setdefault("ASOBBY_HOSTCHECK", "off")
os.environ.setdefault(
    "DATABASE_URL",
    "sqlite+aiosqlite:////tmp/asobby_ranked_test.db",
)
os.environ.setdefault("ASOBBY_DISCORD_CLIENT_ID", "t")
os.environ.setdefault("ASOBBY_DISCORD_CLIENT_SECRET", "t")
os.environ.setdefault("ASOBBY_SESSION_SECRET", "sec")

import db
import main


def make_0x08_reply(ip: str, port: int = 10800) -> bytes:
    return bytes([0x08]) + b"\x00" * 6 + struct.pack("!H", port) + socket.inet_aton(ip)


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
    last_ip: str = "",
    rank: str = "normal",
) -> None:
    async with db.session() as s:
        user = await s.get(db.User, user_id)
        if user is None:
            user = db.User(id=user_id, name=name, last_ip=last_ip, rank=rank)
            s.add(user)
        else:
            user.name = name
            user.last_ip = last_ip
            user.rank = rank
        await s.commit()


async def insert_ranked_win(
    host_user_id: str,
    guest_user_id: str,
    *,
    winner: str = "host",
) -> None:
    await db.insert_match_result(
        host_user_id=host_user_id,
        guest_user_id=guest_user_id,
        host_ip="1.2.3.4",
        guest_ip="5.6.7.8",
        winner=winner,
        ranked=True,
    )


@pytest.fixture(autouse=True)
def clean_state(tmp_path):
    db_path = tmp_path / "asobby_ranked_test.db"
    url = f"sqlite+aiosqlite:///{db_path}"
    os.environ["DATABASE_URL"] = url
    main.DATABASE_URL = url
    main.RECORDS.clear()
    main.LAST_CREATE_AT.clear()
    main.LOGOUT_REVOKED.clear()
    yield
    main.RECORDS.clear()


@pytest.mark.asyncio
async def test_ranked_match_flow():
    async with app_client() as client:
        await create_user("999", name="host", last_ip="1.2.3.4")
        await create_user("888", name="guest", last_ip="5.6.7.8")

        # users.rank デフォルト normal
        async with db.session() as s:
            host = await s.get(db.User, "999")
            assert host is not None
            assert host.rank == "normal"

        token = bearer_token("999", "host")
        res = await client.post(
            "/posts",
            json={"post_type": "ranked", "addr": "1.2.3.4:10800"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 200
        body = res.json()
        post = body["post"]
        owner_token = body["owner_token"]
        assert post["rank"] == "normal"
        assert post["post_type"] == "ranked"

        rec = main.RECORDS[post["id"]]
        await main.apply_guest_probe(rec, make_0x08_reply("5.6.7.8"))
        assert rec.post.guest_connected is True
        assert rec.post.ranked_active is True

        for i in range(4):
            r = await client.post(
                "/posts/result",
                json={
                    "id": post["id"],
                    "owner_token": owner_token,
                    "winner": "host",
                    "host_char": 0,
                    "guest_char": 1,
                    "host_profile": "hp",
                    "guest_profile": "gp",
                },
            )
            assert r.status_code == 200
            data = r.json()
            assert data["recorded"] is True
            expected_ranked = i < 3
            assert data["ranked"] is expected_ranked

        async with db.session() as s:
            res_m = await s.execute(select(db.Match))
            matches = list(res_m.scalars().all())
            assert len(matches) == 4
            ranked_flags = [m.ranked for m in matches]
            assert ranked_flags == [True, True, True, False]
            ranked_match_ranks = [m.match_rank for m in matches[:3]]
            assert ranked_match_ranks == ["normal", "normal", "normal"]
            assert matches[3].match_rank is None

        host_token = bearer_token("999", "host")
        stats_matches = await client.get(
            "/stats/me/matches",
            headers={"Authorization": f"Bearer {host_token}"},
        )
        assert stats_matches.status_code == 200
        rows = stats_matches.json()["matches"]
        ranked_rows = [r for r in rows if r["ranked"]]
        assert len(ranked_rows) == 3
        assert all(r["match_rank"] == "normal" for r in ranked_rows)


@pytest.mark.asyncio
async def test_rank_promotion_and_demotion():
    async with app_client() as client:
        await create_user("999", name="host")
        await create_user("888", name="guest")

        # easy からの昇格を検証するため明示的に easy を設定
        async with db.session() as s:
            host = await s.get(db.User, "999")
            host.rank = "easy"
            await s.commit()

        for _ in range(10):
            await insert_ranked_win("999", "888", winner="host")

        new_rank = await main.evaluate_rank("999")
        assert new_rank == "normal"

        async with db.session() as s:
            user = await s.get(db.User, "999")
            assert user.rank == "normal"
            assert user.rank_changed_at is not None

        again = await main.evaluate_rank("999")
        assert again is None

        await create_user("777", name="exuser", rank="ex")
        await insert_ranked_win("777", "888", winner="host")
        for _ in range(9):
            await insert_ranked_win("777", "888", winner="guest")
        demoted = await main.evaluate_rank("777")
        assert demoted == "normal"

        await create_user("666", name="normaluser", rank="normal")
        for _ in range(2):
            await insert_ranked_win("666", "888", winner="host")
        for _ in range(8):
            await insert_ranked_win("666", "888", winner="guest")
        stay = await main.evaluate_rank("666")
        assert stay is None
        async with db.session() as s:
            user = await s.get(db.User, "666")
            assert user.rank == "normal"


@pytest.mark.asyncio
async def test_trueskill_ph_vs_ph():
    async with app_client() as client:
        await create_user("111", name="ph1", last_ip="1.1.1.1", rank="ph")
        await create_user("222", name="ph2", last_ip="2.2.2.2", rank="ph")

        async with db.session() as s:
            u1 = await s.get(db.User, "111")
            u2 = await s.get(db.User, "222")
            init_mu1, init_mu2 = u1.ts_mu, u2.ts_mu

        token = bearer_token("111", "ph1")
        res = await client.post(
            "/posts",
            json={"post_type": "ranked", "addr": "1.1.1.1:10800"},
            headers={"Authorization": f"Bearer {token}"},
        )
        post = res.json()["post"]
        owner_token = res.json()["owner_token"]
        rec = main.RECORDS[post["id"]]
        await main.apply_guest_probe(rec, make_0x08_reply("2.2.2.2"))

        r = await client.post(
            "/posts/result",
            json={
                "id": post["id"],
                "owner_token": owner_token,
                "winner": "host",
                "host_char": 0,
                "guest_char": 1,
            },
        )
        assert r.json()["ranked"] is True

        async with db.session() as s:
            u1 = await s.get(db.User, "111")
            u2 = await s.get(db.User, "222")
            assert u1.rank == "ph"
            assert u2.rank == "ph"
            assert u1.ts_mu > init_mu1
            assert u2.ts_mu < init_mu2


@pytest.mark.asyncio
async def test_auth_me_and_stats_me():
    async with app_client() as client:
        await create_user("999", name="host", rank="ex")
        token = bearer_token("999", "host")

        me = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200
        me_body = me.json()
        assert me_body["rank"] == "ex"
        assert me_body["rating"] is None

        for i in range(5):
            await insert_ranked_win("999", "888", winner="host" if i % 2 == 0 else "guest")

        stats = await client.get("/stats/me", headers={"Authorization": f"Bearer {token}"})
        assert stats.status_code == 200
        data = stats.json()
        assert data["ranked"]["rank"] == "ex"
        assert data["ranked"]["total"]["games"] == 5
        assert data["ranked"]["recent50"]["games"] == 5


@pytest.mark.asyncio
async def test_different_rank_guest_not_ranked():
    async with app_client() as client:
        await create_user("999", name="host", last_ip="1.2.3.4", rank="normal")
        await create_user("888", name="guest", last_ip="5.6.7.8", rank="hard")

        token = bearer_token("999", "host")
        res = await client.post(
            "/posts",
            json={"post_type": "ranked", "addr": "1.2.3.4:10800"},
            headers={"Authorization": f"Bearer {token}"},
        )
        post = res.json()["post"]
        owner_token = res.json()["owner_token"]
        rec = main.RECORDS[post["id"]]
        await main.apply_guest_probe(rec, make_0x08_reply("5.6.7.8"))
        assert rec.post.ranked_active is False

        for _ in range(4):
            r = await client.post(
                "/posts/result",
                json={
                    "id": post["id"],
                    "owner_token": owner_token,
                    "winner": "host",
                },
            )
            assert r.json()["ranked"] is False


@pytest.mark.asyncio
async def test_legacy_post_body():
    async with app_client() as client:
        await create_user("999", name="host")
        token = bearer_token("999", "host")
        res = await client.post(
            "/posts",
            json={"rank": "any", "addr": "1.2.3.4:10800"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 200
        assert res.json()["post"]["post_type"] == "casual"
