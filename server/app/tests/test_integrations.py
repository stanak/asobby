"""Generic integration contract, authorization and delivery isolation."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import stat
import threading
import time

import httpx
import pytest
from fastapi import FastAPI, HTTPException

import integrations as mod
import post_redis


@pytest.fixture
def service(monkeypatch):
    posts = [{
        "id": "p1", "created_at": 10, "updated_at": time.time(),
        "owner_name": "host", "addr": "203.0.113.7:10800",
        "net_status": 3, "comment": "hello", "post_type": "casual",
        "owner_token": "never-export", "guest_user_id": "private-id",
        "pending_messages": [{"text": "private"}],
    }]
    instance = mod.IntegrationService(lambda: posts, "https://lobby.example")
    saved = []
    monkeypatch.setattr(post_redis, "save_integrations", lambda items: saved.append(items))
    monkeypatch.setattr(post_redis, "load_integrations", lambda: [])

    async def resolve(url):
        return httpx.URL(url).copy_with(host="8.8.8.8"), "receiver.example", "receiver.example"

    monkeypatch.setattr(mod, "resolve_webhook_target", resolve)
    return instance, posts, saved


def app_for(service):
    app = FastAPI()

    async def session(request):
        user = request.headers.get("X-Test-User")
        return {"id": user} if user else None

    app.include_router(mod.build_router(service, session, lambda uid: uid == "admin"))
    return app


@pytest.mark.asyncio
async def test_admin_crud_keys_and_scoped_snapshot(service):
    instance, _, saved = service
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_for(instance)), base_url="https://test") as client:
        assert (await client.get("/admin/integrations")).status_code == 401
        regular = {"X-Test-User": "user"}
        admin = {"X-Test-User": "admin"}
        for method, path, body in [
            ("GET", "/admin/integrations", None),
            ("POST", "/admin/integrations", {"name": "test"}),
            ("PATCH", "/admin/integrations/missing", {"enabled": False}),
            ("DELETE", "/admin/integrations/missing", None),
            ("POST", "/admin/integrations/missing/rotate", None),
            ("POST", "/admin/integrations/missing/test", None),
        ]:
            response = await client.request(method, path, json=body, headers=regular)
            assert response.status_code == 403
        response = await client.post("/admin/integrations", json={"name": "external"}, headers=admin)
        assert response.status_code == 201
        assert response.headers["cache-control"] == "no-store"
        credentials = response.json()
        ident = credentials["integration"]["id"]
        headers = {"Authorization": "Bearer " + credentials["api_key"]}
        serialized = json.dumps(saved)
        assert credentials["api_key"] not in serialized
        listing = await client.get("/admin/integrations", headers=admin)
        assert credentials["signing_secret"] not in listing.text
        assert "api_key_hash" not in listing.text

        response = await client.get("/api/v1/lobby", headers=headers)
        assert response.status_code == 200
        snapshot = response.json()
        post = snapshot["posts"][0]
        assert snapshot["count"] == 1 and post["status"] == "waiting"
        assert not {"addr", "owner_token", "guest_user_id", "pending_messages", "updated_at"} & post.keys()
        assert response.headers["vary"] == "Authorization"
        etag = response.headers["etag"]
        assert (await client.get("/api/v1/lobby", headers={**headers, "If-None-Match": etag})).status_code == 304
        assert (await client.get("/api/v1/lobby", headers={"X-Test-User": "admin"})).status_code == 401
        assert (await client.get("/api/v1/lobby", params={"api_key": credentials["api_key"]})).status_code == 401
        assert (await client.post("/api/v1/lobby", headers=headers, json={})).status_code == 405
        assert (await client.delete("/admin/integrations/" + ident, headers=headers)).status_code == 401

        assert (await client.patch("/admin/integrations/" + ident, headers=admin, json={"include_address": True})).status_code == 200
        response = await client.get("/api/v1/lobby", headers={**headers, "If-None-Match": etag})
        assert response.status_code == 200
        assert response.json()["posts"][0]["addr"] == "203.0.113.7:10800"
        assert (await client.patch("/admin/integrations/" + ident, headers=admin, json={"enabled": False})).status_code == 200
        assert (await client.get("/api/v1/lobby", headers=headers)).status_code == 401
        await client.patch("/admin/integrations/" + ident, headers=admin, json={"enabled": True})
        assert (await client.get("/api/v1/lobby", headers=headers)).status_code == 200
        rotated = (await client.post("/admin/integrations/" + ident + "/rotate", headers=admin)).json()
        assert rotated["signing_secret"] != credentials["signing_secret"]
        assert (await client.get("/api/v1/lobby", headers=headers)).status_code == 401
        headers = {"Authorization": "Bearer " + rotated["api_key"]}
        assert (await client.get("/api/v1/lobby", headers=headers)).status_code == 200
        assert (await client.delete("/admin/integrations/" + ident, headers=admin)).status_code == 204
        assert (await client.get("/api/v1/lobby", headers=headers)).status_code == 401
        assert saved[-1] == []


@pytest.mark.asyncio
async def test_cross_origin_and_invalid_configuration(service):
    instance, _, _ = service
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_for(instance)), base_url="https://test") as client:
        admin = {"X-Test-User": "admin"}
        for headers in [{"Origin": "https://attacker.example"}, {"Sec-Fetch-Site": "cross-site"}]:
            assert (await client.post("/admin/integrations", headers={**admin, **headers}, json={"name": "test"})).status_code == 403
        for body in [{"name": " "}, {"name": "test", "unknown": True}, {"name": "test", "webhook_url": "http://public.example"}]:
            assert (await client.post("/admin/integrations", headers=admin, json=body)).status_code == 422
        created = (await client.post("/admin/integrations", headers=admin, json={"name": "ok"})).json()
        ident = created["integration"]["id"]
        assert (await client.patch("/admin/integrations/" + ident, headers=admin, json={"name": " "})).status_code == 422
        assert (await client.patch("/admin/integrations/" + ident, headers=admin, json={"enabled": None})).status_code == 422
        assert (await client.post("/admin/integrations/" + ident + "/test", headers=admin)).status_code == 409


@pytest.mark.asyncio
async def test_limits_and_url_redaction(service, monkeypatch):
    instance, _, _ = service
    result = await instance.create(mod.IntegrationInput(name="test", webhook_url="https://receiver.example/secret?token=hidden"))
    assert "secret" not in json.dumps(result["integration"])
    assert "hidden" not in json.dumps(result["integration"])
    monkeypatch.setattr(mod, "MAX_INTEGRATIONS", 1)
    with pytest.raises(HTTPException) as caught:
        await instance.create(mod.IntegrationInput(name="overflow"))
    assert caught.value.status_code == 409
    monkeypatch.setattr(mod, "API_REQUESTS_PER_MINUTE", 2)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_for(instance)), base_url="https://test") as client:
        headers = {"Authorization": "Bearer " + result["api_key"]}
        assert (await client.get("/api/v1/lobby", headers=headers)).status_code == 200
        assert (await client.get("/api/v1/lobby", headers=headers)).status_code == 200
        response = await client.get("/api/v1/lobby", headers=headers)
        assert response.status_code == 429 and response.headers["retry-after"] == "60"


@pytest.mark.parametrize("url", [
    "http://example.com", "ftp://example.com", "https://user:password@example.com",
    "https://example.com/#secret", "https://example.com:99999", "https://example.com/\nsecret",
])
def test_unsafe_url_syntax(url):
    with pytest.raises(ValueError):
        mod.normalize_webhook_url(url)


@pytest.mark.asyncio
@pytest.mark.parametrize("address", [
    "127.0.0.1", "10.1.2.3", "172.16.0.1", "192.168.1.1", "169.254.169.254",
    "100.64.0.1", "224.0.0.1", "::1", "fc00::1", "fe80::1", "::ffff:127.0.0.1",
    "64:ff9b::7f00:1", "2002:7f00:1::",
])
async def test_private_dns_and_mixed_answers_are_blocked(monkeypatch, address):
    async def resolve(*args, **kwargs):
        return [(0, 0, 0, "", ("8.8.8.8", 443)), (0, 0, 0, "", (address, 443))]
    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolve)
    with pytest.raises(ValueError):
        await mod.resolve_webhook_target("https://receiver.example/hook")


@pytest.mark.asyncio
@pytest.mark.parametrize("event_type", ["lobby.changed", "post.created"])
async def test_pinned_signed_request_never_follows_redirect_or_reads_body(monkeypatch, event_type):
    async def resolve(*args, **kwargs):
        return [(0, 0, 0, "", ("8.8.8.8", 443))]
    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolve)
    item = mod.Integration(
        id="a" * 32, name="test", webhook_url="https://receiver.example/secret?key=abc",
        api_key_hash="x", signing_secret="test-signing-key", generation="g", created_at=0,
    )
    event = {"id": "event1", "type": event_type, "data": {"count": 0}}
    if event_type == "post.created":
        event["data"]["post_id"] = "post1"
    calls = []

    class UnreadBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            raise AssertionError("response body must not be consumed")
            yield b""  # pragma: no cover

    def receive(request):
        calls.append(request)
        assert request.url.host == "8.8.8.8"
        assert request.url.path == "/secret"
        assert request.headers["host"] == "receiver.example"
        assert request.extensions["sni_hostname"] == "receiver.example"
        expected = hmac.new(
            item.signing_secret.encode(),
            request.headers["x-asobby-timestamp"].encode() + b"." + request.content,
            hashlib.sha256,
        ).hexdigest()
        assert request.headers["x-asobby-signature"] == "sha256=" + expected
        assert request.headers["x-asobby-delivery"] == "event1"
        assert request.headers["x-asobby-event"] == event_type
        assert json.loads(request.content) == event
        return httpx.Response(302, headers={"Location": "http://127.0.0.1/private"}, stream=UnreadBody())

    real_client = httpx.AsyncClient
    def client_factory(**kwargs):
        assert kwargs["trust_env"] is False
        assert kwargs["follow_redirects"] is False
        return real_client(transport=httpx.MockTransport(receive), **kwargs)
    monkeypatch.setattr(mod.httpx, "AsyncClient", client_factory)
    assert await mod.deliver(item, event) == (302, 0)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_heartbeat_suppression_zero_list_retry_and_coalescing(service, monkeypatch):
    instance, posts, _ = service
    result = await instance.create(mod.IntegrationInput(name="test", webhook_url="https://receiver.example/hook"))
    ident = result["integration"]["id"]
    calls = []
    replies = iter([(503, 120), (204, 0), (204, 0)])

    async def send(item, event):
        calls.append(event)
        return next(replies)

    monkeypatch.setattr(mod, "deliver", send)
    await instance.tick()
    pending = instance.pending[ident]
    initial_id = pending.event["id"]
    posts[0]["updated_at"] += 5
    posts[0]["ping_warn_ms"] = 200
    await instance.tick()
    assert pending.event["id"] == initial_id
    pending.next_at = 0
    await instance.tick()
    await asyncio.gather(*instance._jobs.values())
    assert instance.get(ident).last_status == 503
    assert pending.attempts == 1
    assert pending.next_at > time.monotonic() + 110
    await instance.tick()
    assert len(calls) == 1
    # A change replaces the pending state but does not defeat Retry-After.
    posts[0]["net_status"] = 4
    await instance.tick()
    assert pending.event["id"] != initial_id
    assert pending.next_at > time.monotonic() + 110
    posts.clear()
    await instance.tick()
    assert pending.event["data"]["count"] == 0
    assert "203.0.113" not in json.dumps(pending.event)
    assert "posts" not in pending.event["data"]
    pending.next_at = 0
    await instance.tick()
    await asyncio.gather(*instance._jobs.values())
    await instance.tick()
    assert calls[-1]["data"]["count"] == 0
    assert pending.event is None
    assert instance.get(ident).last_success_at is not None
    assert instance.get(ident).last_error is None
    instance.queue_test(ident)
    assert pending.event["test"] is True
    await instance.stop()


@pytest.mark.asyncio
async def test_rank_evidence_alone_triggers_webhook_and_snapshot_revision(service):
    instance, posts, _ = service
    posts[0].update(rank="normal", rank_status="initial", ranked_games=0)
    cred = await instance.create(mod.IntegrationInput(name="test", webhook_url="https://receiver.example/hook"))
    ident = cred["integration"]["id"]
    try:
        await instance.tick()
        previous_id = instance.pending[ident].event["id"]
        previous_revision = instance.snapshot()["revision"]
        for status, games in [("provisional", 1), ("provisional", 2), ("ranked", 50)]:
            posts[0].update(rank_status=status, ranked_games=games)
            await instance.tick()
            snapshot = instance.snapshot()
            assert snapshot["posts"][0]["rank"] == "normal"
            assert snapshot["posts"][0]["rank_status"] == status
            assert snapshot["posts"][0]["ranked_games"] == games
            assert snapshot["revision"] != previous_revision
            assert instance.pending[ident].event["id"] != previous_id
            previous_revision = snapshot["revision"]
            previous_id = instance.pending[ident].event["id"]
    finally:
        await instance.stop()


@pytest.mark.asyncio
async def test_failed_delivery_reuses_event_id(service, monkeypatch):
    instance, _, _ = service
    result = await instance.create(mod.IntegrationInput(name="test", webhook_url="https://receiver.example/hook"))
    ident = result["integration"]["id"]
    seen = []
    async def send(item, event):
        seen.append(event["id"])
        raise httpx.ConnectError("secret-url-must-not-appear")
    monkeypatch.setattr(mod, "deliver", send)
    await instance.tick()
    for _ in range(2):
        instance.pending[ident].next_at = 0
        await instance.tick()
        await asyncio.gather(*instance._jobs.values())
    assert len(seen) == 2 and seen[0] == seen[1]
    assert instance.get(ident).last_error == "connection or delivery failed"
    await instance.stop()


@pytest.mark.asyncio
async def test_slow_destination_does_not_block_another_and_disable_cancels(service, monkeypatch):
    instance, _, _ = service
    first = await instance.create(mod.IntegrationInput(name="slow", webhook_url="https://receiver.example/slow"))
    second = await instance.create(mod.IntegrationInput(name="fast", webhook_url="https://receiver.example/fast"))
    blocked = asyncio.Event()
    fast_finished = asyncio.Event()
    async def send(item, event):
        if item.name == "slow":
            blocked.set()
            await asyncio.Event().wait()
        fast_finished.set()
        return 204, 0
    monkeypatch.setattr(mod, "deliver", send)
    await instance.tick()
    for pending in instance.pending.values():
        pending.next_at = 0
    await instance.tick()
    await asyncio.wait_for(blocked.wait(), 1)
    await asyncio.wait_for(fast_finished.wait(), 1)
    slow_id = first["integration"]["id"]
    await instance.update(slow_id, mod.IntegrationPatch(enabled=False))
    await asyncio.gather(*instance._jobs.values(), return_exceptions=True)
    assert not instance.get(slow_id).enabled
    assert instance.pending[slow_id].event is None
    assert instance.get(second["integration"]["id"]).last_success_at is not None
    await instance.stop()


@pytest.mark.asyncio
async def test_latest_change_during_inflight_request_is_not_lost(service, monkeypatch):
    instance, posts, _ = service
    result = await instance.create(mod.IntegrationInput(name="test", webhook_url="https://receiver.example/hook"))
    ident = result["integration"]["id"]
    started = asyncio.Event()
    release = asyncio.Event()
    async def send(item, event):
        started.set()
        await release.wait()
        return 204, 0
    monkeypatch.setattr(mod, "deliver", send)
    await instance.tick()
    instance.pending[ident].next_at = 0
    await instance.tick()
    await started.wait()
    posts.clear()
    await instance.tick()
    newest = instance.pending[ident].event
    release.set()
    await asyncio.gather(*instance._jobs.values())
    assert instance.pending[ident].event == newest
    assert newest["data"]["count"] == 0
    await instance.stop()


@pytest.mark.asyncio
async def test_store_failure_does_not_claim_configuration_was_saved(service, monkeypatch):
    instance, _, _ = service
    def fail(items):
        raise OSError("private-path")
    monkeypatch.setattr(post_redis, "save_integrations", fail)
    with pytest.raises(HTTPException) as caught:
        await instance.create(mod.IntegrationInput(name="test"))
    assert caught.value.status_code == 503
    assert instance.items == {}


@pytest.mark.asyncio
async def test_corrupt_store_fails_closed_without_overwriting(service, monkeypatch):
    instance, _, saved = service
    monkeypatch.setattr(post_redis, "load_integrations", lambda: [{"corrupt": True}])
    await instance.start()
    assert instance.store_error and not instance.available and instance._task is None
    with pytest.raises(HTTPException):
        await instance.create(mod.IntegrationInput(name="replacement"))
    assert saved == []
    await instance.stop()


@pytest.mark.asyncio
async def test_restart_recovers_credentials_and_queues_latest_snapshot(service, monkeypatch):
    instance, posts, saved = service
    created = await instance.create(mod.IntegrationInput(name="test", webhook_url="https://receiver.example/hook"))
    stored = saved[-1]
    monkeypatch.setattr(post_redis, "load_integrations", lambda: stored)
    posts.clear()
    replacement = mod.IntegrationService(lambda: posts, "https://lobby.example")
    await replacement.start()
    try:
        await replacement.tick()
        ident = created["integration"]["id"]
        assert replacement.get(ident).api_key_hash == instance.get(ident).api_key_hash
        assert replacement.pending[ident].event["data"]["count"] == 0
    finally:
        await replacement.stop()


def test_local_persistence_roundtrip_permissions_and_corruption(tmp_path, monkeypatch):
    monkeypatch.setenv("ASOBBY_STORE", "local")
    monkeypatch.setenv("ASOBBY_STORE_DIR", str(tmp_path))
    monkeypatch.delenv("UPSTASH_REDIS_REST_URL", raising=False)
    monkeypatch.delenv("UPSTASH_REDIS_REST_TOKEN", raising=False)
    assert post_redis.load_integrations() == []
    records = [{"id": "test", "signing_secret": "test-secret"}]
    post_redis.save_integrations(records)
    path = tmp_path / "integrations.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert post_redis.load_integrations() == records
    post_redis.save_integrations([])
    assert post_redis.load_integrations() == []
    path.write_text('{"invalid":"shape"}')
    with pytest.raises(ValueError):
        post_redis.load_integrations()
    assert path.read_text() == '{"invalid":"shape"}'


def test_expired_lobby_records_are_excluded(monkeypatch):
    import main
    now = time.time()
    live = main.PostRecord(post=main.Post(id="live", net_status=3, updated_at=now), owner_token="x", creator_ip="x")
    stale = main.PostRecord(post=main.Post(id="stale", updated_at=now - 100), owner_token="x", creator_ip="x")
    monkeypatch.setattr(main, "RECORDS", {"live": live, "stale": stale})
    assert [post["id"] for post in main.integration_posts()] == ["live"]
    live.post.updated_at = now - 100
    assert main.integration_posts() == []


@pytest.mark.asyncio
async def test_cancelled_admin_request_finishes_atomic_save_before_unlock(service, monkeypatch):
    instance, _, _ = service
    started = threading.Event()
    release = threading.Event()
    saved = []

    def save(items):
        started.set()
        assert release.wait(timeout=3)
        saved.append(items)

    monkeypatch.setattr(post_redis, "save_integrations", save)
    task = asyncio.create_task(instance.create(mod.IntegrationInput(name="cancelled", webhook_url="https://receiver.example/hook")))
    try:
        assert await asyncio.to_thread(started.wait, 2)
        task.cancel()
        await asyncio.sleep(0)
        assert instance._lock.locked()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert len(saved) == 1
        ident = saved[0][0]["id"]
        assert instance.get(ident).name == "cancelled"
        assert instance.pending[ident].event is not None
        assert not instance._lock.locked()
    finally:
        release.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_creation_queue_survives_changes_retries_and_concurrent_creation(service, monkeypatch):
    instance, posts, _ = service
    cred = await instance.create(mod.IntegrationInput(name="hook", webhook_url="https://receiver.example/hook"))
    ident = cred["integration"]["id"]
    await instance.tick()
    pending = instance.pending[ident]
    assert not pending.created  # Existing snapshots never count as new listings.
    instance.post_created("new1")
    first = pending.created[0]
    assert first["type"] == "post.created" and first["data"]["post_id"] == "new1"
    assert set(first["data"]) == {"post_id", "count", "snapshot_url"}
    calls = []
    async def fail(item, event):
        calls.append(event)
        return 503, 120
    monkeypatch.setattr(mod, "deliver", fail)
    pending.next_at = 0
    await instance.tick()
    await asyncio.gather(*instance._jobs.values())
    posts[0]["net_status"] = 4
    instance.queue_test(ident)
    instance.post_created("new2")
    await instance.tick()
    assert pending.created[0] == first and pending.next_at > time.monotonic() + 110
    assert len(calls) == 1

    entered, release = asyncio.Event(), asyncio.Event()
    async def slow(item, event):
        calls.append(event)
        entered.set()
        await release.wait()
        return 204, 0
    monkeypatch.setattr(mod, "deliver", slow)
    pending.next_at = 0
    await instance.tick()
    await entered.wait()
    instance.post_created("new3")
    posts.clear()  # Closing a host does not erase its creation notification.
    await instance.tick()
    release.set()
    await asyncio.gather(*instance._jobs.values())
    assert calls[0]["id"] == calls[1]["id"] == first["id"]
    assert [event["data"]["post_id"] for event in pending.created] == ["new2", "new3"]
    assert pending.next_event()["type"] == "lobby.changed"  # Fairness.
    for _ in range(3):
        pending.next_at = 0
        await instance.tick()
        await asyncio.gather(*instance._jobs.values())
    assert [event["type"] for event in calls[2:]] == ["lobby.changed", "post.created", "post.created"]
    assert not pending.created and pending.event is None
    await instance.stop()


@pytest.mark.asyncio
async def test_creation_queue_bounds_scope_and_config_reset(service, monkeypatch):
    instance, _, _ = service
    hook = await instance.create(mod.IntegrationInput(name="hook", webhook_url="https://receiver.example/hook"))
    api = await instance.create(mod.IntegrationInput(name="api"))
    disabled = await instance.create(mod.IntegrationInput(name="off", webhook_url="https://receiver.example/hook", enabled=False))
    ident = hook["integration"]["id"]
    monkeypatch.setattr(mod, "MAX_PENDING_CREATED", 2)
    for post_id in ["first", "second", "third"]:
        instance.post_created(post_id)
    pending = instance.pending[ident]
    assert [event["data"]["post_id"] for event in pending.created] == ["first", "third"]
    for cred in (api, disabled):
        assert not instance.pending[cred["integration"]["id"]].created
    await instance.rotate(ident)
    assert not instance.pending[ident].created
    assert instance.pending[ident].event["type"] == "lobby.changed"


@pytest.mark.asyncio
async def test_native_author_and_creation_hook_are_not_called_for_updates(service, monkeypatch):
    import main
    from starlette.requests import Request
    instance, _, _ = service
    records = {}
    monkeypatch.setattr(main, "RECORDS", records)
    monkeypatch.setattr(main, "INTEGRATIONS", instance)
    monkeypatch.setattr(main, "LAST_CREATE_AT", {})
    instance.read_posts = main.integration_posts
    async def session(request):
        return {"id": "123456789012345678", "name": "host", "avatar": ""}
    async def noop(*args, **kwargs):
        pass
    async def reachable(*args, **kwargs):
        return True, False, False
    async def rank(*args):
        return {}
    monkeypatch.setattr(main, "resolve_session", session)
    monkeypatch.setattr(main, "enforce_min_client_version", noop)
    monkeypatch.setattr(main, "verify_hostable_for_create", reachable)
    monkeypatch.setattr(main, "host_rank_for_post", rank)
    monkeypatch.setattr(main, "_persist_record", noop)
    monkeypatch.setattr(main.HUB, "publish", noop)
    monkeypatch.setattr(main.geoip, "apply_country_from_addr", lambda *args, **kwargs: None)
    cred = await instance.create(mod.IntegrationInput(name="hook", webhook_url="https://receiver.example/hook"))
    request = Request({"type": "http", "method": "POST", "path": "/posts", "headers": [], "client": ("93.184.216.34", 1234)})
    result = await main.create_post(main.CreatePostIn(addr="93.184.216.34:10800", net_status=3), request)
    pending = instance.pending[cred["integration"]["id"]]
    assert len(pending.created) == 1 and pending.created[0]["data"]["post_id"] == result["post"]["id"]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_for(instance)), base_url="https://test") as client:
        response = await client.get("/api/v1/lobby", headers={"Authorization": "Bearer " + cred["api_key"]})
    post = response.json()["posts"][0]
    assert post["discord_user_id"] == "123456789012345678" and post["discord_user_id_source"] == "oauth"
    assert not {"owner_token", "owner_user_id", "guest_user_id", "external_user_id", "addr"} & post.keys()
    restored = main.post_record_from_dict(main.post_record_to_dict(records[post["id"]]))
    assert mod.author_fields(restored) == {"discord_user_id": "123456789012345678", "discord_user_id_source": "oauth"}
    for state in (4, 3):
        records[post["id"]].post.net_status = state
        await instance.tick()
    assert len(pending.created) == 1
