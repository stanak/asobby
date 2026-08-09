"""asobby の募集・ロビーチャット永続化。

- Upstash Redis (`UPSTASH_REDIS_REST_*` 設定時): 複数インスタンス向け
- ローカルファイル (`ASOBBY_STORE_DIR`, 既定): Redis 未設定時の既定バックエンド
- `ASOBBY_STORE=memory`: 永続化なし (テスト用)

presence (閲覧人数) は Redis 利用時のみ共有。それ以外は main のインメモリ fallback。
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

POST_INDEX_KEY = "asobby:post:index"
POST_KEY_PREFIX = "asobby:post:"
CHAT_LIST_KEY = "asobby:lobby:chat"  # legacy (pre JP/EN split)
CHAT_LIST_KEY_PREFIX = "asobby:lobby:chat:"
CHAT_LANGS = ("ja", "en")
CHAT_COOLDOWN_PREFIX = "asobby:lobby:chat:cooldown:"

PRESENCE_ZSET_KEY = "asobby:presence"
PRESENCE_TTL_SEC = 90

ANNOUNCEMENT_KEY = "asobby:announcement"

_LOCAL_LOCK = threading.Lock()


def is_redis_configured() -> bool:
    return bool(
        os.environ.get("UPSTASH_REDIS_REST_URL")
        and os.environ.get("UPSTASH_REDIS_REST_TOKEN")
    )


def _local_store_enabled() -> bool:
    if is_redis_configured():
        return False
    return os.environ.get("ASOBBY_STORE", "").lower() != "memory"


def is_configured() -> bool:
    return is_redis_configured() or _local_store_enabled()


def _store_dir() -> Path:
    raw = os.environ.get("ASOBBY_STORE_DIR")
    if raw:
        return Path(raw)
    if Path("/data").is_dir():
        return Path("/data/asobby")
    return Path(__file__).resolve().parent.parent / "data" / "asobby"


def _local_posts_dir() -> Path:
    return _store_dir() / "posts"


def _local_chat_path(lang: str) -> Path:
    return _store_dir() / "chat" / f"{normalize_chat_lang(lang)}.json"


def _local_cooldown_path() -> Path:
    return _store_dir() / "chat_cooldowns.json"


def _ensure_local_dirs() -> None:
    _local_posts_dir().mkdir(parents=True, exist_ok=True)
    (_store_dir() / "chat").mkdir(parents=True, exist_ok=True)


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _client():
    from upstash_redis import Redis

    return Redis.from_env()


def _post_key(post_id: str) -> str:
    return f"{POST_KEY_PREFIX}{post_id}"


def _local_save_record_dict(data: dict[str, Any], *, ttl_sec: int) -> None:
    post_id = str((data.get("post") or {}).get("id", ""))
    if not post_id:
        return
    with _LOCAL_LOCK:
        _ensure_local_dirs()
        payload = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
        _atomic_write_text(_local_posts_dir() / f"{post_id}.json", payload)


def _local_delete_record(post_id: str) -> None:
    with _LOCAL_LOCK:
        path = _local_posts_dir() / f"{post_id}.json"
        path.unlink(missing_ok=True)


def _local_load_all_record_dicts() -> list[dict[str, Any]]:
    with _LOCAL_LOCK:
        posts_dir = _local_posts_dir()
        if not posts_dir.is_dir():
            return []
        out: list[dict[str, Any]] = []
        for path in sorted(posts_dir.glob("*.json")):
            try:
                raw = path.read_text(encoding="utf-8")
                data = json.loads(raw)
                if not isinstance(data, dict):
                    raise ValueError("record must be object")
                out.append(data)
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                path.unlink(missing_ok=True)
        return out


def _local_load_cooldowns() -> dict[str, float]:
    path = _local_cooldown_path()
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return {}
        return {str(k): float(v) for k, v in raw.items()}
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return {}


def _local_save_cooldowns(data: dict[str, float]) -> None:
    now = time.time()
    kept = {uid: exp for uid, exp in data.items() if exp > now}
    with _LOCAL_LOCK:
        _local_save_cooldowns_unlocked(kept)


def _local_save_cooldowns_unlocked(kept: dict[str, float]) -> None:
    _ensure_local_dirs()
    if kept:
        payload = json.dumps(kept, separators=(",", ":"))
        _atomic_write_text(_local_cooldown_path(), payload)
    else:
        _local_cooldown_path().unlink(missing_ok=True)


def _local_chat_cooldown_remaining(user_id: str) -> float:
    with _LOCAL_LOCK:
        cooldowns = _local_load_cooldowns()
    expires = cooldowns.get(user_id, 0.0)
    remaining = expires - time.time()
    return max(remaining, 0.0)


def _local_chat_cooldown_mark(user_id: str, cooldown_sec: float) -> None:
    with _LOCAL_LOCK:
        cooldowns = _local_load_cooldowns()
        cooldowns[user_id] = time.time() + max(cooldown_sec, 0.0)
        now = time.time()
        kept = {uid: exp for uid, exp in cooldowns.items() if exp > now}
        _local_save_cooldowns_unlocked(kept)


def _local_load_chat_file(lang: str) -> list[Any]:
    path = _local_chat_path(lang)
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, list):
            return raw
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        pass
    return []


def _local_write_chat_file(lang: str, messages: list[dict[str, Any]]) -> None:
    with _LOCAL_LOCK:
        _ensure_local_dirs()
        payloads = [
            json.dumps(m, separators=(",", ":"), ensure_ascii=False) for m in messages
        ]
        _atomic_write_text(_local_chat_path(lang), "[" + ",".join(payloads) + "]")


def _local_append_chat_message(msg: dict[str, Any], *, max_messages: int) -> None:
    lang = normalize_chat_lang(msg.get("lang"))
    normalized = {**msg, "lang": lang}
    with _LOCAL_LOCK:
        _ensure_local_dirs()
        path = _local_chat_path(lang)
        messages = _local_load_chat_file(lang)
        parsed: list[dict[str, Any]] = []
        for raw in messages:
            if isinstance(raw, dict):
                parsed.append(raw)
            else:
                try:
                    item = json.loads(raw)
                    if isinstance(item, dict):
                        parsed.append(item)
                except (json.JSONDecodeError, TypeError, ValueError):
                    continue
        parsed.append(normalized)
        if max_messages > 0 and len(parsed) > max_messages:
            parsed = parsed[-max_messages:]
        payloads = [
            json.dumps(m, separators=(",", ":"), ensure_ascii=False) for m in parsed
        ]
        _atomic_write_text(path, "[" + ",".join(payloads) + "]")


def save_record_dict(data: dict[str, Any], *, ttl_sec: int) -> None:
    """PostRecord の dict 表現を保存する (TTL は hydrate 側で判定)。"""
    if is_redis_configured():
        post_id = str((data.get("post") or {}).get("id", ""))
        if not post_id:
            return
        payload = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
        redis = _client()
        redis.set(_post_key(post_id), payload, ex=max(ttl_sec, 1))
        redis.sadd(POST_INDEX_KEY, post_id)
        return
    if _local_store_enabled():
        _local_save_record_dict(data, ttl_sec=ttl_sec)


def delete_record(post_id: str) -> None:
    if is_redis_configured():
        redis = _client()
        redis.delete(_post_key(post_id))
        redis.srem(POST_INDEX_KEY, post_id)
        return
    if _local_store_enabled():
        _local_delete_record(post_id)


def load_all_record_dicts() -> list[dict[str, Any]]:
    """全 PostRecord dict を読み込む。壊れたエントリは削除する。"""
    if is_redis_configured():
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
    if _local_store_enabled():
        return _local_load_all_record_dicts()
    return []


def _chat_cooldown_key(user_id: str) -> str:
    return f"{CHAT_COOLDOWN_PREFIX}{user_id}"


def chat_cooldown_remaining(user_id: str) -> float:
    """送信可能になるまでの残秒。0 なら送信可。永続化無効時は常に 0。"""
    if is_redis_configured():
        redis = _client()
        ttl = redis.ttl(_chat_cooldown_key(user_id))
        if ttl is None or ttl < 0:
            return 0.0
        return float(ttl)
    if _local_store_enabled():
        return _local_chat_cooldown_remaining(user_id)
    return 0.0


def chat_cooldown_mark(user_id: str, cooldown_sec: float) -> None:
    if is_redis_configured():
        redis = _client()
        redis.set(
            _chat_cooldown_key(user_id),
            "1",
            ex=max(int(cooldown_sec), 1),
        )
        return
    if _local_store_enabled():
        _local_chat_cooldown_mark(user_id, cooldown_sec)


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
            msg = raw if isinstance(raw, dict) else json.loads(raw)
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
    """チャット 1 件をリスト末尾に追加し、件数上限で切り詰める。"""
    if is_redis_configured():
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
        return
    if _local_store_enabled():
        _local_append_chat_message(msg, max_messages=max_messages)


def load_chat_messages(
    lang: str,
    *,
    max_messages: int,
    max_age_sec: float,
    now: float | None = None,
) -> list[dict[str, Any]]:
    """指定言語のチャット履歴を読み込む (古い順)。"""
    normalized = normalize_chat_lang(lang)
    if is_redis_configured():
        redis = _client()
        if normalized == "ja":
            _migrate_legacy_chat_list(redis)
        raw_values = _parse_chat_raw_values(
            redis.lrange(_chat_list_key(normalized), 0, -1)
        )
        return _filter_chat_messages(
            raw_values,
            max_messages=max_messages,
            max_age_sec=max_age_sec,
            now=now,
        )
    if _local_store_enabled():
        with _LOCAL_LOCK:
            raw_values = _local_load_chat_file(normalized)
        return _filter_chat_messages(
            raw_values,
            max_messages=max_messages,
            max_age_sec=max_age_sec,
            now=now,
            default_lang=normalized,
        )
    return []


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
    """メモリ上のスナップショットでリストを置き換える (年齢トリム後)。"""
    normalized = normalize_chat_lang(lang)
    cutoff = (now if now is not None else time.time()) - max_age_sec
    kept = [
        {**m, "lang": normalized}
        for m in messages
        if float(m.get("ts", 0)) >= cutoff
    ]
    if max_messages > 0 and len(kept) > max_messages:
        kept = kept[-max_messages:]
    if is_redis_configured():
        redis = _client()
        key = _chat_list_key(normalized)
        redis.delete(key)
        if not kept:
            return
        payloads = [
            json.dumps(m, separators=(",", ":"), ensure_ascii=False) for m in kept
        ]
        redis.rpush(key, *payloads)
        return
    if _local_store_enabled():
        _local_write_chat_file(normalized, kept)


def _local_announcement_path() -> Path:
    return _store_dir() / "announcement.json"


def save_announcement(data: dict[str, Any] | None) -> None:
    """お知らせを保存する。None なら削除。"""
    if is_redis_configured():
        redis = _client()
        if data is None:
            redis.delete(ANNOUNCEMENT_KEY)
        else:
            payload = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
            redis.set(ANNOUNCEMENT_KEY, payload)
        return
    if _local_store_enabled():
        with _LOCAL_LOCK:
            path = _local_announcement_path()
            if data is None:
                path.unlink(missing_ok=True)
            else:
                _ensure_local_dirs()
                payload = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
                _atomic_write_text(path, payload)


def load_announcement() -> dict[str, Any] | None:
    """保存済みのお知らせを返す (なければ None)。"""
    if is_redis_configured():
        redis = _client()
        raw = redis.get(ANNOUNCEMENT_KEY)
        if not raw:
            return None
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else None
        except (json.JSONDecodeError, TypeError, ValueError):
            return None
    if _local_store_enabled():
        with _LOCAL_LOCK:
            path = _local_announcement_path()
            if not path.is_file():
                return None
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                return data if isinstance(data, dict) else None
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                return None
    return None


def presence_touch(visitor_id: str, *, now: float | None = None) -> int:
    """訪問者の last_seen を更新し、TTL 内の人数を返す。"""
    if not is_redis_configured():
        raise RuntimeError("redis not configured")
    now = now if now is not None else time.time()
    redis = _client()
    redis.zadd(PRESENCE_ZSET_KEY, {visitor_id: now})
    redis.zremrangebyscore(PRESENCE_ZSET_KEY, 0, now - PRESENCE_TTL_SEC)
    card = redis.zcard(PRESENCE_ZSET_KEY)
    return int(card or 0)


def presence_count(*, now: float | None = None) -> int:
    """TTL 内の訪問者数を返す (期限切れエントリは削除)。"""
    if not is_redis_configured():
        raise RuntimeError("redis not configured")
    now = now if now is not None else time.time()
    redis = _client()
    redis.zremrangebyscore(PRESENCE_ZSET_KEY, 0, now - PRESENCE_TTL_SEC)
    card = redis.zcard(PRESENCE_ZSET_KEY)
    return int(card or 0)
