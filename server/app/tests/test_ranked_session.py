"""ランクマセッション (一級オブジェクト) とゲスト自己申告同定のテスト。"""
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

# テスト用環境変数 (import 前に設定)
os.environ.setdefault("ASOBBY_HOSTCHECK", "off")
os.environ.setdefault(
    "DATABASE_URL",
    "sqlite+aiosqlite:////tmp/asobby_ranked_session_test.db",
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


@pytest.fixture(autouse=True)
def clean_state(tmp_path):
    db_path = tmp_path / "asobby_ranked_session_test.db"
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


async def _create_ranked_post(client: AsyncClient, host_token: str) -> tuple[dict, str]:
    res = await client.post(
        "/posts",
        json={"post_type": "ranked", "addr": "1.2.3.4:10800"},
        headers={"Authorization": f"Bearer {host_token}"},
    )
    assert res.status_code == 200
    body = res.json()
    return body["post"], body["owner_token"]


def test_profiles_from_match_status():
    assert main._profiles_from_match_status("Alice(霊夢) vs Bob(魔理沙)") == (
        "Alice",
        "Bob",
    )
    assert main._profiles_from_match_status("Alice") == ("", "")
    assert main._profiles_from_match_status("") == ("", "")


@pytest.mark.asyncio
async def test_presence_profiles_identify_guest_without_ip_match():
    """プロファイルペア一致の presence は IP が違っても決定的に同定する。"""
    async with app_client() as client:
        await create_user("999", name="host", last_ip="1.2.3.4", rank="normal")
        await create_user("888", name="guest", last_ip="5.6.7.8", rank="normal")

        host_token = bearer_token("999", "host")
        post, _ = await _create_ranked_post(client, host_token)
        rec = main.RECORDS[post["id"]]
        # 旧クライアント形式: match_status のみでプロファイルが分かる
        rec.post.net_status = main.NET_BATTLE
        rec.post.match_status = "Alice(霊夢) vs Bob(魔理沙)"

        guest_token = bearer_token("888", "guest")
        pres = await client.post(
            "/matches/presence",
            json={"host_profile": "Alice", "guest_profile": "Bob", "my_side": "client"},
            headers={
                "Authorization": f"Bearer {guest_token}",
                # プローブ由来 IP とは無関係のアドレスから申告 (VPN 等)
                "X-Forwarded-For": "7.7.7.7",
            },
        )
        assert pres.status_code == 200
        data = pres.json()
        assert data["identified"] is True
        assert rec.guest_user_id == "888"
        assert rec.guest_identity_confirmed is True
        assert rec.post.ranked_active is True
        assert data["ranked_session"] is not None
        assert data["ranked_session"]["games"] == 0
        assert data["ranked_session"]["match_rank"] == "normal"
        assert data["ranked_session"]["limit_reached"] is False


@pytest.mark.asyncio
async def test_presence_profiles_band_mismatch_identifies_but_not_ranked():
    """帯不一致でも同定はされるが、ランクマセッションは成立しない。"""
    async with app_client() as client:
        await create_user("999", name="host", last_ip="1.2.3.4", rank="normal")
        await create_user("888", name="guest", last_ip="5.6.7.8", rank="hard")

        host_token = bearer_token("999", "host")
        post, _ = await _create_ranked_post(client, host_token)
        rec = main.RECORDS[post["id"]]
        rec.post.net_status = main.NET_BATTLE
        rec.post.match_status = "Alice(霊夢) vs Bob(魔理沙)"

        guest_token = bearer_token("888", "guest")
        pres = await client.post(
            "/matches/presence",
            json={"host_profile": "Alice", "guest_profile": "Bob"},
            headers={
                "Authorization": f"Bearer {guest_token}",
                "X-Forwarded-For": "7.7.7.7",
            },
        )
        assert pres.status_code == 200
        data = pres.json()
        assert data["identified"] is True
        assert rec.guest_user_id == "888"
        assert rec.post.ranked_active is False
        assert data["ranked_session"] is None


@pytest.mark.asyncio
async def test_confirmed_identity_not_overwritten_by_ip_inference():
    """presence で確定した同定は、同一 IP の別ユーザーで上書きされない。"""
    async with app_client() as client:
        await create_user("999", name="host", last_ip="1.2.3.4", rank="normal")
        await create_user("888", name="guest", last_ip="5.6.7.8", rank="normal")

        host_token = bearer_token("999", "host")
        post, _ = await _create_ranked_post(client, host_token)
        rec = main.RECORDS[post["id"]]
        rec.post.net_status = main.NET_BATTLE
        rec.post.match_status = "Alice(霊夢) vs Bob(魔理沙)"

        guest_token = bearer_token("888", "guest")
        await client.post(
            "/matches/presence",
            json={"host_profile": "Alice", "guest_profile": "Bob"},
            headers={
                "Authorization": f"Bearer {guest_token}",
                "X-Forwarded-For": "5.6.7.8",
            },
        )
        assert rec.guest_user_id == "888"
        assert rec.guest_identity_confirmed is True

        # 同じ IP を last_ip に持つ別ユーザー (共有 NAT) が後から現れても奪わない
        await create_user("777", name="roommate", last_ip="5.6.7.8", rank="normal")
        rec.guest_ip = "5.6.7.8"
        await main._retry_guest_identity_from_ip(rec)
        assert rec.guest_user_id == "888"


@pytest.mark.asyncio
async def test_session_counts_games_and_limits_to_three():
    """結果報告はセッションのゲーム数で 3 戦上限を判定し、レスポンスで進捗を返す。"""
    async with app_client() as client:
        await create_user("999", name="host", last_ip="1.2.3.4", rank="normal")
        await create_user("888", name="guest", last_ip="5.6.7.8", rank="normal")

        host_token = bearer_token("999", "host")
        post, owner_token = await _create_ranked_post(client, host_token)
        rec = main.RECORDS[post["id"]]
        rec.post.net_status = main.NET_BATTLE
        rec.post.match_status = "Alice(霊夢) vs Bob(魔理沙)"

        guest_token = bearer_token("888", "guest")
        pres = await client.post(
            "/matches/presence",
            json={"host_profile": "Alice", "guest_profile": "Bob"},
            headers={
                "Authorization": f"Bearer {guest_token}",
                "X-Forwarded-For": "7.7.7.7",
            },
        )
        assert pres.json()["ranked_session"]["games"] == 0
        assert len(main.RANKED_SESSIONS) == 1

        played_at = time.time()
        for i in range(4):
            r = await client.post(
                "/posts/result",
                json={
                    "id": post["id"],
                    "owner_token": owner_token,
                    "winner": "host",
                    "host_char": 0,
                    "guest_char": 1,
                    "host_profile": "Alice",
                    "guest_profile": "Bob",
                    "played_at": played_at + i * 120,
                },
            )
            assert r.status_code == 200
            data = r.json()
            assert data["recorded"] is True
            if i < 3:
                assert data["ranked"] is True, data
                assert data["ranked_session"]["games"] == i + 1
            else:
                assert data["ranked"] is False, data
                assert "session_limit" in (data["ranked_reason"] or "")

        async with db.session() as s:
            res_m = await s.execute(select(db.Match))
            flags = [m.ranked for m in res_m.scalars().all()]
            assert flags == [True, True, True, False]


@pytest.mark.asyncio
async def test_session_seeded_from_db_streak_after_restart():
    """セッション消失 (再起動相当) 後も DB の連戦数から復元される。"""
    async with app_client() as client:
        await create_user("999", name="host", last_ip="1.2.3.4", rank="normal")
        await create_user("888", name="guest", last_ip="5.6.7.8", rank="normal")

        for _ in range(2):
            await db.insert_match_result(
                host_user_id="999",
                guest_user_id="888",
                host_ip="1.2.3.4",
                guest_ip="5.6.7.8",
                winner="host",
                ranked=True,
                match_rank="normal",
            )

        ses = await main.get_or_create_ranked_session("999", "888", "normal")
        assert ses.games == 2

        # 直近 insert を反映する場合は既存セッションが bump される
        ses2 = await main._record_ranked_game("999", "888", "normal")
        assert ses2 is ses
        assert ses2.games == 3
        assert ses2.limit_reached() is True


@pytest.mark.asyncio
async def test_session_gap_creates_fresh_session():
    """30 分以上空いたセッションは新規作成され、ゲーム数がリセットされる。"""
    async with app_client() as client:
        await create_user("999", name="host", rank="normal")
        await create_user("888", name="guest", rank="normal")

        ses = await main.get_or_create_ranked_session("999", "888", "normal")
        ses.games = 3
        ses.started_at = time.time() - 3600
        ses.last_game_at = time.time() - 3600

        fresh = await main.get_or_create_ranked_session("999", "888", "normal")
        assert fresh is not ses
        assert fresh.games == 0


@pytest.mark.asyncio
async def test_presence_without_body_still_works():
    """旧クライアント (ボディなし presence) も従来どおり動作する。"""
    async with app_client() as client:
        await create_user("999", name="host", last_ip="1.2.3.4", rank="normal")
        await create_user("888", name="guest", last_ip="5.6.7.8", rank="normal")

        host_token = bearer_token("999", "host")
        post, _ = await _create_ranked_post(client, host_token)
        rec = main.RECORDS[post["id"]]
        await main.apply_guest_probe(rec, make_0x08_reply("5.6.7.8"))
        rec.guest_user_id = ""
        rec.guest_rank = ""
        rec.post.guest_user_id = ""
        rec.post.ranked_active = False

        guest_token = bearer_token("888", "guest")
        pres = await client.post(
            "/matches/presence",
            headers={
                "Authorization": f"Bearer {guest_token}",
                "X-Forwarded-For": "5.6.7.8",
            },
        )
        assert pres.status_code == 200
        assert pres.json()["ok"] is True
        assert rec.guest_user_id == "888"
        assert rec.post.ranked_active is True


@pytest.mark.asyncio
async def test_update_response_includes_ranked_session(monkeypatch):
    """ホスト heartbeat のレスポンスにランクマセッション進捗が載る。"""

    async def _no_probe(rec, *, force=False):
        return None

    monkeypatch.setattr(main, "probe_guest_for_record", _no_probe)

    async with app_client() as client:
        await create_user("999", name="host", last_ip="1.2.3.4", rank="normal")
        await create_user("888", name="guest", last_ip="5.6.7.8", rank="normal")

        host_token = bearer_token("999", "host")
        post, owner_token = await _create_ranked_post(client, host_token)
        rec = main.RECORDS[post["id"]]
        rec.guest_ip = "5.6.7.8"
        rec.guest_user_id = "888"
        rec.guest_rank = "normal"
        main.refresh_ranked_active(rec)

        r = await client.post(
            "/posts/update",
            json={
                "id": post["id"],
                "owner_token": owner_token,
                "post_type": "ranked",
                "addr": "1.2.3.4:10800",
                "match_status": "Alice(霊夢) vs Bob(魔理沙)",
                "net_status": main.NET_BATTLE,
                "host_profile": "Alice",
                "guest_profile": "Bob",
            },
            headers={"Authorization": f"Bearer {host_token}"},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["ranked_session"] is not None
        assert data["ranked_session"]["games"] == 0
        assert data["ranked_session"]["max_games"] == 3
        # 明示フィールドが rec に保存される
        assert rec.host_profile == "Alice"
        assert rec.guest_profile == "Bob"
