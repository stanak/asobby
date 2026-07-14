"""リプレイ検索 API の結合テスト。"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

os.environ.setdefault("ASOBBY_HOSTCHECK", "off")
os.environ.setdefault(
    "DATABASE_URL",
    "sqlite+aiosqlite:////tmp/asobby_replay_search_test.db",
)
os.environ.setdefault("ASOBBY_DISCORD_CLIENT_ID", "t")
os.environ.setdefault("ASOBBY_DISCORD_CLIENT_SECRET", "t")
os.environ.setdefault("ASOBBY_SESSION_SECRET", "sec")

import db
import main

REPLAY_DATA = b"REPLAYDATA" * 100


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
    ts_mu: float = 25.0,
    ts_sigma: float = 8.333333333333334,
) -> None:
    async with db.session() as s:
        user = await s.get(db.User, user_id)
        if user is None:
            user = db.User(
                id=user_id,
                name=name,
                rank=rank,
                ts_mu=ts_mu,
                ts_sigma=ts_sigma,
            )
            s.add(user)
        else:
            user.name = name
            user.rank = rank
            user.ts_mu = ts_mu
            user.ts_sigma = ts_sigma
        await s.commit()


async def create_match_with_replay(
    match_id: str,
    *,
    host_user_id: str | None = None,
    guest_user_id: str | None = None,
    host_char: int = 0,
    guest_char: int = 5,
    host_profile: str = "hostprof",
    guest_profile: str = "guestprof",
    winner: str = "host",
    ranked: bool = False,
    match_rank: str | None = None,
    played_at: datetime,
    with_replay: bool = True,
) -> None:
    async with db.session() as s:
        match = db.Match(
            id=match_id,
            host_user_id=host_user_id,
            guest_user_id=guest_user_id,
            winner=winner,
            host_char=host_char,
            guest_char=guest_char,
            host_profile=host_profile,
            guest_profile=guest_profile,
            ranked=ranked,
            match_rank=match_rank,
            source="host",
            played_at=played_at,
        )
        s.add(match)
        if with_replay:
            s.add(db.Replay(
                match_id=match_id,
                filename=f"{match_id}.rep",
                size=len(REPLAY_DATA),
                data=REPLAY_DATA,
            ))
        await s.commit()


@pytest.fixture(autouse=True)
def clean_state(tmp_path):
    db_path = tmp_path / "asobby_replay_search_test.db"
    url = f"sqlite+aiosqlite:///{db_path}"
    os.environ["DATABASE_URL"] = url
    main.DATABASE_URL = url
    main.RECORDS.clear()
    main.LAST_CREATE_AT.clear()
    main.LOGOUT_REVOKED.clear()
    yield
    main.RECORDS.clear()


@pytest.mark.asyncio
async def test_replay_search_filters():
    async with app_client() as client:
        await create_user("u1", name="AliceDiscord")
        await create_user("u2", name="BobDiscord")

        t1 = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        t2 = datetime(2025, 6, 2, 12, 0, 0, tzinfo=timezone.utc)
        t3 = datetime(2025, 6, 3, 12, 0, 0, tzinfo=timezone.utc)

        await create_match_with_replay(
            "m1" + "0" * 30,
            host_user_id="u1",
            guest_user_id="u2",
            host_char=0,
            guest_char=5,
            host_profile="AlphaHost",
            guest_profile="BetaGuest",
            played_at=t1,
        )
        await create_match_with_replay(
            "m2" + "0" * 30,
            host_user_id="u2",
            guest_user_id="u1",
            host_char=5,
            guest_char=0,
            host_profile="BobHost",
            guest_profile="AliceGuest",
            played_at=t2,
        )
        await create_match_with_replay(
            "m3" + "0" * 30,
            host_char=3,
            guest_char=7,
            host_profile="SoloA",
            guest_profile="SoloB",
            played_at=t3,
        )
        await create_match_with_replay(
            "m4" + "0" * 30,
            host_char=1,
            guest_char=2,
            host_profile="NoReplayA",
            guest_profile="NoReplayB",
            played_at=t3,
            with_replay=False,
        )

        by_profile = await client.get("/replays/search", params={"player": "alphahost"})
        assert by_profile.status_code == 200
        ids = {r["match_id"] for r in by_profile.json()["replays"]}
        assert "m1" + "0" * 30 in ids
        assert "m4" + "0" * 30 not in ids

        by_discord = await client.get("/replays/search", params={"player": "bobdiscord"})
        assert by_discord.status_code == 200
        ids = {r["match_id"] for r in by_discord.json()["replays"]}
        assert "m1" + "0" * 30 in ids
        assert "m2" + "0" * 30 in ids

        by_char1 = await client.get("/replays/search", params={"char1": 3})
        assert by_char1.status_code == 200
        ids = {r["match_id"] for r in by_char1.json()["replays"]}
        assert ids == {"m3" + "0" * 30}

        by_mirror = await client.get("/replays/search", params={"char1": 0, "char2": 5})
        assert by_mirror.status_code == 200
        ids = {r["match_id"] for r in by_mirror.json()["replays"]}
        assert "m1" + "0" * 30 in ids
        assert "m2" + "0" * 30 in ids
        assert "m3" + "0" * 30 not in ids

        by_date = await client.get(
            "/replays/search",
            params={"date_from": "2025-06-02", "date_to": "2025-06-02"},
        )
        assert by_date.status_code == 200
        ids = {r["match_id"] for r in by_date.json()["replays"]}
        assert ids == {"m2" + "0" * 30}


@pytest.mark.asyncio
async def test_replay_search_sort_and_paging():
    async with app_client() as client:
        await create_user("h1", name="HostLow", rank="easy")
        await create_user("g1", name="GuestHigh", rank="ph", ts_mu=30.0, ts_sigma=2.0)
        await create_user("h2", name="HostMid", rank="luna")
        await create_user("g2", name="GuestLow", rank="normal")

        t_old = datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        t_new = datetime(2025, 12, 1, 0, 0, 0, tzinfo=timezone.utc)

        await create_match_with_replay(
            "a" * 32,
            host_user_id="h1",
            guest_user_id="g1",
            played_at=t_old,
        )
        await create_match_with_replay(
            "b" * 32,
            host_user_id="h2",
            guest_user_id="g2",
            played_at=t_new,
        )

        rank_res = await client.get(
            "/replays/search",
            params={"sort": "rank", "order": "desc"},
        )
        assert rank_res.status_code == 200
        body = rank_res.json()
        assert body["total"] == 2
        assert body["replays"][0]["match_id"] == "a" * 32

        date_asc = await client.get(
            "/replays/search",
            params={"sort": "date", "order": "asc"},
        )
        assert date_asc.json()["replays"][0]["match_id"] == "a" * 32

        date_desc = await client.get(
            "/replays/search",
            params={"sort": "date", "order": "desc"},
        )
        assert date_desc.json()["replays"][0]["match_id"] == "b" * 32

        page1 = await client.get("/replays/search", params={"limit": 1, "offset": 0})
        assert len(page1.json()["replays"]) == 1
        page2 = await client.get("/replays/search", params={"limit": 1, "offset": 1})
        assert len(page2.json()["replays"]) == 1
        assert page1.json()["replays"][0]["match_id"] != page2.json()["replays"][0]["match_id"]

        clamped = await client.get("/replays/search", params={"limit": 500})
        assert len(clamped.json()["replays"]) <= 100


@pytest.mark.asyncio
async def test_replay_search_public_access():
    async with app_client() as client:
        played_at = datetime(2025, 3, 1, 0, 0, 0, tzinfo=timezone.utc)
        match_id = "c" * 32
        await create_match_with_replay(match_id, played_at=played_at)

        search = await client.get("/replays/search")
        assert search.status_code == 200
        assert search.json()["ok"] is True
        assert any(r["match_id"] == match_id for r in search.json()["replays"])

        dl = await client.get(f"/replays/{match_id}")
        assert dl.status_code == 200
        assert dl.content == REPLAY_DATA

        page = await client.get("/replays")
        assert page.status_code == 200
        assert "リプレイ検索" in page.text


@pytest.mark.asyncio
async def test_replay_search_includes_match_rank():
    async with app_client() as client:
        played_at = datetime(2025, 4, 1, 0, 0, 0, tzinfo=timezone.utc)
        match_id = "d" * 32
        await create_match_with_replay(
            match_id,
            ranked=True,
            match_rank="ex",
            played_at=played_at,
        )

        search = await client.get("/replays/search")
        assert search.status_code == 200
        row = next(r for r in search.json()["replays"] if r["match_id"] == match_id)
        assert row["ranked"] is True
        assert row["match_rank"] == "ex"


@pytest.mark.asyncio
async def test_replay_players_suggest():
    async with app_client() as client:
        await create_user("u1", name="AliceDiscord")
        await create_user("u2", name="BobDiscord")

        t1 = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        t2 = datetime(2025, 6, 2, 12, 0, 0, tzinfo=timezone.utc)

        await create_match_with_replay(
            "p1" + "0" * 30,
            host_user_id="u1",
            guest_user_id="u2",
            host_profile="UniqueProfileHost",
            guest_profile="OtherGuest",
            played_at=t1,
        )
        await create_match_with_replay(
            "p2" + "0" * 30,
            host_char=1,
            guest_char=2,
            host_profile="NoReplayOnly",
            guest_profile="HiddenGuest",
            played_at=t2,
            with_replay=False,
        )

        by_profile = await client.get("/replays/players", params={"q": "uniqueprofile"})
        assert by_profile.status_code == 200
        body = by_profile.json()
        assert body["ok"] is True
        profiles = [s for s in body["suggestions"] if s["kind"] == "profile"]
        assert any(s["name"] == "UniqueProfileHost" for s in profiles)

        by_user = await client.get("/replays/players", params={"q": "alicediscord"})
        assert by_user.status_code == 200
        users = [s for s in by_user.json()["suggestions"] if s["kind"] == "user"]
        assert len(users) == 1
        assert users[0]["name"] == "AliceDiscord"
        assert users[0]["user_id"] == "u1"
        assert "avatar" in users[0]

        case_insensitive = await client.get("/replays/players", params={"q": "ALICE"})
        assert case_insensitive.status_code == 200
        assert any(
            s["kind"] == "user" and s["name"] == "AliceDiscord"
            for s in case_insensitive.json()["suggestions"]
        )

        no_replay = await client.get("/replays/players", params={"q": "noreplayonly"})
        assert no_replay.status_code == 200
        assert not any(
            s["name"] == "NoReplayOnly" for s in no_replay.json()["suggestions"]
        )

        empty_q = await client.get("/replays/players", params={"q": "  "})
        assert empty_q.status_code == 200
        assert empty_q.json()["suggestions"] == []

        clamped = await client.get("/replays/players", params={"q": "a", "limit": 99})
        assert clamped.status_code == 200
        assert len(clamped.json()["suggestions"]) <= 20


@pytest.mark.asyncio
async def test_replay_players_suggest_public_access():
    async with app_client() as client:
        played_at = datetime(2025, 3, 1, 0, 0, 0, tzinfo=timezone.utc)
        await create_match_with_replay("e" * 32, host_profile="PublicProf", played_at=played_at)

        res = await client.get("/replays/players", params={"q": "publicprof"})
        assert res.status_code == 200
        assert res.json()["ok"] is True
