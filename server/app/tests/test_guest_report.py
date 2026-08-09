"""ゲスト側対戦結果報告の結合テスト。"""
from __future__ import annotations

import os
import socket
import struct
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

os.environ.setdefault("ASOBBY_HOSTCHECK", "off")
os.environ.setdefault(
    "DATABASE_URL",
    "sqlite+aiosqlite:////tmp/asobby_guest_report_test.db",
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


GUEST_REPORT_BODY = {
    "winner": "guest",
    "host_char": 0,
    "guest_char": 1,
    "host_profile": "hostp",
    "guest_profile": "guestp",
}


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


@pytest.fixture(autouse=True)
def clean_state(tmp_path):
    db_path = tmp_path / "asobby_guest_report_test.db"
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
async def test_guest_report_recorded_and_stats():
    async with app_client() as client:
        await create_user("888", name="guest", last_ip="5.6.7.8")
        guest_token = bearer_token("888", "guest")

        res = await client.post(
            "/matches/report",
            json=GUEST_REPORT_BODY,
            headers={"Authorization": f"Bearer {guest_token}"},
        )
        assert res.status_code == 200
        body = res.json()
        assert body["ok"] is True
        assert body["recorded"] is True
        assert body.get("match_id")

        async with db.session() as s:
            res_m = await s.execute(select(db.Match))
            match = list(res_m.scalars().all())[0]
            assert match.source == "guest"
            assert match.ranked is False
            assert match.host_user_id is None
            assert match.guest_user_id == "888"
            assert match.winner == "guest"

        stats = await client.get(
            "/stats/me",
            headers={"Authorization": f"Bearer {guest_token}"},
        )
        assert stats.status_code == 200
        data = stats.json()
        assert data["total"]["games"] == 1
        assert data["total"]["wins"] == 1


@pytest.mark.asyncio
async def test_guest_report_duplicate():
    async with app_client() as client:
        await create_user("888", name="guest")
        guest_token = bearer_token("888", "guest")

        first = await client.post(
            "/matches/report",
            json=GUEST_REPORT_BODY,
            headers={"Authorization": f"Bearer {guest_token}"},
        )
        assert first.json()["recorded"] is True

        second = await client.post(
            "/matches/report",
            json=GUEST_REPORT_BODY,
            headers={"Authorization": f"Bearer {guest_token}"},
        )
        assert second.status_code == 200
        assert second.json()["recorded"] is False
        assert second.json()["reason"] == "duplicate"
        assert second.json().get("match_id")

        async with db.session() as s:
            res_m = await s.execute(select(db.Match))
            assert len(list(res_m.scalars().all())) == 1


@pytest.mark.asyncio
async def test_host_first_guest_duplicate():
    async with app_client() as client:
        await create_user("999", name="host", last_ip="1.2.3.4")
        await create_user("888", name="guest", last_ip="5.6.7.8")

        host_token = bearer_token("999", "host")
        res = await client.post(
            "/posts",
            json={"post_type": "casual", "addr": "1.2.3.4:10800"},
            headers={"Authorization": f"Bearer {host_token}"},
        )
        post = res.json()["post"]
        owner_token = res.json()["owner_token"]
        rec = main.RECORDS[post["id"]]
        await main.apply_guest_probe(rec, make_0x08_reply("5.6.7.8"))

        host_result = await client.post(
            "/posts/result",
            json={
                "id": post["id"],
                "owner_token": owner_token,
                "winner": "host",
                "host_char": 0,
                "guest_char": 1,
                "host_profile": "hostp",
                "guest_profile": "guestp",
            },
        )
        assert host_result.json()["recorded"] is True

        guest_token = bearer_token("888", "guest")
        guest_res = await client.post(
            "/matches/report",
            json=GUEST_REPORT_BODY,
            headers={"Authorization": f"Bearer {guest_token}"},
        )
        assert guest_res.json()["recorded"] is False
        assert guest_res.json()["reason"] == "duplicate"

        async with db.session() as s:
            res_m = await s.execute(select(db.Match))
            matches = list(res_m.scalars().all())
            assert len(matches) == 1
            assert matches[0].source == "host"


@pytest.mark.asyncio
async def test_guest_first_host_promotes():
    async with app_client() as client:
        await create_user("999", name="host", last_ip="1.2.3.4")
        await create_user("888", name="guest", last_ip="5.6.7.8")

        guest_token = bearer_token("888", "guest")
        guest_res = await client.post(
            "/matches/report",
            json=GUEST_REPORT_BODY,
            headers={"Authorization": f"Bearer {guest_token}"},
        )
        assert guest_res.json()["recorded"] is True

        # ゲスト報告時の touch_user で last_ip がテストクライアント IP に上書きされるため復元
        await create_user("888", name="guest", last_ip="5.6.7.8")

        async with db.session() as s:
            res_m = await s.execute(select(db.Match))
            promoted_id = list(res_m.scalars().all())[0].id

        host_token = bearer_token("999", "host")
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

        host_result = await client.post(
            "/posts/result",
            json={
                "id": post["id"],
                "owner_token": owner_token,
                "winner": "host",
                "host_char": 2,
                "guest_char": 3,
                "host_profile": "hostp",
                "guest_profile": "guestp",
            },
        )
        assert host_result.status_code == 200
        data = host_result.json()
        assert data["recorded"] is True
        assert data["ranked"] is True

        async with db.session() as s:
            res_m = await s.execute(select(db.Match))
            matches = list(res_m.scalars().all())
            assert len(matches) == 1
            m = matches[0]
            assert m.id == promoted_id
            assert m.source == "host"
            assert m.ranked is True
            assert m.match_rank == "normal"
            assert m.host_user_id == "999"
            assert m.guest_user_id == "888"


@pytest.mark.asyncio
async def test_sync_consecutive_rematches_same_profiles_not_deduped():
    """同一相手・同一勝者でも 120s 後の 2 戦目は別行として記録される。"""
    async with app_client() as client:
        await create_user("999", name="host", rank="normal")
        host_token = bearer_token("999", "host")
        base = time.time() - 120

        sync_body = {
            "my_side": "host",
            "winner": "host",
            "my_char": RANKED_MATCH_PROFILES["host_char"],
            "opp_char": RANKED_MATCH_PROFILES["guest_char"],
            "my_profile": RANKED_MATCH_PROFILES["host_profile"],
            "opp_profile": RANKED_MATCH_PROFILES["guest_profile"],
            "ranked": False,
        }
        first = await client.post(
            "/matches/sync",
            json={
                "matches": [{
                    **sync_body,
                    "client_id": "1" * 32,
                    "played_at": base,
                }],
            },
            headers={"Authorization": f"Bearer {host_token}"},
        )
        second = await client.post(
            "/matches/sync",
            json={
                "matches": [{
                    **sync_body,
                    "client_id": "2" * 32,
                    "played_at": base + 120,
                }],
            },
            headers={"Authorization": f"Bearer {host_token}"},
        )
        assert first.json()["results"][0]["status"] == "imported"
        assert second.json()["results"][0]["status"] == "imported"

        async with db.session() as s:
            res_m = await s.execute(select(db.Match))
            assert len(list(res_m.scalars().all())) == 2


@pytest.mark.asyncio
async def test_sync_different_opponents_within_120s_not_deduped():
    """120s 以内でも相手が違えば両方 import される (時刻のみ dedup 廃止の回帰防止)。"""
    async with app_client() as client:
        await create_user("999", name="host", rank="normal")
        host_token = bearer_token("999", "host")
        base = time.time() - 90

        first = await client.post(
            "/matches/sync",
            json={
                "matches": [{
                    "client_id": "3" * 32,
                    "played_at": base,
                    "my_side": "host",
                    "winner": "host",
                    "my_char": 0,
                    "opp_char": 1,
                    "my_profile": "hostp",
                    "opp_profile": "guest_a",
                    "ranked": False,
                }],
            },
            headers={"Authorization": f"Bearer {host_token}"},
        )
        second = await client.post(
            "/matches/sync",
            json={
                "matches": [{
                    "client_id": "4" * 32,
                    "played_at": time.time(),
                    "my_side": "host",
                    "winner": "guest",
                    "my_char": 0,
                    "opp_char": 2,
                    "my_profile": "hostp",
                    "opp_profile": "guest_b",
                    "ranked": False,
                }],
            },
            headers={"Authorization": f"Bearer {host_token}"},
        )
        assert first.json()["results"][0]["status"] == "imported"
        assert second.json()["results"][0]["status"] == "imported"

        async with db.session() as s:
            res_m = await s.execute(select(db.Match))
            assert len(list(res_m.scalars().all())) == 2


@pytest.mark.asyncio
async def test_guest_report_unauthorized():
    async with app_client() as client:
        res = await client.post("/matches/report", json=GUEST_REPORT_BODY)
        assert res.status_code == 401


RANKED_MATCH_PROFILES = {
    "winner": "host",
    "host_char": 0,
    "guest_char": 1,
    "host_profile": "rank_hp",
    "guest_profile": "rank_gp",
}


@pytest.mark.asyncio
async def test_host_result_without_guest_ip_promotes_ranked():
    """ゲスト報告先行・プローブなしでも host /posts/result がランクマ昇格する。"""
    async with app_client() as client:
        await create_user("999", name="host", last_ip="1.2.3.4", rank="normal")
        await create_user("888", name="guest", last_ip="5.6.7.8", rank="normal")

        played_at = time.time()
        guest_token = bearer_token("888", "guest")
        guest_res = await client.post(
            "/matches/report",
            json={**RANKED_MATCH_PROFILES, "played_at": played_at},
            headers={"Authorization": f"Bearer {guest_token}"},
        )
        assert guest_res.json()["recorded"] is True

        await create_user("888", name="guest", last_ip="5.6.7.8", rank="normal")

        async with db.session() as s:
            res_m = await s.execute(select(db.Match))
            promoted_id = list(res_m.scalars().all())[0].id

        host_token = bearer_token("999", "host")
        res = await client.post(
            "/posts",
            json={"post_type": "ranked", "addr": "1.2.3.4:10800"},
            headers={"Authorization": f"Bearer {host_token}"},
        )
        post = res.json()["post"]
        owner_token = res.json()["owner_token"]
        rec = main.RECORDS[post["id"]]
        assert rec.guest_ip == ""

        host_result = await client.post(
            "/posts/result",
            json={
                "id": post["id"],
                "owner_token": owner_token,
                "played_at": played_at,
                **RANKED_MATCH_PROFILES,
            },
        )
        assert host_result.status_code == 200
        data = host_result.json()
        assert data["recorded"] is True
        assert data["ranked"] is True

        async with db.session() as s:
            res_m = await s.execute(select(db.Match))
            matches = list(res_m.scalars().all())
            assert len(matches) == 1
            m = matches[0]
            assert m.id == promoted_id
            assert m.source == "host"
            assert m.ranked is True
            assert m.match_rank == "normal"
            assert m.host_user_id == "999"
            assert m.guest_user_id == "888"


@pytest.mark.asyncio
async def test_guest_and_sync_same_played_at_dedup():
    """guest 報告と sync が同一 played_at・プロファイルなら 1 行に dedup される。"""
    async with app_client() as client:
        await create_user("888", name="guest", rank="normal")

        played_at = time.time()
        guest_token = bearer_token("888", "guest")
        guest_body = {**RANKED_MATCH_PROFILES, "played_at": played_at}
        guest_res = await client.post(
            "/matches/report",
            json=guest_body,
            headers={"Authorization": f"Bearer {guest_token}"},
        )
        assert guest_res.json()["recorded"] is True

        async with db.session() as s:
            res_m = await s.execute(select(db.Match))
            promoted_id = list(res_m.scalars().all())[0].id

        sync_res = await client.post(
            "/matches/sync",
            json={
                "matches": [{
                    "client_id": "b" * 32,
                    "played_at": played_at,
                    "my_side": "client",
                    "winner": RANKED_MATCH_PROFILES["winner"],
                    "my_char": RANKED_MATCH_PROFILES["guest_char"],
                    "opp_char": RANKED_MATCH_PROFILES["host_char"],
                    "my_profile": RANKED_MATCH_PROFILES["guest_profile"],
                    "opp_profile": RANKED_MATCH_PROFILES["host_profile"],
                    "ranked": False,
                }],
            },
            headers={"Authorization": f"Bearer {guest_token}"},
        )
        assert sync_res.status_code == 200
        assert sync_res.json()["results"][0]["status"] == "duplicate"

        async with db.session() as s:
            res_m = await s.execute(select(db.Match))
            matches = list(res_m.scalars().all())
            assert len(matches) == 1
            assert matches[0].id == promoted_id


@pytest.mark.asyncio
async def test_host_sync_mergeable_dedup_played_at_skew():
    """guest 報告と host sync の played_at が 3 分ズレても 1 行にまとまる。"""
    async with app_client() as client:
        await create_user("999", name="host", rank="normal")
        await create_user("888", name="guest", rank="normal")

        guest_played_at = time.time() - 180
        guest_token = bearer_token("888", "guest")
        guest_res = await client.post(
            "/matches/report",
            json={**RANKED_MATCH_PROFILES, "played_at": guest_played_at},
            headers={"Authorization": f"Bearer {guest_token}"},
        )
        assert guest_res.json()["recorded"] is True

        async with db.session() as s:
            res_m = await s.execute(select(db.Match))
            promoted_id = list(res_m.scalars().all())[0].id

        host_token = bearer_token("999", "host")
        host_played_at = time.time()
        sync_res = await client.post(
            "/matches/sync",
            json={
                "matches": [{
                    "client_id": "c" * 32,
                    "played_at": host_played_at,
                    "my_side": "host",
                    "winner": "host",
                    "my_char": RANKED_MATCH_PROFILES["host_char"],
                    "opp_char": RANKED_MATCH_PROFILES["guest_char"],
                    "my_profile": RANKED_MATCH_PROFILES["host_profile"],
                    "opp_profile": RANKED_MATCH_PROFILES["guest_profile"],
                    "ranked": True,
                    "match_rank": "normal",
                }],
            },
            headers={"Authorization": f"Bearer {host_token}"},
        )
        assert sync_res.status_code == 200
        assert sync_res.json()["results"][0]["status"] == "duplicate"

        async with db.session() as s:
            res_m = await s.execute(select(db.Match))
            matches = list(res_m.scalars().all())
            assert len(matches) == 1
            m = matches[0]
            assert m.id == promoted_id
            assert m.source == "host"
            assert m.ranked is True
            assert m.host_user_id == "999"
            assert m.guest_user_id == "888"


@pytest.mark.asyncio
async def test_consecutive_ranked_matches_same_players_not_deduped():
    """同一対戦相手・同一勝者の連続ランクマ (120s 以内) は別試合として記録される。"""
    async with app_client() as client:
        await create_user("999", name="host", rank="normal")
        await create_user("888", name="guest", rank="normal")

        host_token = bearer_token("999", "host")
        guest_token = bearer_token("888", "guest")
        first_at = time.time() - 120
        second_at = time.time() - 30  # 90s 後 (旧 180s 窓では誤 dedup)

        guest_res = await client.post(
            "/matches/report",
            json={**RANKED_MATCH_PROFILES, "played_at": first_at},
            headers={"Authorization": f"Bearer {guest_token}"},
        )
        assert guest_res.json()["recorded"] is True

        sync_body = lambda client_id, played_at: {
            "matches": [{
                "client_id": client_id,
                "played_at": played_at,
                "my_side": "host",
                "winner": RANKED_MATCH_PROFILES["winner"],
                "my_char": RANKED_MATCH_PROFILES["host_char"],
                "opp_char": RANKED_MATCH_PROFILES["guest_char"],
                "my_profile": RANKED_MATCH_PROFILES["host_profile"],
                "opp_profile": RANKED_MATCH_PROFILES["guest_profile"],
                "ranked": True,
                "match_rank": "normal",
            }],
        }

        res1 = await client.post(
            "/matches/sync",
            json=sync_body("a" * 32, first_at),
            headers={"Authorization": f"Bearer {host_token}"},
        )
        assert res1.status_code == 200
        assert res1.json()["results"][0]["status"] == "duplicate"

        res2 = await client.post(
            "/matches/sync",
            json=sync_body("b" * 32, second_at),
            headers={"Authorization": f"Bearer {host_token}"},
        )
        assert res2.status_code == 200
        assert res2.json()["results"][0]["status"] == "imported"

        async with db.session() as s:
            res_m = await s.execute(select(db.Match))
            matches = list(res_m.scalars().all())
            assert len(matches) == 2


@pytest.mark.asyncio
async def test_sync_promotes_guest_report_ranked():
    """host sync (ranked=true) が先行ゲスト報告を昇格する。"""
    async with app_client() as client:
        await create_user("999", name="host", rank="normal")
        await create_user("888", name="guest", rank="normal")

        played_at = time.time()
        guest_token = bearer_token("888", "guest")
        guest_res = await client.post(
            "/matches/report",
            json={**RANKED_MATCH_PROFILES, "played_at": played_at},
            headers={"Authorization": f"Bearer {guest_token}"},
        )
        assert guest_res.json()["recorded"] is True

        async with db.session() as s:
            res_m = await s.execute(select(db.Match))
            promoted_id = list(res_m.scalars().all())[0].id

        host_token = bearer_token("999", "host")
        client_id = "a" * 32
        sync_res = await client.post(
            "/matches/sync",
            json={
                "matches": [{
                    "client_id": client_id,
                    "played_at": played_at,
                    "my_side": "host",
                    "winner": "host",
                    "my_char": RANKED_MATCH_PROFILES["host_char"],
                    "opp_char": RANKED_MATCH_PROFILES["guest_char"],
                    "my_profile": RANKED_MATCH_PROFILES["host_profile"],
                    "opp_profile": RANKED_MATCH_PROFILES["guest_profile"],
                    "ranked": True,
                    "match_rank": "normal",
                }],
            },
            headers={"Authorization": f"Bearer {host_token}"},
        )
        assert sync_res.status_code == 200
        assert sync_res.json()["results"][0]["status"] == "duplicate"

        async with db.session() as s:
            res_m = await s.execute(select(db.Match))
            matches = list(res_m.scalars().all())
            assert len(matches) == 1
            m = matches[0]
            assert m.id == promoted_id
            assert m.source == "host"
            assert m.ranked is True
            assert m.match_rank == "normal"
            assert m.host_user_id == "999"
            assert m.guest_user_id == "888"


@pytest.mark.asyncio
async def test_host_result_repeat_report_is_idempotent():
    """同一対戦の /posts/result 再送で session_games が二重加算されない。"""
    async with app_client() as client:
        await create_user("999", name="host", last_ip="1.2.3.4", rank="normal")
        await create_user("888", name="guest", last_ip="5.6.7.8", rank="normal")

        host_token = bearer_token("999", "host")
        res = await client.post(
            "/posts",
            json={"post_type": "ranked", "addr": "1.2.3.4:10800"},
            headers={"Authorization": f"Bearer {host_token}"},
        )
        post = res.json()["post"]
        owner_token = res.json()["owner_token"]
        rec = main.RECORDS[post["id"]]
        await main.apply_guest_probe(rec, make_0x08_reply("5.6.7.8"))

        body = {
            "id": post["id"],
            "owner_token": owner_token,
            "winner": "host",
            "host_char": 0,
            "guest_char": 1,
            "host_profile": "hp",
            "guest_profile": "gp",
        }
        first = await client.post("/posts/result", json=body)
        assert first.json()["recorded"] is True
        assert first.json().get("duplicate") is not True
        games_after_first = rec.session_games

        second = await client.post("/posts/result", json=body)
        assert second.json()["recorded"] is True
        assert second.json().get("duplicate") is True
        assert rec.session_games == games_after_first

        async with db.session() as s:
            res_m = await s.execute(select(db.Match))
            assert len(list(res_m.scalars().all())) == 1


@pytest.mark.asyncio
async def test_guest_reports_two_hosts_not_blocked():
    """30 秒以内でも別ホストとのゲスト報告は両方記録される。"""
    async with app_client() as client:
        await create_user("888", name="guest", rank="normal")
        guest_token = bearer_token("888", "guest")

        first = await client.post(
            "/matches/report",
            json={
                "winner": "guest",
                "host_char": 0,
                "guest_char": 1,
                "host_profile": "host_a",
                "guest_profile": "guestp",
            },
            headers={"Authorization": f"Bearer {guest_token}"},
        )
        second = await client.post(
            "/matches/report",
            json={
                "winner": "host",
                "host_char": 2,
                "guest_char": 3,
                "host_profile": "host_b",
                "guest_profile": "guestp",
            },
            headers={"Authorization": f"Bearer {guest_token}"},
        )
        assert first.json()["recorded"] is True
        assert second.json()["recorded"] is True

        async with db.session() as s:
            res_m = await s.execute(select(db.Match))
            assert len(list(res_m.scalars().all())) == 2


@pytest.mark.asyncio
async def test_concurrent_host_result_and_guest_report_single_row():
    """ホスト報告とゲスト報告が同時に届いても 1 行に dedup される (書き込み直列化)。"""
    import asyncio
    import time

    async with app_client() as client:
        await create_user("999", name="host", last_ip="1.2.3.4")
        await create_user("888", name="guest", last_ip="5.6.7.8")

        host_token = bearer_token("999", "host")
        res = await client.post(
            "/posts",
            json={"post_type": "ranked", "addr": "1.2.3.4:10800"},
            headers={"Authorization": f"Bearer {host_token}"},
        )
        post = res.json()["post"]
        owner_token = res.json()["owner_token"]
        rec = main.RECORDS[post["id"]]
        await main.apply_guest_probe(rec, make_0x08_reply("5.6.7.8"))
        assert rec.guest_user_id == "888"

        guest_token = bearer_token("888", "guest")
        now = time.time()
        # 本番で観測された挙動: KO 検知時刻は 7 秒ズレるが HTTP はほぼ同時に届く
        host_res, guest_res = await asyncio.gather(
            client.post(
                "/posts/result",
                json={
                    "id": post["id"],
                    "owner_token": owner_token,
                    "winner": "host",
                    "host_char": 2,
                    "guest_char": 3,
                    "host_profile": "hostp",
                    "guest_profile": "guestp",
                    "played_at": now,
                },
            ),
            client.post(
                "/matches/report",
                json={
                    "winner": "host",
                    "host_char": 2,
                    "guest_char": 3,
                    "host_profile": "hostp",
                    "guest_profile": "guestp",
                    "played_at": now - 7,
                },
                headers={"Authorization": f"Bearer {guest_token}"},
            ),
        )
        assert host_res.status_code == 200
        assert guest_res.status_code == 200

        async with db.session() as s:
            res_m = await s.execute(select(db.Match))
            matches = list(res_m.scalars().all())
            assert len(matches) == 1
            assert matches[0].host_user_id == "999"
            assert matches[0].guest_user_id == "888"
