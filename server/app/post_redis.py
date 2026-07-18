"""asobby の Upstash Redis 永続化。

- 募集投稿 (PostRecord): デプロイ・再起動後も復元
- ロビーチャット: デプロイ・再起動後も履歴を維持

Redis 未設定時は no-op (従来どおりプロセス内メモリのみ)。
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

POST_INDEX_KEY = "asobby:post:index"
POST_KEY_PREFIX = "asobby:post:"
CHAT_LIST_KEY = "asobby:lobby:chat"  # legacy (pre JP/EN split)
CHAT_LIST_KEY_PREFIX = "asobby:lobby:chat:"
CHAT_LANGS = ("ja", "en")
CHAT_COOLDOWN_PREFIX = "asobby:lobby:chat:cooldown:"


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


def _chat_cooldown_key(user_id: str) -> str:
    return f"{CHAT_COOLDOWN_PREFIX}{user_id}"


def chat_cooldown_remaining(user_id: str) -> float:
    """送信可能になるまでの残秒。0 なら送信可。Redis 未設定時は常に 0。"""
    if not is_configured():
        return 0.0
    redis = _client()
    ttl = redis.ttl(_chat_cooldown_key(user_id))
    if ttl is None or ttl < 0:
        return 0.0
    return float(ttl)


def chat_cooldown_mark(user_id: str, cooldown_sec: float) -> None:
    if not is_configured():
        return
    redis = _client()
    redis.set(
        _chat_cooldown_key(user_id),
        "1",
        ex=max(int(cooldown_sec), 1),
    )


def normalize_chat_lang(lang: str | None) -> str:
    if lang and str(lang).lower().startswith("en"):
        return "en"
    return "ja"


def _chat_list_key(lang: str) -> str:
    return f"{CHAT_LIST_KEY_PREFIX}{normalize_chat_lang(lang)}"


def _parse_chat_raw_values(raw_values: Any) -> list[Any]:
    if raw_values is None:
        return []
    if not isinstance(raw_values, list):
        return [raw_values]
    return raw_values


def _filter_chat_messages(
    raw_values: list[Any],
    *,
    max_messages: int,
    max_age_sec: float,
    now: float | None = None,
    default_lang: str | None = None,
) -> list[dict[str, Any]]:
    cutoff = (now if now is not None else time.time()) - max_age_sec
    out: list[dict[str, Any]] = []
    for raw in raw_values:
        if raw is None:
            continue
        try:
            msg = json.loads(raw)
            if not isinstance(msg, dict):
                continue
            if float(msg.get("ts", 0)) < cutoff:
                continue
            if default_lang and "lang" not in msg:
                msg = dict(msg)
                msg["lang"] = default_lang
            out.append(msg)
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
    if max_messages > 0 and len(out) > max_messages:
        out = out[-max_messages:]
    return out


def _migrate_legacy_chat_list(redis: Any) -> None:
    """旧単一リストを JP チャンネルへ移行する (1 回限り)。"""
    try:
        legacy = redis.lrange(CHAT_LIST_KEY, 0, -1)
    except Exception:
        return
    raw_values = _parse_chat_raw_values(legacy)
    if not raw_values:
        return
    ja_key = _chat_list_key("ja")
    for raw in raw_values:
        if raw is None:
            continue
        try:
            msg = json.loads(raw)
            if not isinstance(msg, dict):
                continue
            if "lang" not in msg:
                msg = dict(msg)
                msg["lang"] = "ja"
            payload = json.dumps(msg, separators=(",", ":"), ensure_ascii=False)
            redis.rpush(ja_key, payload)
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
    redis.delete(CHAT_LIST_KEY)


def append_chat_message(msg: dict[str, Any], *, max_messages: int) -> None:
    """チャット 1 件を Redis リスト末尾に追加し、件数上限で切り詰める。"""
    if not is_configured():
        return
    lang = normalize_chat_lang(msg.get("lang"))
    payload = json.dumps(
        {**msg, "lang": lang},
        separators=(",", ":"),
        ensure_ascii=False,
    )
    redis = _client()
    key = _chat_list_key(lang)
    redis.rpush(key, payload)
    if max_messages > 0:
        redis.ltrim(key, -max_messages, -1)


def load_chat_messages(
    lang: str,
    *,
    max_messages: int,
    max_age_sec: float,
    now: float | None = None,
) -> list[dict[str, Any]]:
    """Redis から指定言語のチャット履歴を読み込む (古い順)。"""
    if not is_configured():
        return []
    redis = _client()
    normalized = normalize_chat_lang(lang)
    if normalized == "ja":
        _migrate_legacy_chat_list(redis)
    raw_values = _parse_chat_raw_values(redis.lrange(_chat_list_key(normalized), 0, -1))
    return _filter_chat_messages(
        raw_values,
        max_messages=max_messages,
        max_age_sec=max_age_sec,
        now=now,
    )


def load_all_chat_messages(
    *,
    max_messages: int,
    max_age_sec: float,
    now: float | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """全言語チャンネルのチャット履歴を読み込む。"""
    return {
        lang: load_chat_messages(
            lang,
            max_messages=max_messages,
            max_age_sec=max_age_sec,
            now=now,
        )
        for lang in CHAT_LANGS
    }


def replace_chat_messages(
    lang: str,
    messages: list[dict[str, Any]],
    *,
    max_messages: int,
    max_age_sec: float,
    now: float | None = None,
) -> None:
    """メモリ上のスナップショットで Redis リストを置き換える (年齢トリム後)。"""
    if not is_configured():
        return
    normalized = normalize_chat_lang(lang)
    cutoff = (now if now is not None else time.time()) - max_age_sec
    kept = [
        {**m, "lang": normalized}
        for m in messages
        if float(m.get("ts", 0)) >= cutoff
    ]
    if max_messages > 0 and len(kept) > max_messages:
        kept = kept[-max_messages:]
    redis = _client()
    key = _chat_list_key(normalized)
    redis.delete(key)
    if not kept:
        return
    payloads = [
        json.dumps(m, separators=(",", ":"), ensure_ascii=False) for m in kept
    ]
    redis.rpush(key, *payloads)
