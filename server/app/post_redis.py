"""募集投稿 (PostRecord) の Upstash Redis 永続化。

デプロイ・再起動後も RECORDS を復元し、ホストクライアントの id/owner_token を
維持する。Redis 未設定時は no-op (従来どおりプロセス内メモリのみ)。
"""
from __future__ import annotations

import json
import os
from typing import Any

POST_INDEX_KEY = "asobby:post:index"
POST_KEY_PREFIX = "asobby:post:"


def is_configured() -> bool:
    return bool(
        os.environ.get("UPSTASH_REDIS_REST_URL")
        and os.environ.get("UPSTASH_REDIS_REST_TOKEN")
    )


def _client():
    from upstash_redis import Redis

    return Redis.from_env()


def _post_key(post_id: str) -> str:
    return f"{POST_KEY_PREFIX}{post_id}"


def save_record_dict(data: dict[str, Any], *, ttl_sec: int) -> None:
    """PostRecord の dict 表現を Redis に保存する (TTL 付き)。"""
    if not is_configured():
        return
    post_id = str((data.get("post") or {}).get("id", ""))
    if not post_id:
        return
    payload = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    redis = _client()
    redis.set(_post_key(post_id), payload, ex=max(ttl_sec, 1))
    redis.sadd(POST_INDEX_KEY, post_id)


def delete_record(post_id: str) -> None:
    if not is_configured():
        return
    redis = _client()
    redis.delete(_post_key(post_id))
    redis.srem(POST_INDEX_KEY, post_id)


def load_all_record_dicts() -> list[dict[str, Any]]:
    """Redis 上の全 PostRecord dict を読み込む。壊れたエントリは削除する。"""
    if not is_configured():
        return []
    redis = _client()
    ids = redis.smembers(POST_INDEX_KEY)
    if not ids:
        return []
    if isinstance(ids, (str, bytes)):
        ids = [ids]
    keys = [_post_key(str(i)) for i in ids]
    raw_values = redis.mget(*keys)
    if raw_values is None:
        raw_values = []
    elif not isinstance(raw_values, list):
        raw_values = [raw_values]

    out: list[dict[str, Any]] = []
    for post_id, raw in zip(ids, raw_values):
        pid = str(post_id)
        if raw is None:
            redis.srem(POST_INDEX_KEY, pid)
            continue
        try:
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("record must be object")
            out.append(data)
        except (json.JSONDecodeError, TypeError, ValueError):
            delete_record(pid)
    return out
