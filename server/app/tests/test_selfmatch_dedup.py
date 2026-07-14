"""自分対自分バグ関連: ゲスト未同定時の重複排除と再同定のテスト。"""
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
    "sqlite+aiosqlite:////tmp/asobby_selfmatch_test.db",
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


async def create_user(user_id: str, *, name: str = "user", last_ip: str = "") -> None:
    async with db.session() as s:
        user = await s.get(db.User, user_id)
        if user is None:
            user = db.User(id=user_id, name=name, last_ip=last_ip)
            s.add(user)
        else:
            user.name = name
            user.last_ip = last_ip
        await s.commit()


async def create_post(
    client: AsyncClient, host_id: str, host_ip: str
) -> tuple[dict, str]:
    token = bearer_token(host_id, "host")
    res = await client.post(
        "/posts",
        json={"post_type": "casual", "addr": f"{host_ip}:10800"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    body = res.json()
    return body["post"], body["owner_token"]


async def report_host_result(
    client: AsyncClient, post_id: str, owner_token: str
) -> None:
    r = await client.post(
        "/posts/result",
        json={
            "id": post_id,
            "owner_token": owner_token,
            "winner": "host",
            "host_char": 0,
            "guest_char": 5,
            "host_profile": "hostprof",
            "guest_profile": "guestprof",
        },
    )
    assert r.status_code == 200


async def all_matches() -> list[db.Match]:
    async with db.session() as s:
        res = await s.execute(select(db.Match))
        return list(res.scalars().all())


@pytest.fixture(autouse=True)
def clean_state(tmp_path):
    db_path = tmp_path / "asobby_selfmatch_test.db"
    url = f"sqlite+aiosqlite:///{db_path}"
    os.environ["DATABASE_URL"] = url
    main.DATABASE_URL = url
    main.RECORDS.clear()
    main.LAST_CREATE_AT.clear()
    main.LOGOUT_REVOKED.clear()
    yield
    main.RECORDS.clear()


@pytest.mark.asyncio
async def test_probe_does_not_identify_guest_as_host_self():
    """ゲスト IP がホスト自身の last_ip と一致しても自分対自分にしない。"""
    async with app_client() as client:
        await create_user("100", name="host", last_ip="1.2.3.4")
        post, _ = await create_post(client, "100", "1.2.3.4")
        rec = main.RECORDS[post["id"]]

        await main.apply_guest_probe(rec, make_0x08_reply("1.2.3.4"))
        assert rec.guest_ip == "1.2.3.4"
        assert rec.guest_user_id == ""
        assert rec.post.guest_name == ""


@pytest.mark.asyncio
async def test_result_reidentifies_guest_by_ip():
    """接続時に同定できなかったゲストを result 報告時に再同定する。"""
    async with app_client() as client:
        await create_user("100", name="host", last_ip="1.2.3.4")
        post, owner_token = await create_post(client, "100", "1.2.3.4")
        rec = main.RECORDS[post["id"]]

        # プローブ時点ではゲストの last_ip が未登録 → 同定失敗
        await main.apply_guest_probe(rec, make_0x08_reply("5.6.7.8"))
        assert rec.guest_user_id == ""

        # その後ログインして last_ip が付いた
        await create_user("200", name="guest", last_ip="5.6.7.8")

        await report_host_result(client, post["id"], owner_token)
        matches = await all_matches()
        assert len(matches) == 1
        assert matches[0].host_user_id == "100"
        assert matches[0].guest_user_id == "200"


@pytest.mark.asyncio
async def test_sync_dedups_against_unlinked_host_report_by_profiles():
    """ゲスト未同定の host 報告に対し、sync がプロファイル照合で重複排除される。"""
    async with app_client() as client:
        await create_user("100", name="host", last_ip="1.2.3.4")
        post, owner_token = await create_post(client, "100", "1.2.3.4")
        rec = main.RECORDS[post["id"]]
        await main.apply_guest_probe(rec, make_0x08_reply("5.6.7.8"))
        assert rec.guest_user_id == ""
        await report_host_result(client, post["id"], owner_token)

        matches = await all_matches()
        assert len(matches) == 1
        host_match_id = matches[0].id
        assert matches[0].guest_user_id is None

        # ゲストが後からローカル戦績を sync (net_side 反転バグ時の
        # my_side="host" でもプロファイル順は同じになるため照合できる)
        await create_user("200", name="guest", last_ip="")
        guest_token = bearer_token("200", "guest")
        res = await client.post(
            "/matches/sync",
            json={
                "matches": [{
                    "client_id": "e" * 32,
                    "played_at": time.time(),
                    "my_side": "guest",
                    "winner": "host",
                    "my_char": 5,
                    "opp_char": 0,
                    "my_profile": "guestprof",
                    "opp_profile": "hostprof",
                }],
            },
            headers={"Authorization": f"Bearer {guest_token}"},
        )
        r0 = res.json()["results"][0]
        assert r0["status"] == "duplicate"
        assert r0["server_id"] == host_match_id

        # 重複判定と同時に guest_user_id が補完される
        matches = await all_matches()
        assert len(matches) == 1
        assert matches[0].guest_user_id == "200"


@pytest.mark.asyncio
async def test_guest_report_dedups_against_unlinked_host_report():
    """ゲスト未同定の host 報告があるとき /matches/report は重複扱いになる。"""
    async with app_client() as client:
        await create_user("100", name="host", last_ip="1.2.3.4")
        post, owner_token = await create_post(client, "100", "1.2.3.4")
        rec = main.RECORDS[post["id"]]
        await main.apply_guest_probe(rec, make_0x08_reply("5.6.7.8"))
        await report_host_result(client, post["id"], owner_token)

        await create_user("200", name="guest", last_ip="")
        guest_token = bearer_token("200", "guest")
        res = await client.post(
            "/matches/report",
            json={
                "winner": "host",
                "host_char": 0,
                "guest_char": 5,
                "host_profile": "hostprof",
                "guest_profile": "guestprof",
            },
            headers={"Authorization": f"Bearer {guest_token}"},
        )
        body = res.json()
        assert body["recorded"] is False
        assert body["reason"] == "duplicate"

        matches = await all_matches()
        assert len(matches) == 1
        assert matches[0].guest_user_id == "200"


@pytest.mark.asyncio
async def test_host_result_promotes_guest_report_by_profiles():
    """ゲスト報告が先行し、ホスト側でゲスト未同定でも昇格して二重登録しない。"""
    async with app_client() as client:
        await create_user("100", name="host", last_ip="1.2.3.4")
        post, owner_token = await create_post(client, "100", "1.2.3.4")
        rec = main.RECORDS[post["id"]]
        await main.apply_guest_probe(rec, make_0x08_reply("5.6.7.8"))
        assert rec.guest_user_id == ""

        # ゲスト報告が先に届く
        await create_user("200", name="guest", last_ip="")
        guest_token = bearer_token("200", "guest")
        res = await client.post(
            "/matches/report",
            json={
                "winner": "host",
                "host_char": 0,
                "guest_char": 5,
                "host_profile": "hostprof",
                "guest_profile": "guestprof",
            },
            headers={"Authorization": f"Bearer {guest_token}"},
        )
        assert res.json()["recorded"] is True

        await report_host_result(client, post["id"], owner_token)
        matches = await all_matches()
        assert len(matches) == 1
        assert matches[0].source == "host"
        assert matches[0].host_user_id == "100"
        assert matches[0].guest_user_id == "200"
