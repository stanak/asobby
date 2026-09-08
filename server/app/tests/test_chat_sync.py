"""Bidirectional chat contract, with no real Discord/network traffic."""
from __future__ import annotations

import asyncio
import copy
import json
import threading
import time

import httpx
import pytest

import chat_sync
import integrations
import main
import post_redis


BODY = {
    "request_id": "discord-message-1", "external_user_id": "123456789012345678",
    "name": "Discord player", "text": "対戦しませんか？",
    "discord_user_id": "123456789012345678",
}


@pytest.fixture
def bridge(monkeypatch):
    instance = integrations.IntegrationService(lambda: [], "https://asobby.test")
    lock = asyncio.Lock()
    monkeypatch.setattr(main, "INTEGRATIONS", instance)
    monkeypatch.setattr(main, "LOBBY_CHAT_LOCK", lock)
    monkeypatch.setattr(main.CHAT_SYNC, "integrations", instance)
    monkeypatch.setattr(main.CHAT_SYNC, "lock", lock)
    monkeypatch.setattr(main.db, "is_configured", lambda: False)
    monkeypatch.setattr(post_redis, "is_redis_configured", lambda: False)
    published = []

    async def publish(kind, message):
        published.append((kind, copy.deepcopy(message)))

    async def session(request):
        return {"id": "987654321098765432", "name": "Native"} if request.headers.get("x-native") else None

    monkeypatch.setattr(main.HUB, "publish", publish)
    monkeypatch.setattr(main, "resolve_session", session)
    main.LOBBY_CHAT.clear()
    main.LOBBY_CHAT_LAST_SENT.clear()
    yield instance, published
    main.LOBBY_CHAT.clear()
    main.LOBBY_CHAT_LAST_SENT.clear()


async def key(instance, **permissions):
    result = await instance.create(integrations.IntegrationInput(name="bridge", **permissions))
    return result["integration"]["id"], {"Authorization": "Bearer " + result["api_key"]}


def client():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="https://asobby.test")


@pytest.mark.asyncio
async def test_opt_in_auth_separate_permissions_and_revocation(bridge):
    instance, published = bridge
    ident, headers = await key(instance)
    assert instance.get(ident).public()["include_chat"] is False
    assert instance.get(ident).public()["allow_chat_posting"] is False
    async with client() as http:
        for method in ("GET", "POST"):
            kwargs = {"json": BODY} if method == "POST" else {}
            assert (await http.request(method, "/api/v1/chat", **kwargs)).status_code == 401
            assert (await http.request(method, "/api/v1/chat", headers=headers, **kwargs)).status_code == 403
        await instance.update(ident, integrations.IntegrationPatch(allow_chat_posting=True))
        assert (await http.post("/api/v1/chat", headers=headers, json=BODY)).status_code == 201
        assert (await http.get("/api/v1/chat", headers=headers)).status_code == 403
        await instance.update(ident, integrations.IntegrationPatch(include_chat=True, allow_chat_posting=False))
        assert (await http.get("/api/v1/chat", headers=headers)).status_code == 200
        assert (await http.post("/api/v1/chat", headers=headers, json=BODY)).status_code == 403
        await instance.update(ident, integrations.IntegrationPatch(enabled=False))
        assert (await http.get("/api/v1/chat", headers=headers)).status_code == 401
        await instance.update(ident, integrations.IntegrationPatch(enabled=True))
        await instance.rotate(ident)
        assert (await http.get("/api/v1/chat", headers=headers)).status_code == 401
    assert len(published) == 1


