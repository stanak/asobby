"""初回開始ランク選択の結合テスト。"""
from __future__ import annotations

import os
import socket
import struct
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

os.environ.setdefault("ASOBBY_HOSTCHECK", "off")
os.environ.setdefault(
    "DATABASE_URL",
    "sqlite+aiosqlite:////tmp/asobby_initrank_test.db",
)
os.environ.setdefault("ASOBBY_DISCORD_CLIENT_ID", "t")
os.environ.setdefault("ASOBBY_DISCORD_CLIENT_SECRET", "t")
os.environ.setdefault("ASOBBY_SESSION_SECRET", "sec")

import db
import integrations
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
    rank: str | None = None,
    rank_locked: bool = False,
) -> None:
    async with db.session() as s:
        user = await s.get(db.User, user_id)
        if user is None:
            kwargs: dict = {
                "id": user_id,
                "name": name,
                "last_ip": last_ip,
                "rank_locked": rank_locked,
            }
            if rank is not None:
                kwargs["rank"] = rank
            user = db.User(**kwargs)
            s.add(user)
        else:
            user.name = name
            user.last_ip = last_ip
            user.rank_locked = rank_locked
            if rank is not None:
                user.rank = rank
        await s.commit()


@pytest.fixture(autouse=True)
def clean_state(tmp_path):
    db_path = tmp_path / "asobby_initrank_test.db"
    url = f"sqlite+aiosqlite:///{db_path}"
    os.environ["DATABASE_URL"] = url
    main.DATABASE_URL = url
    main.RECORDS.clear()
    main.RANKED_SESSIONS.clear()
    main.LAST_CREATE_AT.clear()
    main.LOGOUT_REVOKED.clear()
    yield
    main.RECORDS.clear()
    main.RANKED_SESSIONS.clear()


@pytest.mark.asyncio
async def test_new_user_default_normal_and_can_choose():
    async with app_client() as client:
        await create_user("111", name="newbie")
        token = bearer_token("111", "newbie")

        async with db.session() as s:
            user = await s.get(db.User, "111")
            assert user.rank == "normal"
            assert user.rank_locked is False

        me = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200
        body = me.json()
        assert body["rank"] == "normal"
        assert body["can_choose_rank"] is True


