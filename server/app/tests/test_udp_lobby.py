"""Registration permissions, liveness transitions and durable restart behavior."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import threading
import time
from dataclasses import asdict

import httpx
import pytest
from fastapi import FastAPI, HTTPException

import integrations
import main
import post_redis
import udp_lobby as mod
from udp_probe import Result


def body(**changes):
    return mod.Registration(**{
        "request_id": "message-1", "external_user_id": "discord-user-1",
        "owner_name": "host", "addr": "93.184.216.34:10800", "comment": "対戦募集",
        **changes,
    })


@pytest.fixture
def service(monkeypatch):
    records, saved, events = {}, {}, []
    monkeypatch.setattr(main, "RECORDS", records)
    instance = integrations.IntegrationService(main.integration_posts, "https://asobby.test")
    def save(rec):
        saved[rec.post.id] = copy.deepcopy(main.post_record_to_dict(rec))
    async def publish(kind, data):
        events.append((kind, data))
    service = mod.Service(
        records=records, integrations=instance, make_record=main.make_udp_record,
        save=save, delete=lambda ident: saved.pop(ident, None), publish=publish,
        probe=lambda *a, **kw: Result(alive=True, state="waiting", direct=True),
        enabled=lambda: True, stream_allowed=main.is_allowed_stream_url,
    )
    return service, saved, events


async def credential(service, **changes):
    return await service.integrations.create(integrations.IntegrationInput(name="test", allow_posting=True, **changes))


def app_for(service):
    app = FastAPI()
    app.include_router(mod.build_router(service))
    async def session(request):
        return {"id": "admin"} if request.headers.get("X-Test-Admin") else None
    app.include_router(integrations.build_router(service.integrations, session, lambda uid: uid == "admin"))
    return app


def due(service):
    for rec in service.records.values():
        rec.monitor.next_check_at = 0


@pytest.mark.asyncio
async def test_api_scope_pending_hidden_until_checked_and_casual_publication(service):
    s, saved, events = service
    readonly = await s.integrations.create(integrations.IntegrationInput(name="read"))
    cred = await credential(s)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_for(s)), base_url="https://asobby.test") as client:
        url = "/api/v1/posts"
        assert (await client.post(url, json=body().model_dump())).status_code == 401
        assert (await client.post(url, json=body().model_dump(), headers={"Authorization": "Bearer " + readonly["api_key"]})).status_code == 403
        headers = {"Authorization": "Bearer " + cred["api_key"]}
        response = await client.post(url, json=body().model_dump(), headers=headers)
        assert response.status_code == 202 and response.headers["cache-control"] == "no-store"
        ident = response.json()["id"]
        assert response.json()["state"] == "checking" and response.json()["post"] is None
        assert response.json()["external_user_id"] == body().external_user_id
        assert ident in saved and not events
        assert main.sorted_public_posts() == [] and main.integration_posts() == []
        assert not main.user_has_other_posts("viewer")
        await s.tick()
        response = await client.get(url + "/" + ident, headers=headers)
        assert response.json()["state"] == "active"
        post = response.json()["post"]
        assert post["post_type"] == "casual" and post["rank"] == ""
        assert post["rank_status"] == "unknown" and post["ranked_games"] is None
        assert post["discord_user_id"] is None and post["discord_user_id_source"] is None
        assert not post["supports_messages"] and not post["ping_warn_enabled"]
        assert not {"owner_token", "monitor", "external_user_id", "integration_id"} & post.keys()
        assert s.records[ident].owner_user_id == "" and s.records[ident].creator_ip == ""
        assert events[-1][0] == "upsert" and len(main.sorted_public_posts()) == 1
        snapshot = (await client.get("/api/v1/lobby", headers=headers)).json()
        assert snapshot["count"] == 1 and snapshot["posts"][0]["status"] == "waiting"
        assert "addr" not in snapshot["posts"][0]
        assert snapshot["posts"][0]["discord_user_id"] is None
        assert (await client.delete(url + "/" + ident, headers=headers)).status_code == 405
        assert (await client.patch(url + "/" + ident, headers=headers, json={})).status_code == 405


@pytest.mark.asyncio
async def test_duplicate_retry_and_same_request_different_content(service):
    s, _, _ = service
    cred = await credential(s)
    item = s.integrations.get(cred["integration"]["id"])
    first, second = await asyncio.gather(s.register(item, body()), s.register(item, body()))
    assert first["id"] == second["id"] and len(s.records) == 1
    with pytest.raises(HTTPException) as error:
        await s.register(item, body(comment="different"))
    assert error.value.status_code == 409
    with pytest.raises(HTTPException) as error:
        await s.register(item, body(request_id="new", external_user_id="other"))
    assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_discord_attribution_roundtrip_and_only_first_publication_emits_created(service):
    s, saved, _ = service
    cred = await credential(s)
    ident = cred["integration"]["id"]
    # Queue only; never resolve or contact a real webhook destination.
    s.integrations.items[ident] = s.integrations.get(ident).model_copy(update={"webhook_url": "https://receiver.example/hook"})
    request = body(discord_user_id="123456789012345678")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_for(s)), base_url="https://asobby.test") as client:
        headers = {"Authorization": "Bearer " + cred["api_key"]}
        first = (await client.post("/api/v1/posts", json=request.model_dump(), headers=headers)).json()
        post_id = first["id"]
        assert not s.integrations.pending[ident].created
        await s.tick()
        active = (await client.get("/api/v1/posts/" + post_id, headers=headers)).json()
        snapshot = (await client.get("/api/v1/lobby", headers=headers)).json()["posts"][0]
        assert active["external_user_id"] == request.external_user_id
        for post in (active["post"], snapshot):
            assert post["discord_user_id"] == request.discord_user_id
            assert post["discord_user_id_source"] == "integration"
            assert post["rank"] == "" and post["rank_status"] == "unknown"
            assert "external_user_id" not in post and "owner_user_id" not in post
        retried = (await client.post("/api/v1/posts", json=request.model_dump(), headers=headers)).json()
        assert retried == active
        assert (await client.post("/api/v1/posts", json={**request.model_dump(), "discord_user_id": "987654321098765432"}, headers=headers)).status_code == 409
    pending = s.integrations.pending[ident]
    assert len(pending.created) == 1 and pending.created[0]["data"]["post_id"] == post_id
    restored = main.post_record_from_dict(saved[post_id])
    assert restored.owner_user_id == ""  # Attribution never becomes account authority.
    assert integrations.author_fields(restored) == {
        "discord_user_id": request.discord_user_id, "discord_user_id_source": "integration",
    }
    s.records[post_id] = restored
    for result in (Result(alive=True, direct=True, state="connecting"), Result(), Result(alive=True, direct=True, state="waiting")):
        s.probe = lambda *a, **kw: result
        due(s)
        await s.tick()
    assert len(pending.created) == 1  # Restoration/updates/recovery are not new.
    pending.created.clear()
    s.integrations._revision = None
    await s.integrations.tick()
    assert not pending.created  # A restart snapshot cannot synthesize creation.


@pytest.mark.parametrize("value", [123456789012345678, "", "0", "0123", "abc", " 123", "123 ", "１２３", "18446744073709551616", "1" * 21])
def test_discord_id_requires_unsigned_snowflake_string(value):
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        body(discord_user_id=value)


@pytest.mark.asyncio
async def test_legacy_retry_fingerprint_and_numeric_external_id_do_not_infer_discord(service):
    s, saved, _ = service
    cred = await credential(s)
    item = s.integrations.get(cred["integration"]["id"])
    request = body(external_user_id="123456789012345678")
    reg = await s.register(item, request)
    rec = s.records[reg["id"]]
    old_body = request.model_dump(exclude={"discord_user_id"})
    assert rec.monitor.fingerprint == hashlib.sha256(integrations.encode_json(old_body)).hexdigest()
    legacy = copy.deepcopy(saved[reg["id"]])
    legacy["monitor"].pop("discord_user_id")
    s.records[reg["id"]] = main.post_record_from_dict(legacy)
    assert (await s.register(item, request))["id"] == reg["id"]
    await s.tick()
    assert s.status(s.records[reg["id"]])["post"]["discord_user_id"] is None


@pytest.mark.asyncio
async def test_foreign_status_is_hidden_and_no_identity_forging_fields(service):
    s, _, _ = service
    cred, other = await credential(s), await credential(s)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_for(s)), base_url="https://asobby.test") as client:
        headers = {"Authorization": "Bearer " + cred["api_key"]}
        for extra in [
            {"post_type": "ranked"}, {"owner_user_id": "victim"}, {"net_status": 4},
            {"giuroll": True}, {"autopunch": True}, {"rank_status": "ranked"}, {"ranked_games": 999},
        ]:
            assert (await client.post("/api/v1/posts", json={**body().model_dump(), **extra}, headers=headers)).status_code == 422
        response = await client.post("/api/v1/posts", json=body().model_dump(), headers=headers)
        ident = response.json()["id"]
        foreign = {"Authorization": "Bearer " + other["api_key"]}
        assert (await client.get("/api/v1/posts/" + ident, headers=foreign)).status_code == 404


@pytest.mark.asyncio
async def test_enabled_permission_defaults_and_revocation(service):
    s, saved, events = service
    assert integrations.IntegrationInput(name="old").allow_posting is False
    cred = await credential(s)
    item = s.integrations.get(cred["integration"]["id"])
    reg = await s.register(item, body())
    await s.tick()
    # Key rotation is not a reason to destroy existing registrations.
    await s.integrations.rotate(item.id)
    due(s)
    await s.tick()
    assert reg["id"] in s.records
    await s.integrations.update(item.id, integrations.IntegrationPatch(allow_posting=False))
    await s.tick()
    assert not saved and not s.records and events[-1][1]["reason"] == "integration_disabled"


@pytest.mark.asyncio
async def test_giuroll_busy_return_to_waiting_and_no_heartbeat_events(service):
    s, _, events = service
    cred = await credential(s)
    reg = await s.register(s.integrations.get(cred["integration"]["id"]), body())
    s.probe = lambda *a, **kw: Result(alive=True, state="waiting", giuroll=True, autopunch=True)
    await s.tick()
    revision = s.integrations.snapshot()["revision"]
    due(s)
    await s.tick()
    assert len(events) == 1 and revision == s.integrations.snapshot()["revision"]
    for state, expected, connected in [("connecting", 2, True), ("unknown", 0, False), ("waiting", 3, False)]:
        def probe(*a, **kwargs):
            assert kwargs["prefer_autopunch"]
            assert kwargs["detect_giuroll"]  # known Giuroll pong is also liveness
            return Result(alive=True, state=state, autopunch=True)
        s.probe = probe
        due(s)
        await s.tick()
        post = s.records[reg["id"]].post
        assert post.net_status == expected and post.guest_connected == connected
        assert post.giuroll  # no response is not evidence of absence
    assert len(events) == 4


@pytest.mark.asyncio
async def test_three_consecutive_misses_not_age_end_listing(service, monkeypatch):
    s, saved, events = service
    cred = await credential(s)
    reg = await s.register(s.integrations.get(cred["integration"]["id"]), body())
    await s.tick()
    rec = s.records[reg["id"]]
    rec.post.updated_at = time.time() - 365 * 86400
    assert main.post_record_ttl(rec) == float("inf")
    assert main.post_record_redis_ttl(rec) is None
    assert len(main.integration_posts()) == 1
    s.probe = lambda *a, **kw: Result()
    for i in range(3):
        due(s)
        if i:
            s.records[reg["id"]].monitor.first_failure_at -= 16
        await s.tick()
        if i < 2:
            assert reg["id"] in s.records
    assert reg["id"] not in s.records and not saved
    assert events[-1][1]["reason"] == "host_unreachable"


@pytest.mark.asyncio
async def test_confirmed_failure_rechecks_each_second_and_closes_after_two_seconds(service, monkeypatch):
    s, saved, _ = service
    cred = await credential(s)
    reg = await s.register(s.integrations.get(cred["integration"]["id"]), body())
    await s.tick()
    clock = [time.time()]
    monkeypatch.setattr(mod.time, "time", lambda: clock[0])
    due(s)
    calls = []
    def fail(*args, **kwargs):
        calls.append(clock[0])
        return Result()
    s.probe = fail
    assert mod.FAILURE_GRACE == 2 and mod.FAILURES_TO_CLOSE == 3
    await s.tick()
    monitor = s.records[reg["id"]].monitor
    assert monitor.failures == 1 and monitor.next_check_at == clock[0] + 1
    await s.tick()
    assert len(calls) == 1  # Cannot busy-loop retries.
    clock[0] += 1
    await s.tick()
    assert s.records[reg["id"]].monitor.failures == 2
    clock[0] += 1
    await s.tick()
    assert len(calls) == 3 and reg["id"] not in s.records and reg["id"] not in saved


@pytest.mark.asyncio
@pytest.mark.parametrize("result", [Result(alive=True, direct=True), Result(inconclusive=True)])
async def test_recovery_or_inconclusive_result_restores_normal_check_interval(service, monkeypatch, result):
    s, _, _ = service
    cred = await credential(s)
    reg = await s.register(s.integrations.get(cred["integration"]["id"]), body())
    await s.tick()
    clock = [time.time()]
    monkeypatch.setattr(mod.time, "time", lambda: clock[0])
    due(s)
    s.probe = lambda *a, **kw: Result()
    await s.tick()
    clock[0] += 1
    s.probe = lambda *a, **kw: result
    await s.tick()
    monitor = s.records[reg["id"]].monitor
    assert monitor.failures == 0 and monitor.first_failure_at == 0
    assert monitor.next_check_at == clock[0] + mod.CHECK_INTERVAL


@pytest.mark.asyncio
async def test_outage_and_recovery_reset_streak(service):
    s, _, _ = service
    cred = await credential(s)
    reg = await s.register(s.integrations.get(cred["integration"]["id"]), body())
    await s.tick()
    for result, failures in [(Result(), 1), (Result(inconclusive=True), 0), (Result(), 1), (Result(alive=True, direct=True), 0)]:
        s.probe = lambda *a, **kw: result
        due(s)
        await s.tick()
        rec = s.records[reg["id"]]
        assert rec.monitor.failures == failures
    assert not rec.post.reachability_lost


@pytest.mark.asyncio
async def test_storage_error_never_reports_acceptance_or_removes_undurably(service):
    s, saved, _ = service
    cred = await credential(s)
    item = s.integrations.get(cred["integration"]["id"])
    save = s.save
    def fail(*args):
        raise OSError("disk unavailable")
    s.save = fail
    with pytest.raises(HTTPException) as error:
        await s.register(item, body())
    assert error.value.status_code == 503 and not s.records
    s.save = save
    reg = await s.register(item, body())
    s.delete = fail
    await s.integrations.delete(item.id)
    with pytest.raises(HTTPException):
        await s.tick()
    assert reg["id"] in s.records and reg["id"] in saved


@pytest.mark.asyncio
async def test_pending_never_emits_upsert_on_failure_and_can_register_again(service, monkeypatch):
    s, _, events = service
    monkeypatch.setattr(mod, "FAILURE_GRACE", 0)
    cred = await credential(s)
    item = s.integrations.get(cred["integration"]["id"])
    await s.register(item, body())
    s.probe = lambda *a, **kw: Result()
    for _ in range(3):
        due(s)
        await s.tick()
    assert not s.records and not events
    assert (await s.register(item, body()))["state"] == "checking"


@pytest.mark.asyncio
async def test_admission_limits_disabled_verification_and_stream_validation(service, monkeypatch):
    s, _, _ = service
    cred = await credential(s)
    item = s.integrations.get(cred["integration"]["id"])
    with pytest.raises(HTTPException) as error:
        await s.register(item, body(stream_url="https://evil.example"))
    assert error.value.status_code == 422
    s.enabled = lambda: False
    with pytest.raises(HTTPException) as error:
        await s.register(item, body())
    assert error.value.status_code == 503
    s.enabled = lambda: True
    await s.register(item, body())
    with pytest.raises(HTTPException) as error:
        await s.register(item, body(request_id="two", addr="1.1.1.1:10800"))
    assert error.value.status_code == 429
    monkeypatch.setattr(mod, "MAX_LISTINGS", 1)
    with pytest.raises(HTTPException) as error:
        await s.register(item, body(request_id="two", external_user_id="two", addr="1.1.1.1:10800"))
    assert error.value.status_code == 429


@pytest.mark.asyncio
async def test_restart_ignores_old_heartbeat_age_and_resumes_both_waiting_and_busy(service, monkeypatch):
    s, saved, _ = service
    cred = await credential(s)
    reg = await s.register(s.integrations.get(cred["integration"]["id"]), body())
    await s.tick()
    data = saved[reg["id"]]
    data["post"]["updated_at"] = 1
    data["post"]["net_status"] = main.NET_CHECKING
    data["monitor"]["failures"] = 100
    monkeypatch.setattr(post_redis, "is_configured", lambda: True)
    monkeypatch.setattr(post_redis, "load_all_record_dicts", lambda: [data])
    s.records.clear()
    await main._hydrate_records_from_redis()
    assert reg["id"] in s.records and not main.probe_target_records()
    s.enabled = lambda: False
    await s.start()
    assert s.records[reg["id"]].monitor.failures == 0
    await s.stop()
    s.enabled = lambda: True
    await s.tick()
    assert s.records[reg["id"]].post.net_status == 3


@pytest.mark.asyncio
async def test_unavailable_integration_store_does_not_remove_restored_posts(service):
    s, _, _ = service
    cred = await credential(s)
    reg = await s.register(s.integrations.get(cred["integration"]["id"]), body())
    s.integrations.items.clear()
    s.integrations.available = False
    await s.tick()
    assert reg["id"] in s.records


@pytest.mark.asyncio
async def test_revoke_during_probe_does_not_publish(service):
    s, saved, events = service
    cred = await credential(s)
    item = s.integrations.get(cred["integration"]["id"])
    await s.register(item, body())
    started, finish = threading.Event(), threading.Event()
    def probe(*a, **kw):
        started.set()
        assert finish.wait(3)
        return Result(alive=True, direct=True)
    s.probe = probe
    task = asyncio.create_task(s.tick())
    try:
        await asyncio.to_thread(started.wait, 2)
        await s.integrations.delete(item.id)
    finally:
        finish.set()
        await task
    assert not saved and not s.records and not events


@pytest.mark.asyncio
async def test_cancelled_registration_finishes_durable_write_before_retry(service):
    s, saved, _ = service
    cred = await credential(s)
    item = s.integrations.get(cred["integration"]["id"])
    started, finish = threading.Event(), threading.Event()
    save = s.save
    def delayed(rec):
        started.set()
        assert finish.wait(3)
        save(rec)
    s.save = delayed
    task = asyncio.create_task(s.register(item, body()))
    try:
        assert await asyncio.to_thread(started.wait, 2)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    retried = await s.register(item, body())
    assert len(saved) == len(s.records) == 1 and retried["id"] in saved


def test_monitor_roundtrip_and_normal_post_defaults():
    monitor = mod.Monitor("integration", "external-user", "request", "fingerprint", published=True)
    rec = main.make_udp_record(body(), monitor)
    restored = main.post_record_from_dict(main.post_record_to_dict(rec))
    assert asdict(restored.monitor) == asdict(monitor)
    assert restored.post.supports_messages is False
    forged = main.post_record_to_dict(rec)
    forged["post"].update(rank_status="ranked", ranked_games=500)
    sanitized = main.post_record_from_dict(forged)
    assert sanitized.post.rank_status == "unknown" and sanitized.post.ranked_games is None
    normal = main.post_record_from_dict({"post": {"id": "normal"}})
    assert normal.monitor is None and normal.post.supports_messages
    assert main.post_record_ttl(normal) == 20


def test_redis_udp_records_have_no_ttl(monkeypatch):
    calls = []
    class Redis:
        def set(self, *args, **kw):
            calls.append((args, kw))
        def sadd(self, *args):
            pass
    monkeypatch.setattr(post_redis, "is_redis_configured", lambda: True)
    monkeypatch.setattr(post_redis, "_client", Redis)
    post_redis.save_record_dict({"post": {"id": "udp"}}, ttl_sec=None)
    post_redis.save_record_dict({"post": {"id": "native"}}, ttl_sec=50)
    assert calls[0][1] == {} and calls[1][1] == {"ex": 50}


@pytest.mark.asyncio
async def test_native_registration_cannot_race_durable_reservation(service, monkeypatch):
    s, _, _ = service
    cred = await credential(s)
    item = s.integrations.get(cred["integration"]["id"])
    started, finish = threading.Event(), threading.Event()
    save = s.save
    def delayed(rec):
        started.set()
        assert finish.wait(3)
        save(rec)
    s.save = delayed
    async def session(request):
        return {"id": "native-user", "name": "native", "avatar": ""}
    monkeypatch.setattr(main, "resolve_session", session)
    monkeypatch.setattr(main, "MIN_CLIENT_VERSION", "")
    monkeypatch.setattr(main, "LAST_CREATE_AT", {})
    registration = asyncio.create_task(s.register(item, body()))
    try:
        assert await asyncio.to_thread(started.wait, 2)
        assert not main.sorted_public_posts()
        from starlette.requests import Request
        request = Request({"type": "http", "headers": [], "client": ("1.1.1.1", 1234)})
        with pytest.raises(HTTPException) as error:
            await main.create_post(main.CreatePostIn(addr="93.184.216.34:010800"), request)
        assert error.value.status_code == 409
    finally:
        finish.set()
        await registration
    assert len(s.records) == 1


@pytest.mark.asyncio
async def test_registration_conflicts_with_noncanonical_native_endpoint(service):
    s, _, _ = service
    cred = await credential(s)
    rec = main.PostRecord(
        post=main.Post(addr="93.184.216.34:010800"), owner_token="native", creator_ip="",
    )
    s.records[rec.post.id] = rec
    with pytest.raises(HTTPException) as error:
        await s.register(s.integrations.get(cred["integration"]["id"]), body())
    assert error.value.status_code == 409 and len(s.records) == 1


@pytest.mark.asyncio
async def test_legacy_message_route_rejects_unreceivable_messages(service, monkeypatch):
    s, _, _ = service
    cred = await credential(s)
    reg = await s.register(s.integrations.get(cred["integration"]["id"]), body())
    await s.tick()
    async def session(request):
        return {"id": "viewer"}
    monkeypatch.setattr(main, "resolve_session", session)
    with pytest.raises(HTTPException) as error:
        await main.post_message(reg["id"], main.PostMessageIn(type="giuroll_request"), None)
    assert error.value.status_code == 409
    assert not s.records[reg["id"]].pending_messages


@pytest.mark.asyncio
async def test_registration_rate_and_integration_cap(service, monkeypatch):
    s, _, _ = service
    cred = await credential(s)
    item = s.integrations.get(cred["integration"]["id"])
    await s.register(item, body())
    next_body = body(addr="1.1.1.1:10800", request_id="two", external_user_id="two")
    monkeypatch.setattr(mod, "MAX_PER_INTEGRATION", 1)
    with pytest.raises(HTTPException) as error:
        await s.register(item, next_body)
    assert error.value.status_code == 429
    monkeypatch.setattr(mod, "MAX_PER_INTEGRATION", 10)
    monkeypatch.setattr(mod, "CREATES_PER_MINUTE", 1)
    with pytest.raises(HTTPException) as error:
        await s.register(item, next_body)
    assert error.value.status_code == 429 and error.value.headers["Retry-After"] == "60"


@pytest.mark.asyncio
async def test_monitor_round_robin_and_shutdown_waits_for_inflight_socket(service):
    s, _, _ = service
    cred = await credential(s)
    item = s.integrations.get(cred["integration"]["id"])
    await s.register(item, body())
    await s.register(item, body(addr="1.1.1.1:10800", request_id="two", external_user_id="two"))
    checked = []
    def probe(addr, **kw):
        checked.append(addr)
        return Result(alive=True, state="waiting", direct=True)
    s.probe = probe
    await s.tick()
    await s.tick()
    await s.tick()
    assert len(checked) == 2 and len(set(checked)) == 2
    started, finish = threading.Event(), threading.Event()
    def slow_probe(*a, **kw):
        started.set()
        assert finish.wait(3)
        return Result(alive=True, direct=True)
    s.probe = slow_probe
    await s.start()
    stopping = None
    try:
        assert await asyncio.to_thread(started.wait, 2)
        stopping = asyncio.create_task(s.stop())
        await asyncio.sleep(0)
        assert not stopping.done()
    finally:
        finish.set()
        if stopping:
            await stopping
        else:
            await s.stop()
    assert s._probe_task is None and s._task is None