@pytest.mark.asyncio
async def test_incoming_idempotency_conflict_concurrency_namespace_and_no_echo(bridge):
    instance, published = bridge
    ident, headers = await key(instance, allow_chat_posting=True, include_chat=True)
    instance.items[ident] = instance.get(ident).model_copy(update={"webhook_url": "https://receiver.example/hook"})
    async with client() as http:
        responses = await asyncio.gather(*(http.post("/api/v1/chat", headers=headers, json=BODY) for _ in range(5)))
        assert sorted(r.status_code for r in responses) == [200, 200, 200, 200, 201]
        messages = [r.json()["message"] for r in responses]
        assert all(message == messages[0] for message in messages)
        message = messages[0]
        assert message["source"] == message["discord_user_id_source"] == "integration"
        assert message["user_id"] == message["avatar"] == "" and message["mentions"] == []
        assert message["discord_user_id"] == BODY["discord_user_id"]
        assert len(published) == 1 and published[0] == ("chat_message", message)
        assert not instance.pending[ident].chat
        snapshot = await http.get("/api/v1/chat", headers=headers)
        assert snapshot.json() == {"schema_version": 1, "count": 1, "messages": [message]}
        assert snapshot.headers["cache-control"] == "private, no-store"
        assert snapshot.headers["vary"] == "Authorization"
        assert main.lobby_chat_snapshot() == [message]
        assert "_chat_sync" not in json.dumps(message) and "request_id" not in json.dumps(message)
        assert (await http.post("/api/v1/chat", headers=headers, json={**BODY, "text": "changed"})).status_code == 409
        limited = await http.post("/api/v1/chat", headers=headers, json={**BODY, "request_id": "another"})
        assert limited.status_code == 429 and limited.headers["retry-after"] == "3"
        _, other = await key(instance, allow_chat_posting=True)
        independent = await http.post("/api/v1/chat", headers=other, json=BODY)
        assert independent.status_code == 201
        assert independent.json()["message"]["id"] != message["id"]


@pytest.mark.asyncio
async def test_native_notification_is_opt_in_public_and_nonblocking(bridge):
    instance, published = bridge
    opted, _ = await key(instance, include_chat=True)
    default, _ = await key(instance)
    for ident in (opted, default):
        instance.items[ident] = instance.get(ident).model_copy(update={"webhook_url": "https://receiver.example/hook"})
    async with client() as http:
        response = await http.post("/lobby/chat", headers={"x-native": "1"}, json={"text": "Native chat"})
    assert response.status_code == 200
    message = response.json()["message"]
    assert message["source"] == "asobby" and message["discord_user_id_source"] == "oauth"
    event = instance.pending[opted].chat[0]
    assert event["type"] == "chat.message.created"
    assert event["data"] == {"message": message, "snapshot_url": "https://asobby.test/api/v1/chat"}
    assert not instance.pending[default].chat
    assert published == [("chat_message", message)]
    await instance.update(opted, integrations.IntegrationPatch(include_chat=False))
    assert not instance.pending[opted].chat


@pytest.mark.asyncio
@pytest.mark.parametrize("patch", [
    {"text": " "}, {"text": "a" * 501}, {"text": "a\n" * 8 + "a"},
    {"name": " "}, {"name": "fake\nuser"}, {"external_user_id": ""},
    {"request_id": "a" * 129}, {"discord_user_id": 123},
    {"discord_user_id": "18446744073709551616"}, {"discord_user_id": "0"},
    {"avatar": "https://tracker.example"}, {"user_id": "native"},
    {"source": "asobby"}, {"mentions": [{"user_id": "victim"}]},
])
async def test_invalid_and_spoofed_input_rejected(bridge, patch):
    instance, published = bridge
    _, headers = await key(instance, allow_chat_posting=True)
    async with client() as http:
        assert (await http.post("/api/v1/chat", headers=headers, json={**BODY, **patch})).status_code == 422
    assert not published and not main.LOBBY_CHAT


@pytest.mark.asyncio
async def test_normalization_and_optional_discord_id(bridge):
    instance, _ = bridge
    _, headers = await key(instance, allow_chat_posting=True)
    body = {**BODY, "text": " \r\nhello\rworld \n", "discord_user_id": None}
    async with client() as http:
        result = (await http.post("/api/v1/chat", headers=headers, json=body)).json()
        assert result["message"]["text"] == "hello\nworld"
        assert result["message"]["discord_user_id"] is None
        assert result["message"]["discord_user_id_source"] is None
        response = await http.post("/api/v1/chat", headers=headers, json={**body, "text": "hello\nworld"})
        assert response.status_code == 200


@pytest.mark.asyncio
async def test_receipts_survive_local_storage_restart_and_stay_private(bridge, monkeypatch):
    instance, published = bridge
    monkeypatch.setenv("ASOBBY_STORE", "local")
    _, headers = await key(instance, allow_chat_posting=True, include_chat=True)
    async with client() as http:
        first = await http.post("/api/v1/chat", headers=headers, json=BODY)
        assert first.status_code == 201
        main.LOBBY_CHAT.clear()
        await main._hydrate_chat_from_redis()
        assert main.LOBBY_CHAT[0]["_chat_sync"]["request_id"] == BODY["request_id"]
        retry = await http.post("/api/v1/chat", headers=headers, json=BODY)
        assert retry.status_code == 200 and retry.json()["duplicate"] is True
        assert retry.json()["message"] == first.json()["message"]
        history = await http.get("/api/v1/chat", headers=headers)
        assert "_chat_sync" not in history.text
    assert len(published) == 1


