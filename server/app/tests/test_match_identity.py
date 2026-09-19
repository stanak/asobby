"""Regression: 6m22s skew, wrong-game promotion, retries and shared identities."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4
import time
import os

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

import db
os.environ.setdefault("ASOBBY_HOSTCHECK", "off")
import main
import match_identity as identity


@pytest_asyncio.fixture(autouse=True)
async def database(tmp_path, monkeypatch):
    engine = db.init_engine(f"sqlite+aiosqlite:///{tmp_path / 'identity.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(db.Base.metadata.create_all)
    async with db.session() as s:
        s.add_all([db.User(id=uid, name=uid, rank="hard") for uid in ("host", "guest", "other")])
        await s.commit()
    main.RECORDS.clear()
    main.RANKED_SESSIONS.clear()
    monkeypatch.setattr(main, "SESSION_SECRET", "test-match-identity")
    now = [datetime(2026, 9, 19, 4, 30, tzinfo=timezone.utc)]
    monkeypatch.setattr(db, "utcnow", lambda: now[0])
    yield now
    main.RECORDS.clear()
    await db.dispose()


async def pair(now, *, rank="hard", guest_first=False):
    ids = {"host": uuid4().hex, "guest": uuid4().hex}
    matches = []
    for side in (("guest", "host") if guest_first else ("host", "guest")):
        matches.append(await identity.announce(
            user_id=side, client_id=ids[side], side=side, post_id="post",
            host_user_id="host", guest_user_id="guest", host_profile="hp", guest_profile="gp", match_rank=rank))
    assert matches[0] == matches[1]
    return ids, matches[0]


def result(now, *, skew=0, winner="host", duration=120):
    return dict(winner=winner, host_char=0, guest_char=5, host_profile="hp", guest_profile="gp",
                played_at=now[0].timestamp() + skew, duration_sec=duration,
                host_wins=2 if winner == "host" else 1, guest_wins=1 if winner == "host" else 2)


async def submit(ids, mid, side, payload):
    return await identity.submit(user_id=side, client_id=ids[side], match_id=mid, side=side, payload=payload)


async def matches():
    async with db.session() as s:
        return list((await s.scalars(select(db.Match).order_by(db.Match.played_at))).all())


@pytest.mark.asyncio
@pytest.mark.parametrize("guest_first", [False, True])
async def test_clock_skew_never_creates_duplicate_or_rewrites_previous(database, guest_first):
    now = database
    for game in range(5):
        ids, mid = await pair(now, guest_first=guest_first)
        now[0] += timedelta(seconds=120)
        winner = "host" if game in (0, 3) else "guest"
        order = ("guest", "host") if guest_first else ("host", "guest")
        first = await submit(ids, mid, order[0], result(now, skew=-382 if order[0] == "host" else 0, winner=winner))
        assert first["status"] == "pending"
        second = await submit(ids, mid, order[1], result(now, skew=-382 if order[1] == "host" else 0, winner=winner))
        assert second["newly_recorded"] and second["ranked"]
        now[0] += timedelta(seconds=8)
    stored = await matches()
    assert len(stored) == 5
    assert all((b.played_at - a.played_at).total_seconds() == 128 for a, b in zip(stored, stored[1:]))


@pytest.mark.asyncio
async def test_short_real_games_same_winner_are_not_deduplicated(database):
    now = database
    for _ in range(2):
        ids, mid = await pair(now)
        now[0] += timedelta(seconds=8)
        for side in ("host", "guest"):
            await submit(ids, mid, side, result(now, duration=8))
    assert len(await matches()) == 2


@pytest.mark.asyncio
async def test_retry_and_changed_retry_do_not_change_confirmed_match(database):
    ids, mid = await pair(database)
    database[0] += timedelta(seconds=120)
    payload = result(database)
    for side in ("host", "guest"):
        await submit(ids, mid, side, payload)
    before = (await matches())[0]
    for _ in range(3):
        reply = await submit(ids, mid, "host", payload)
        assert reply["duplicate"] and not reply["newly_recorded"]
    conflict = await submit(ids, mid, "host", result(database, winner="guest"))
    assert conflict["status"] == "conflict"
    after = await matches()
    assert len(after) == 1 and after[0].winner == "host" and after[0].played_at == before.played_at


@pytest.mark.asyncio
async def test_disagreement_is_not_a_ranked_match(database):
    ids, mid = await pair(database)
    database[0] += timedelta(seconds=120)
    await submit(ids, mid, "host", result(database))
    reply = await submit(ids, mid, "guest", result(database, winner="guest"))
    assert reply["status"] == "conflict"
    assert await matches() == []
    async with db.session() as s:
        assert not (await s.get(db.User, "host")).rank_locked
        assert len((await s.scalars(select(db.MatchReport))).all()) == 2


@pytest.mark.asyncio
async def test_delayed_sync_preserves_battle_time_and_reuses_report_id(database):
    ids, mid = await pair(database)
    database[0] += timedelta(seconds=120)
    end = database[0]
    payload = result(database, skew=3600)
    database[0] += timedelta(days=1)
    for side in ("host", "guest"):
        await submit(ids, mid, side, payload)
    assert identity.utc((await matches())[0].played_at) == end


@pytest.mark.asyncio
async def test_result_cannot_claim_someone_elses_id(database):
    ids, mid = await pair(database)
    with pytest.raises(ValueError):
        await submit(ids, uuid4().hex, "host", result(database))
    reply = await identity.submit(user_id="other", client_id=ids["host"], match_id=mid, side="host", payload=result(database))
    assert reply["status"] == "pending" and reply["match_id"] is None
    assert not await matches()


@pytest.mark.asyncio
async def test_closed_or_old_start_is_not_joined(database):
    hc, gc = uuid4().hex, uuid4().hex
    args = dict(post_id="post", host_user_id="host", guest_user_id="guest", host_profile="hp", guest_profile="gp")
    hm = await identity.announce(user_id="host", client_id=hc, side="host", **args)
    await identity.announce(user_id="host", client_id=hc, side="host", ended=True, **args)
    gm = await identity.announce(user_id="guest", client_id=gc, side="guest", **args)
    assert hm != gm
    assert await identity.announce(user_id="host", client_id=hc, side="host", **args) == hm
    database[0] += timedelta(seconds=11)
    hm2 = await identity.announce(user_id="host", client_id=uuid4().hex, side="host", **args)
    assert hm2 != gm


@pytest.mark.asyncio
async def test_ph_rating_applied_once_in_same_transaction(database):
    async with db.session() as s:
        for uid in ("host", "guest"):
            (await s.get(db.User, uid)).rank = "ph"
        await s.commit()
    ids, mid = await pair(database, rank="ph")
    database[0] += timedelta(seconds=120)
    payload = result(database)
    await submit(ids, mid, "host", payload)
    assert await db.get_char_rating("host", 0) is None
    await submit(ids, mid, "guest", payload)
    rating = await db.get_char_rating("host", 0)
    assert rating[0] > db.DEFAULT_TS_MU
    for side in ("host", "guest"):
        await submit(ids, mid, side, payload)
    assert await db.get_char_rating("host", 0) == rating


@pytest.mark.asyncio
async def test_legacy_wrong_promotion_and_timestamp_overwrite_are_disabled(database):
    now = database[0]
    old_id = await db.insert_match_result(None, "guest", "", "", "guest", host_profile="hp", guest_profile="gp", source="guest", played_at=now-timedelta(seconds=150))
    found = await db.find_recent_guest_reported_match("guest", winner="guest", host_profile="hp", guest_profile="gp", played_at=now-timedelta(seconds=382))
    assert found is None
    await db.promote_guest_match(old_id, host_user_id="host", host_ip="", winner="guest", host_char=0, guest_char=5,
                                host_profile="hp", guest_profile="gp", ranked=False, played_at=now-timedelta(seconds=382))
    assert identity.utc((await db.get_match_by_id(old_id)).played_at) == now-timedelta(seconds=150)


@pytest.mark.asyncio
async def test_live_clock_guard_cannot_be_bypassed_by_legacy_sync(database):
    payload = result(database, skew=-382)
    held = await main._legacy_clock_guard("host", "host", payload)
    assert held["status"] == "pending"
    token = main.make_session_token({"id": "host", "name": "host"}, 1)
    async with AsyncClient(transport=ASGITransport(app=main.app), base_url="http://test") as client:
        response = await client.post("/matches/sync", headers={"Authorization": f"Bearer {token}"}, json={"matches": [{
            "client_id": uuid4().hex, "played_at": payload["played_at"], "my_side": "host", "winner": "host",
            "my_profile": "hp", "opp_profile": "gp", "my_char": 0, "opp_char": 5,
        }]})
    assert response.status_code == 200, response.text
    assert response.json()["results"][0]["status"] == "pending"
    assert await matches() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("guest_first", [False, True])
async def test_api_start_report_sync_status_and_replay_use_same_id(database, guest_first):
    post = main.Post(id="f"*32, addr="1.2.3.4:10800", post_type="ranked", rank="hard",
                     net_status=main.NET_BATTLE, guest_connected=True, updated_at=time.time(), created_at=time.time())
    main.RECORDS[post.id] = main.PostRecord(post, "owner", "1.2.3.4", owner_user_id="host", guest_user_id="guest",
                                          guest_ip="127.0.0.1", guest_rank="hard", guest_identity_confirmed=True,
                                          host_profile="hp", guest_profile="gp")
    headers = {uid: {"Authorization": "Bearer " + main.make_session_token({"id": uid, "name": uid}, 1)} for uid in ("host", "guest", "other")}
    ids = {uid: uuid4().hex for uid in ("host", "guest")}
    order = ("guest", "host") if guest_first else ("host", "guest")
    async with AsyncClient(transport=ASGITransport(app=main.app), base_url="http://test") as client:
        mids = []
        for side in order:
            r = await client.post("/matches/presence", headers=headers[side], json={
                "client_id": ids[side], "post_id": post.id if side == "host" else "",
                "my_side": "host" if side == "host" else "client", "host_profile": "hp", "guest_profile": "gp",
            })
            assert r.status_code == 200, r.text
            mids.append(r.json()["match_id"])
        assert mids[0] == mids[1] and mids[0]
        mid = mids[0]
        database[0] += timedelta(seconds=120)
        for side in order:
            payload = {**result(database, skew=-382 if side == "host" else 0), "report_version": 2, "client_id": ids[side], "match_id": mid}
            if side == "host":
                payload.update(id=post.id, owner_token="owner")
            r = await client.post("/posts/result" if side == "host" else "/matches/report", headers=headers[side], json=payload)
            assert r.status_code == 200, r.text
        assert r.json()["recorded"] and r.json()["ranked"]
        assert r.json()["ranked_session"]["games"] == 1
        # Same report in a later batch (including duplicate batch entries).
        item = {"client_id": ids["host"], "match_id": mid, "report_version": 2, "duration_sec": 120,
                "played_at": database[0].timestamp() - 382, "my_side": "host", "winner": "host",
                "my_char": 0, "opp_char": 5, "my_profile": "hp", "opp_profile": "gp", "host_wins": 2, "guest_wins": 1}
        r = await client.post("/matches/sync", headers=headers["host"], json={"matches": [item, item]})
        assert r.status_code == 200, r.text
        assert [v["status"] for v in r.json()["results"]] == ["duplicate", "duplicate"]
        assert len(await matches()) == 1
        status = await client.get("/matches/reports", headers=headers["host"], params={"client_id": ids["host"]})
        report = status.json()["reports"][0]
        assert report["status"] == "confirmed" and report["match"]["id"] == mid
        assert report["match"]["played_at"] != item["played_at"]
        private = await client.get("/matches/reports", headers=headers["other"], params={"client_id": ids["host"]})
        assert private.json()["reports"] == []
        denied = await client.post("/replays/upload", headers=headers["other"], params={"match_id": mid}, content=b"not-a-participant")
        assert denied.status_code == 403
        replay = await client.post("/replays/upload", headers=headers["host"], params={"client_id": ids["host"], "battle_ts": item["played_at"]}, content=b"test-replay")
        assert replay.json()["stored"] is True
        assert await db.replay_count_for_match(mid) == 1


@pytest.mark.asyncio
async def test_delayed_start_uses_elapsed_duration_not_wall_clock(database):
    args = dict(post_id="p", host_user_id="host", guest_user_id="guest", host_profile="hp", guest_profile="gp")
    host = await identity.announce(user_id="host", client_id="a"*32, side="host", **args)
    database[0] += timedelta(seconds=20)
    guest = await identity.announce(user_id="guest", client_id="b"*32, side="guest", start_age_sec=20, **args)
    assert guest == host


@pytest.mark.asyncio
async def test_single_asobby_player_can_record_casual(database):
    cid = uuid4().hex
    mid = await identity.announce(user_id="guest", client_id=cid, side="guest", guest_user_id="guest", host_profile="hp", guest_profile="gp")
    database[0] += timedelta(seconds=120)
    reply = await identity.submit(user_id="guest", client_id=cid, match_id=mid, side="guest", payload=result(database))
    assert reply["recorded"] and not reply["ranked"]


@pytest.mark.asyncio
async def test_next_start_does_not_accept_previous_games_late_report(database):
    first_ids, first_mid = await pair(database)
    database[0] += timedelta(seconds=120)
    old_payload = result(database)
    second_ids, second_mid = await pair(database)
    assert first_mid != second_mid
    for side in ("host", "guest"):
        await submit(first_ids, first_mid, side, old_payload)
    database[0] += timedelta(seconds=120)
    for side in ("host", "guest"):
        await submit(second_ids, second_mid, side, result(database, winner="guest"))
    assert [m.winner for m in await matches()] == ["host", "guest"]


@pytest.mark.asyncio
async def test_ranked_limit_with_reversed_delayed_results(database):
    games = []
    for _ in range(6):
        ids, mid = await pair(database)
        database[0] += timedelta(seconds=120)
        games.append((ids, mid, result(database)))
    for ids, mid, payload in reversed(games):
        for side in ("host", "guest"):
            await submit(ids, mid, side, payload)
    assert len(await matches()) == 6
    assert sum(m.ranked for m in await matches()) == 5
