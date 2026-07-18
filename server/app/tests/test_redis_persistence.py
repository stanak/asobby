"""Redis 永続化 (チャット・対戦中ポスト) のテスト。"""
from __future__ import annotations

import json
import os
import time
from typing import Any

import pytest

os.environ.setdefault("ASOBBY_HOSTCHECK", "off")

import main
import post_redis


class FakeRedis:
    def __init__(self) -> None:
        self.kv: dict[str, Any] = {}
        self.lists: dict[str, list[str]] = {}
        self.sets: dict[str, set[str]] = {}

    def set(self, key: str, value: str, *, ex: int | None = None) -> None:
        self.kv[key] = value

    def get(self, key: str) -> str | None:
        return self.kv.get(key)

    def delete(self, *keys: str) -> None:
        for key in keys:
            self.kv.pop(key, None)
            self.lists.pop(key, None)

    def ttl(self, key: str) -> int:
        return -2 if key not in self.kv else 60

    def sadd(self, key: str, member: str) -> None:
        self.sets.setdefault(key, set()).add(member)

    def srem(self, key: str, member: str) -> None:
        self.sets.get(key, set()).discard(member)

    def smembers(self, key: str) -> list[str]:
        return sorted(self.sets.get(key, set()))

    def mget(self, *keys: str) -> list[str | None]:
        return [self.kv.get(k) for k in keys]

    def rpush(self, key: str, *values: str) -> None:
        self.lists.setdefault(key, []).extend(values)

    def ltrim(self, key: str, start: int, stop: int) -> None:
        items = self.lists.get(key, [])
        n = len(items)
        if n == 0:
            return
        if start < 0:
            start = max(n + start, 0)
        if stop < 0:
            stop = n + stop
        self.lists[key] = items[start : stop + 1]

    def lrange(self, key: str, start: int, stop: int) -> list[str]:
        items = self.lists.get(key, [])
        if stop == -1:
            stop = len(items) - 1
        return items[start : stop + 1]


@pytest.fixture
def fake_redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setenv("UPSTASH_REDIS_REST_URL", "http://fake")
    monkeypatch.setenv("UPSTASH_REDIS_REST_TOKEN", "token")
    monkeypatch.setattr(post_redis, "_client", lambda: fake)
    return fake


def test_post_record_ttl_battle():
    now = main.now_ts()
    post = main.Post(updated_at=now, created_at=now)
    idle = main.PostRecord(post=post, owner_token="t", creator_ip="1.1.1.1")
    battle = main.PostRecord(
        post=post,
        owner_token="t",
        creator_ip="1.1.1.1",
        guest_ip="2.2.2.2",
    )
    assert main.post_record_ttl(idle) == main.POST_TTL_SEC
    assert main.post_record_ttl(battle) == main.POST_BATTLE_TTL_SEC


def test_chat_roundtrip(fake_redis):
    now = time.time()
    msg = {
        "id": "m1",
        "user_id": "u1",
        "name": "Alice",
        "text": "hello",
        "mentions": [],
        "ts": now,
    }
    post_redis.append_chat_message(msg, max_messages=100)
    loaded = post_redis.load_chat_messages(
        "ja", max_messages=100, max_age_sec=3600, now=now
    )
    assert len(loaded) == 1
    assert loaded[0]["id"] == "m1"
    assert loaded[0]["text"] == "hello"


def test_chat_cooldown(fake_redis):
    post_redis.chat_cooldown_mark("u1", 3.0)
    assert post_redis.chat_cooldown_remaining("u1") > 0
    assert post_redis.chat_cooldown_remaining("u2") == 0


def test_chat_max_messages(fake_redis):
    now = time.time()
    for i in range(5):
        post_redis.append_chat_message(
            {"id": f"m{i}", "text": str(i), "ts": now + i},
            max_messages=3,
        )
    loaded = post_redis.load_chat_messages(
        "ja", max_messages=3, max_age_sec=3600, now=now + 10
    )
    assert len(loaded) == 3
    assert loaded[0]["id"] == "m2"
    assert loaded[-1]["id"] == "m4"


@pytest.mark.asyncio
async def test_hydrate_battle_post_survives_stale_heartbeat(monkeypatch):
    main.RECORDS.clear()
    now = main.now_ts()
    post = main.Post(
        id="battle1",
        addr="1.2.3.4:10800",
        updated_at=now - 120,
        created_at=now - 600,
        guest_connected=True,
    )
    data = main.post_record_to_dict(
        main.PostRecord(
            post=post,
            owner_token="tok",
            creator_ip="1.2.3.4",
            guest_ip="5.6.7.8",
            guest_user_id="guest1",
        )
    )

    monkeypatch.setattr(post_redis, "is_configured", lambda: True)

    async def fake_load():
        return [data]

    async def fake_delete(_post_id: str) -> None:
        pass

    monkeypatch.setattr(main, "_delete_persisted_post", fake_delete)
    monkeypatch.setattr(
        main,
        "_hydrate_records_from_redis",
        main._hydrate_records_from_redis,
    )
    monkeypatch.setattr(
        post_redis,
        "load_all_record_dicts",
        lambda: [data],
    )

    await main._hydrate_records_from_redis()
    assert "battle1" in main.RECORDS
    assert main.RECORDS["battle1"].guest_ip == "5.6.7.8"
    main.RECORDS.clear()


@pytest.mark.asyncio
async def test_hydrate_idle_post_dropped_when_stale(monkeypatch):
    main.RECORDS.clear()
    now = main.now_ts()
    post = main.Post(id="idle1", updated_at=now - 120, created_at=now - 600)
    data = main.post_record_to_dict(
        main.PostRecord(post=post, owner_token="tok", creator_ip="1.2.3.4")
    )

    monkeypatch.setattr(post_redis, "is_configured", lambda: True)
    deleted: list[str] = []

    async def fake_delete(post_id: str) -> None:
        deleted.append(post_id)

    monkeypatch.setattr(main, "_delete_persisted_post", fake_delete)
    monkeypatch.setattr(post_redis, "load_all_record_dicts", lambda: [data])

    await main._hydrate_records_from_redis()
    assert "idle1" not in main.RECORDS
    assert deleted == ["idle1"]
    main.RECORDS.clear()


@pytest.mark.asyncio
async def test_hydrate_chat_from_redis(monkeypatch):
    _clear = lambda: [main.LOBBY_CHATS[lang].clear() for lang in main.LOBBY_CHAT_LANGS]
    _clear()
    now = time.time()
    messages = {
        "ja": [{"id": "a", "text": "one", "lang": "ja", "ts": now}],
        "en": [{"id": "b", "text": "two", "lang": "en", "ts": now + 1}],
    }

    monkeypatch.setattr(post_redis, "is_configured", lambda: True)
    monkeypatch.setattr(
        post_redis,
        "load_all_chat_messages",
        lambda **kwargs: {k: list(v) for k, v in messages.items()},
    )

    await main._hydrate_chat_from_redis()
    snap = main.lobby_chat_snapshot()
    assert len(snap["ja"]) == 1
    assert snap["ja"][0]["id"] == "a"
    assert len(snap["en"]) == 1
    assert snap["en"][0]["id"] == "b"
    _clear()