@pytest.mark.asyncio
async def test_failed_storage_does_not_publish_or_consume_request_id(bridge, monkeypatch):
    instance, published = bridge
    monkeypatch.setenv("ASOBBY_STORE", "local")
    _, headers = await key(instance, allow_chat_posting=True)
    append = post_redis.append_chat_message
    def fail(*args, **kwargs):
        raise OSError("secret storage details")
    monkeypatch.setattr(post_redis, "append_chat_message", fail)
    async with client() as http:
        failure = await http.post("/api/v1/chat", headers=headers, json=BODY)
        assert failure.status_code == 503 and "secret" not in failure.text
        assert not main.LOBBY_CHAT and not published
        monkeypatch.setattr(post_redis, "append_chat_message", append)
        assert (await http.post("/api/v1/chat", headers=headers, json=BODY)).status_code == 201


@pytest.mark.asyncio
async def test_cancellation_finishes_persistence_before_unlocking(bridge, monkeypatch):
    instance, published = bridge
    monkeypatch.setenv("ASOBBY_STORE", "local")
    ident, _ = await key(instance, allow_chat_posting=True)
    started, release = threading.Event(), threading.Event()
    append = post_redis.append_chat_message
    def slow(*args, **kwargs):
        started.set()
        assert release.wait(5)
        append(*args, **kwargs)
    monkeypatch.setattr(post_redis, "append_chat_message", slow)
    body, item = chat_sync.ChatInput(**BODY), instance.get(ident)
    first = asyncio.create_task(chat_sync.durable(main.CHAT_SYNC.create(body, item)))
    try:
        assert await asyncio.to_thread(started.wait, 5)
        first.cancel()
        await asyncio.sleep(0)
        assert not first.done() and main.LOBBY_CHAT_LOCK.locked()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await first
    result, status = await main.CHAT_SYNC.create(body, item)
    assert status == 200 and result["duplicate"] is True
    assert len(published) == 1


@pytest.mark.asyncio
async def test_history_bounds_legacy_records_and_shared_rate_limit(bridge):
    instance, _ = bridge
    _, headers = await key(instance, include_chat=True)
    for n in range(105):
        main.LOBBY_CHAT.append({"id": str(n), "user_id": "123", "name": "old", "text": str(n), "ts": time.time() - (4000 if n < 6 else 0)})
    async with client() as http:
        result = (await http.get("/api/v1/chat", headers=headers)).json()
        assert result["count"] == 99 and result["messages"][0]["id"] == "6"
        assert result["messages"][0]["source"] == "asobby"
        for _ in range(59):
            # Same shared authenticator used by /api/v1/lobby and other APIs.
            instance.authenticate(httpx.Request("GET", "https://asobby.test/api/v1/lobby", headers=headers))
        assert (await http.get("/api/v1/chat", headers=headers)).status_code == 429


@pytest.mark.asyncio
async def test_chat_queue_bounds_fairness_retry_and_ack(bridge, monkeypatch):
    instance, _ = bridge
    ident, _ = await key(instance, include_chat=True)
    instance.items[ident] = instance.get(ident).model_copy(update={"webhook_url": "https://receiver.example/hook"})
    for n in range(205):
        instance.chat_created({"source": "asobby", "id": str(n), "text": "hello"})
    pending = instance.pending[ident]
    assert len(pending.chat) == 200
    assert pending.chat[0]["data"]["message"]["id"] == "0"
    assert pending.chat[1]["data"]["message"]["id"] == "6"
    instance.post_created("post1")
    pending.event = instance.event()
    seen = []
    async def success(item, event):
        seen.append(event["type"])
        return 204, 0
    monkeypatch.setattr(integrations, "deliver", success)
    for _ in range(3):
        await instance._send(instance.get(ident), pending, pending.next_event())
    assert seen == ["post.created", "lobby.changed", "chat.message.created"]
    assert len(pending.chat) == 199 and not pending.created and pending.event is None
    event = pending.next_event()
    async def fail(item, event):
        return 429, 45
    monkeypatch.setattr(integrations, "deliver", fail)
    before = time.monotonic()
    await instance._send(instance.get(ident), pending, event)
    assert pending.next_event()["id"] == event["id"] and pending.next_at >= before + 45