@pytest.mark.asyncio
async def test_choose_initial_rank_luna():
    async with app_client() as client:
        await create_user("111", name="picker")
        token = bearer_token("111", "picker")

        res = await client.post(
            "/rank/initial",
            json={"rank": "luna"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 200
        assert res.json() == {"ok": True, "rank": "luna"}

        async with db.session() as s:
            user = await s.get(db.User, "111")
            assert user.rank == "luna"
            assert user.rank_locked is True
            assert user.rank_changed_at is not None

        me = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me.json()["can_choose_rank"] is False


@pytest.mark.asyncio
async def test_choose_initial_rank_twice_conflict():
    async with app_client() as client:
        await create_user("111", name="picker")
        token = bearer_token("111", "picker")

        first = await client.post(
            "/rank/initial",
            json={"rank": "ex"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert first.status_code == 200

        second = await client.post(
            "/rank/initial",
            json={"rank": "hard"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert second.status_code == 409


@pytest.mark.asyncio
async def test_choose_initial_rank_ph_rejected():
    async with app_client() as client:
        await create_user("111", name="picker")
        token = bearer_token("111", "picker")

        res = await client.post(
            "/rank/initial",
            json={"rank": "ph"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 422


@pytest.mark.asyncio
async def test_choose_initial_rank_unauthorized():
    async with app_client() as client:
        res = await client.post("/rank/initial", json={"rank": "normal"})
        assert res.status_code == 401


@pytest.mark.asyncio
async def test_ranked_game_locks_both_users():
    async with app_client() as client:
        await create_user("999", name="host", last_ip="1.2.3.4")
        await create_user("888", name="guest", last_ip="5.6.7.8")

        host_token = bearer_token("999", "host")
        guest_token = bearer_token("888", "guest")

        res = await client.post(
            "/posts",
            json={"post_type": "ranked", "addr": "1.2.3.4:10800"},
            headers={"Authorization": f"Bearer {host_token}"},
        )
        post = res.json()["post"]
        owner_token = res.json()["owner_token"]
        rec = main.RECORDS[post["id"]]
        await main.apply_guest_probe(rec, make_0x08_reply("5.6.7.8"))
        assert rec.post.ranked_active is True

        r = await client.post(
            "/posts/result",
            json={
                "id": post["id"],
                "owner_token": owner_token,
                "winner": "host",
            },
        )
        assert r.status_code == 200
        assert r.json()["ranked"] is True

        async with db.session() as s:
            host = await s.get(db.User, "999")
            guest = await s.get(db.User, "888")
            assert host.rank_locked is True
            assert guest.rank_locked is True

        for token in (host_token, guest_token):
            locked = await client.post(
                "/rank/initial",
                json={"rank": "easy"},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert locked.status_code == 409

        # First confirmed ranked result updates the public listing immediately.
        assert rec.post.rank_status == "provisional"
        assert rec.post.ranked_games == 1
        guest_details = await main.host_rank_for_post("888")
        assert guest_details["rank_status"] == "provisional"
        assert guest_details["ranked_games"] == 1


async def add_rank_history(user_id, games, *, ranked=True, winner="host", guest_side=False):
    async with db.session() as s:
        s.add_all([
            db.Match(
                host_user_id=None if guest_side else user_id,
                guest_user_id=user_id if guest_side else None,
                ranked=ranked, winner=winner, played_at=db.utcnow(), match_rank="normal" if ranked else None,
            )
            for _ in range(games)
        ])
        await s.commit()


@pytest.mark.asyncio
async def test_rank_evidence_same_normal_choice_updates_lobby_api_and_sse():
    async with app_client() as client:
        await create_user("111")
        headers = {"Authorization": "Bearer " + bearer_token("111")}
        created = await client.post("/posts", headers=headers, json={"addr": "1.2.3.4:10800"})
        assert created.status_code == 200
        post = created.json()["post"]
        assert (post["rank"], post["rank_status"], post["ranked_games"]) == ("normal", "unset", 0)
        cred = await main.INTEGRATIONS.create(integrations.IntegrationInput(name="rank-reader"))
        api_headers = {"Authorization": "Bearer " + cred["api_key"]}
        before = await client.get("/api/v1/lobby", headers=api_headers)
        assert before.json()["posts"][0]["rank_status"] == "unset"
        q = await main.HUB.subscribe()
        try:
            choice = await client.post("/rank/initial", headers=headers, json={"rank": "normal"})
            assert choice.status_code == 200
            event = q.get_nowait()
            assert '"rank_status":"initial"' in event and '"ranked_games":0' in event
        finally:
            await main.HUB.unsubscribe(q)
        lobby = (await client.get("/posts", headers=headers)).json()[0]
        assert (lobby["rank"], lobby["rank_status"], lobby["ranked_games"]) == ("normal", "initial", 0)
        assert lobby["updated_at"] == post["updated_at"]  # no heartbeat lease renewal
        after = await client.get("/api/v1/lobby", headers={**api_headers, "If-None-Match": before.headers["etag"]})
        assert after.status_code == 200
        assert after.json()["posts"][0]["rank_status"] == "initial"
        assert not {"owner_user_id", "rank_locked", "owner_token"} & after.json()["posts"][0].keys()
        assert after.json()["revision"] != before.json()["revision"]


@pytest.mark.asyncio
@pytest.mark.parametrize("games,status", [(0, "initial"), (1, "provisional"), (49, "provisional"), (50, "ranked"), (51, "ranked")])
async def test_rank_evidence_boundaries_and_both_player_sides(games, status):
    async with app_client():
        await create_user("111", rank_locked=True)
        await add_rank_history("111", games // 2)
        await add_rank_history("111", games - games // 2, guest_side=True, winner="draw")
        await add_rank_history("111", 60, ranked=False)
        await add_rank_history("111", 60, winner="")
        details = await main.host_rank_for_post("111")
        assert details["rank"] == "normal"
        assert details["rank_status"] == status and details["ranked_games"] == games


@pytest.mark.asyncio
async def test_rank_evidence_survives_promotion_and_unknown_is_not_zero():
    async with app_client():
        await create_user("111", rank="luna", rank_locked=True)
        await add_rank_history("111", 50)
        await db.set_user_rank("111", "ph")
        assert not await db.fetch_ranked_matches_at_current_rank("111")
        details = await main.host_rank_for_post("111")
        assert details["rank"] == "ph" and details["rating"] is not None
        assert details["rank_status"] == "ranked" and details["ranked_games"] == 50
        unknown = await main.host_rank_for_post("missing")
        assert unknown["rank_status"] == "unknown" and unknown["ranked_games"] is None


@pytest.mark.asyncio
async def test_update_cannot_forge_rank_evidence_and_refreshes_unchanged_rank():
    async with app_client() as client:
        await create_user("111", rank_locked=True)
        headers = {"Authorization": "Bearer " + bearer_token("111")}
        created = await client.post("/posts", headers=headers, json={
            "addr": "1.2.3.4:10800", "rank_status": "ranked", "ranked_games": 999,
        })
        data = created.json()
        assert data["post"]["rank_status"] == "initial" and data["post"]["ranked_games"] == 0
        await add_rank_history("111", 49)
        updated = await client.post("/posts/update", json={
            "id": data["post"]["id"], "owner_token": data["owner_token"], "addr": "1.2.3.4:10800",
            "rank_status": "ranked", "ranked_games": 999,
        })
        assert updated.status_code == 200
        rec = main.RECORDS[data["post"]["id"]]
        assert rec.post.rank == "normal" and rec.post.rank_status == "provisional"
        assert rec.post.ranked_games == 49
        await add_rank_history("111", 1)
        await main.refresh_active_post_ranks({"111"})
        assert rec.post.rank == "normal" and rec.post.rank_status == "ranked"
        assert rec.post.ranked_games == 50


@pytest.mark.asyncio
async def test_hydrate_recomputes_rank_evidence_for_older_records(monkeypatch):
    async with app_client():
        await create_user("111", rank_locked=True)
        await add_rank_history("111", 3)
        now = time.time()
        rec = main.PostRecord(
            post=main.Post(id="old", rank="normal", created_at=now, updated_at=now),
            owner_token="test", creator_ip="", owner_user_id="111",
        )
        data = main.post_record_to_dict(rec)
        data["post"].pop("rank_status")
        data["post"].pop("ranked_games")
        monkeypatch.setattr(main.post_redis, "is_configured", lambda: True)
        monkeypatch.setattr(main.post_redis, "load_all_record_dicts", lambda: [data])
        await main._hydrate_records_from_redis()
        restored = main.RECORDS["old"].post
        assert restored.rank_status == "provisional" and restored.ranked_games == 3
