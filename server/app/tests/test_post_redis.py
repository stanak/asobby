"""PostRecord の Redis 永続化 (シリアライズ) の単体テスト。"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("ASOBBY_HOSTCHECK", "off")

import main
import post_redis


def test_post_record_roundtrip():
    now = main.now_ts()
    post = main.Post(
        rank="normal",
        post_type="casual",
        rating=1500.0,
        addr="1.2.3.4:10800",
        comment="hello",
        stream_url="",
        giuroll=False,
        autopunch=False,
        match_status="",
        net_status=0,
        owner_name="host",
        owner_avatar="",
        updated_at=now,
        created_at=now,
    )
    rec = main.PostRecord(
        post=post,
        owner_token="tok123",
        creator_ip="10.0.0.1",
        owner_user_id="u1",
        guest_ip="10.0.0.2",
        guest_user_id="u2",
        guest_rank="normal",
        session_games=2,
        pending_messages=[{"id": "m1", "type": "giuroll_request"}],
        sent_log={"m1": {"type": "giuroll_request", "replied": False}},
    )
    data = main.post_record_to_dict(rec)
    restored = main.post_record_from_dict(data)
    assert restored.owner_token == rec.owner_token
    assert restored.guest_ip == rec.guest_ip
    assert restored.session_games == rec.session_games
    assert restored.pending_messages == rec.pending_messages
    assert restored.sent_log == rec.sent_log
    assert restored.post.addr == rec.post.addr
    assert restored.post.owner_name == rec.post.owner_name


def test_post_redis_not_configured_is_noop():
    for key in ("UPSTASH_REDIS_REST_URL", "UPSTASH_REDIS_REST_TOKEN"):
        os.environ.pop(key, None)
    assert post_redis.is_configured() is False
    post_redis.save_record_dict({"post": {"id": "x"}}, ttl_sec=60)
    assert post_redis.load_all_record_dicts() == []
    post_redis.delete_record("x")
