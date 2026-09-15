"""Authenticated registration and server-owned liveness of casual listings.

The adapter supplies ordinary lobby records; no separate public listing type,
user account, heartbeat API, close API or publication deadline is introduced.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import time
from collections import deque
from dataclasses import asdict, dataclass
from typing import Callable

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

import integrations
from udp_probe import Result, public_address, same_address

CHECK_INTERVAL = 15.0
MIN_TICK = 1.0
GIUROLL_CHECK_INTERVAL = 60.0
FAILURES_TO_CLOSE = 3
FAILURE_GRACE = 2.0  # consecutive silence, NOT a publication lifetime
FAILURE_RECHECK_INTERVAL = 1.0
MAX_LISTINGS = 50
MAX_PER_INTEGRATION = 10
MAX_PER_USER = 1
CREATES_PER_MINUTE = 6
ADMISSION_TIMEOUT = 3.0


def admission_error(status, code, message):
    return HTTPException(status, {"code": code, "message": message}, headers={"Cache-Control": "no-store"})


def apply_alive(rec, result, now):
    monitor, post = rec.monitor, rec.post
    monitor.published = True
    monitor.last_success_at = now
    monitor.failures = 0
    monitor.first_failure_at = 0
    post.giuroll = post.giuroll or result.giuroll
    post.autopunch = result.autopunch
    post.direct_reachable = result.direct
    post.reachability_uncertain = result.autopunch and not result.direct
    post.reachability_lost = False
    post.net_status = {"waiting": 3, "connecting": 2}.get(result.state, 0)
    # A Giuroll pong proves liveness, not a free player slot.
    post.guest_connected = result.state == "connecting"


class Registration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=1, max_length=128)
    external_user_id: str = Field(min_length=1, max_length=128)
    owner_name: str = Field(min_length=1, max_length=80)
    addr: str = Field(max_length=64)
    comment: str = Field(default="", max_length=200)
    stream_url: str = Field(default="", max_length=300)
    discord_user_id: str | None = Field(default=None, min_length=1, max_length=20, pattern=r"^[1-9][0-9]{0,19}$")

    @field_validator("discord_user_id")
    @classmethod
    def discord_snowflake(cls, value):
        if value is not None and int(value) > 2**64 - 1:
            raise ValueError("discord_user_id must be a positive unsigned 64-bit ID string")
        return value

    @field_validator("request_id", "external_user_id", "owner_name")
    @classmethod
    def nonblank(cls, value):
        value = value.strip()
        if not value or any(ord(c) < 32 for c in value):
            raise ValueError("a nonempty value without control characters is required")
        return value

    @field_validator("addr")
    @classmethod
    def public_host(cls, value):
        host, port = public_address(value)
        return f"{host}:{port}"


@dataclass
class Monitor:
    integration_id: str
    external_user_id: str
    request_id: str
    fingerprint: str
    published: bool = False
    last_success_at: float = 0
    last_check_at: float = 0
    last_giuroll_check_at: float = 0
    next_check_at: float = 0
    failures: int = 0
    first_failure_at: float = 0
    discord_user_id: str | None = None


class Service:
    def __init__(
        self, *, records, integrations: integrations.IntegrationService,
        make_record: Callable, save: Callable, delete: Callable,
        publish: Callable, probe: Callable, enabled: Callable, stream_allowed: Callable,
    ):
        self.records = records
        self.integrations = integrations
        self.make_record = make_record
        self.save = save
        self.delete = delete
        self.publish = publish
        self.probe = probe
        self.enabled = enabled
        self.stream_allowed = stream_allowed
        self._lock = asyncio.Lock()
        self._creates: dict[str, deque] = {}
        self._task: asyncio.Task | None = None
        self._probe_task: asyncio.Task | None = None
        self._probe_gate = asyncio.Lock()
        self._admissions: dict[str, asyncio.Task] = {}
        self._checks: set[asyncio.Task] = set()

    def permitted(self, ident):
        item = self.integrations.items.get(ident)
        return bool(item and item.enabled and item.allow_posting)

    def authenticate(self, request):
        item = self.integrations.authenticate(request)
        if not item.allow_posting:
            raise HTTPException(403, "listing creation is not allowed for this integration")
        return item

    def status(self, rec):
        return {
            "id": rec.post.id,
            "state": "active" if rec.monitor.published else "checking",
            "status_url": self.integrations.base_url + "/api/v1/posts/" + rec.post.id,
            "external_user_id": rec.monitor.external_user_id,
            "post": {**asdict(rec.post), **integrations.author_fields(rec)} if rec.monitor.published else None,
        }

    async def _durable(self, operation, commit):
        # Hold the service lock through cancellation so a late thread write
        # cannot recreate a deleted listing or overwrite its newer state.
        task = asyncio.create_task(asyncio.to_thread(operation))
        cancelled = False
        try:
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                cancelled = True
                await task
        except Exception:
            raise HTTPException(503, "listing storage unavailable") from None
        commit()
        if cancelled:
            raise asyncio.CancelledError

    async def register(self, item, body: Registration):
        deadline = time.monotonic() + ADMISSION_TIMEOUT
        try:
            async with asyncio.timeout_at(deadline):
                async with self._lock:
                    rec, created = self._reserve(item, body)
                    if rec.monitor.published:
                        return self.status(rec), False
                    job = self._admissions.get(rec.post.id)
                    if job is None:
                        if not created:
                            # Pre-upgrade 202 registrations retain their original
                            # background lifecycle; do not silently resubmit them.
                            raise admission_error(409, "registration_in_progress", "a legacy registration is still being checked")
                        job = asyncio.create_task(self._admit(rec, item, deadline))
                        self._admissions[rec.post.id] = job
                        job.add_done_callback(lambda task: self._admission_done(rec, task))
        except TimeoutError:
            raise admission_error(503, "verification_busy", "host verification is busy; retry later") from None
        # Duplicate requests share the same attempt and deadline. A disconnected
        # caller cannot cancel another caller's attempt or interrupt a disk write.
        return await asyncio.shield(job), created

    def _admission_done(self, rec, task):
        ident = rec.post.id
        if self._admissions.get(ident) is task:
            self._admissions.pop(ident, None)
        # A task cancelled before its first step never enters _admit's finally.
        if self.records.get(ident) is rec:
            self.records.pop(ident, None)
        if not task.cancelled():
            task.exception()  # Observe failures even if every HTTP caller left.

    def _reserve(self, item, body):
        """Called under _lock. Reserve only in memory until verification succeeds."""
        if not self.enabled():
            raise HTTPException(503, "UDP host verification is disabled")
        if not self.integrations.available:
            raise HTTPException(503, "integration storage unavailable")
        if not self.stream_allowed(body.stream_url):
            raise HTTPException(422, "stream_url must be youtube, twitch, or niconico")
        # Omitting the new optional field must retain pre-upgrade retry hashes.
        fingerprint = hashlib.sha256(integrations.encode_json(body.model_dump(exclude_none=True))).hexdigest()
        if not self.permitted(item.id):
            raise HTTPException(403, "listing creation is not allowed for this integration")
        managed = [rec for rec in self.records.values() if rec.monitor is not None]
        owned = [rec for rec in managed if rec.monitor.integration_id == item.id]
        for rec in owned:
            if rec.monitor.request_id == body.request_id:
                if rec.monitor.fingerprint != fingerprint:
                    raise HTTPException(409, "request_id was already used with different data")
                return rec, False
        if any(same_address(rec.post.addr, body.addr) for rec in self.records.values()):
            raise HTTPException(409, "this host already has a listing")
        if (
            len(managed) >= MAX_LISTINGS or len(owned) >= MAX_PER_INTEGRATION
            or sum(rec.monitor.external_user_id == body.external_user_id for rec in owned) >= MAX_PER_USER
        ):
            raise HTTPException(429, "too many active registrations", headers={"Retry-After": "30"})
        now = time.time()
        self._creates = {key: values for key, values in self._creates.items() if key in self.integrations.items}
        recent = self._creates.setdefault(item.id, deque())
        while recent and recent[0] <= now - 60:
            recent.popleft()
        if len(recent) >= CREATES_PER_MINUTE:
            raise HTTPException(429, "too many registrations", headers={"Retry-After": "60"})
        rec = self.make_record(body, Monitor(
            integration_id=item.id, external_user_id=body.external_user_id,
            request_id=body.request_id, fingerprint=fingerprint,
            discord_user_id=body.discord_user_id,
        ))
        # Native-client registration also checks RECORDS for reserved endpoints.
        self.records[rec.post.id] = rec
        recent.append(now)  # Failed attempts must not bypass the UDP rate limit.
        return rec, True

    async def _check(self, addr, started=None, **kwargs):
        # Serialize before allocating a thread. A timed-out HTTP request must
        # not release the socket gate until its underlying worker actually exits.
        async with self._probe_gate:
            if started is not None:
                started.set()
            task = asyncio.create_task(asyncio.to_thread(self.probe, addr, **kwargs))
            self._probe_task = task
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                while not task.done():
                    try:
                        await asyncio.shield(task)
                    except asyncio.CancelledError:
                        continue
                    except Exception:
                        break
                if not task.cancelled():
                    task.exception()
                raise
            except Exception:
                return Result(inconclusive=True)
            finally:
                self._probe_task = None

    def _check_done(self, task):
        self._checks.discard(task)
        if not task.cancelled():
            task.exception()

    async def _admit(self, rec, item, deadline):
        job = None
        started = asyncio.Event()
        try:
            job = asyncio.create_task(self._check(
                rec.post.addr, started=started, detect_giuroll=True, deadline=deadline,
            ))
            self._checks.add(job)
            job.add_done_callback(self._check_done)
            try:
                async with asyncio.timeout_at(deadline):
                    result = await asyncio.shield(job)
            except TimeoutError:
                if not started.is_set():
                    raise admission_error(503, "verification_busy", "host verification is busy; retry later") from None
                raise admission_error(422, "verification_timeout", "host verification did not finish within 3 seconds") from None
            if result.timed_out or time.monotonic() >= deadline:
                raise admission_error(422, "verification_timeout", "host verification did not finish within 3 seconds")
            if result.inconclusive:
                raise admission_error(503, "verification_unavailable", "host verification is temporarily unavailable")
            if not result.alive:
                raise admission_error(422, "host_unconfirmed", "no valid host response was received")
            try:
                async with asyncio.timeout_at(deadline):
                    await self._lock.acquire()
            except TimeoutError:
                raise admission_error(503, "verification_busy", "registration is busy; retry later") from None
            try:
                current = self.integrations.items.get(item.id)
                if not self.integrations.available or not self.enabled():
                    raise admission_error(503, "verification_unavailable", "host verification is temporarily unavailable")
                if not self.permitted(item.id):
                    raise HTTPException(403, "listing creation is not allowed for this integration")
                if current.api_key_hash != item.api_key_hash:
                    raise HTTPException(401, "integration key changed")
                updated = copy.deepcopy(rec)
                now = time.time()
                apply_alive(updated, result, now)
                updated.post.updated_at = now
                updated.monitor.last_check_at = updated.monitor.last_giuroll_check_at = now
                updated.monitor.next_check_at = now + CHECK_INTERVAL

                def committed():
                    self.records[rec.post.id] = updated
                    self.integrations.post_created(rec.post.id)

                # The 3s verification budget is over. Finish the durable commit
                # before replying; never time out a write that could later publish.
                await self._durable(lambda: self.save(updated), committed)
                await self.publish("upsert", asdict(updated.post))
                return self.status(updated)
            finally:
                self._lock.release()
        finally:
            if job is not None and not job.done():
                job.cancel()  # _check drains the thread while retaining its gate.
            if self.records.get(rec.post.id) is rec:
                # No disk write occurs before host verification. A late worker
                # result cannot resurrect this discarded in-memory reservation.
                self.records.pop(rec.post.id, None)

    async def remove(self, rec, reason):
        await self._durable(
            lambda: self.delete(rec.post.id), lambda: self.records.pop(rec.post.id, None),
        )
        if rec.monitor.published:
            await self.publish("close", {"id": rec.post.id, "reason": reason, "ts": time.time()})

    async def tick(self):
        if not self.integrations.available:
            return  # broken integration storage is NOT an instruction to delete
        async with self._lock:
            records = [rec for rec in self.records.values()
                       if rec.monitor is not None and rec.post.id not in self._admissions]
            for rec in records:
                if not self.permitted(rec.monitor.integration_id):
                    await self.remove(rec, "integration_disabled")
            if not self.enabled():
                return
            records = [rec for rec in records if self.records.get(rec.post.id) is rec]
            if not records:
                return
            rec = min(records, key=lambda rec: (rec.monitor.next_check_at, rec.post.id))
            now = time.time()
            if rec.monitor.next_check_at > now:
                return
            detect_giuroll = rec.post.giuroll or now - rec.monitor.last_giuroll_check_at >= GIUROLL_CHECK_INTERVAL
        result = await self._check(
            rec.post.addr,
            prefer_autopunch=rec.post.autopunch and not rec.post.direct_reachable,
            detect_giuroll=detect_giuroll,
        )
        async with self._lock:
            if self.records.get(rec.post.id) is not rec:
                return
            if not self.integrations.available:
                return
            if not self.permitted(rec.monitor.integration_id):
                await self.remove(rec, "integration_disabled")
                return
            updated = copy.deepcopy(rec)
            monitor, post = updated.monitor, updated.post
            now = time.time()
            monitor.last_check_at = now
            monitor.next_check_at = now + CHECK_INTERVAL
            if detect_giuroll:
                monitor.last_giuroll_check_at = now
            if result.alive:
                apply_alive(updated, result, now)
            else:
                post.net_status = 0
                post.guest_connected = False
                post.reachability_lost = True
                if result.inconclusive:
                    monitor.failures = 0
                    monitor.first_failure_at = 0
                else:
                    if monitor.failures == 0:
                        monitor.first_failure_at = now
                    monitor.failures += 1
                    monitor.next_check_at = now + FAILURE_RECHECK_INTERVAL
                    if monitor.failures >= FAILURES_TO_CLOSE and now - monitor.first_failure_at >= FAILURE_GRACE:
                        await self.remove(rec, "host_unreachable")
                        return
            changed = asdict(rec.post) != asdict(post) or not rec.monitor.published and monitor.published
            if changed:
                post.updated_at = now
            def committed():
                self.records[post.id] = updated
                if monitor.published and not rec.monitor.published:
                    self.integrations.post_created(post.id)

            await self._durable(lambda: self.save(updated), committed)
            if changed and monitor.published:
                await self.publish("upsert", asdict(post))

    async def start(self):
        self._lock = asyncio.Lock()
        self._creates.clear()
        for rec in self.records.values():
            if rec.monitor is not None:
                # Never count downtime as failed probes or inherit a failure
                # streak that could immediately remove a restored listing.
                rec.monitor.next_check_at = 0
                rec.monitor.failures = 0
                rec.monitor.first_failure_at = 0
        self._task = asyncio.create_task(self._run(), name="udp-lobby")

    async def stop(self):
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        for job in list(self._admissions.values()):
            job.cancel()
        await asyncio.gather(*list(self._admissions.values()), return_exceptions=True)
        await asyncio.gather(*list(self._checks), return_exceptions=True)

    async def _run(self):
        while True:
            try:
                await self.tick()
            except Exception:
                print("UDP lobby monitor tick failed")
            await asyncio.sleep(MIN_TICK)


def build_router(service: Service):
    router = APIRouter()

    @router.post("/api/v1/posts", status_code=201, responses={
        200: {"description": "Existing registration (idempotent retry)"},
        409: {"description": "Conflicting or legacy in-progress registration"},
        422: {"description": "Invalid input, unconfirmed host or verification timeout"},
        503: {"description": "Verification busy/unavailable or storage unavailable"},
    })
    async def register(body: Registration, request: Request):
        item = service.authenticate(request)
        result, created = await service.register(item, body)
        return JSONResponse(result, status_code=201 if created else 200, headers={
            "Cache-Control": "no-store", "Location": result["status_url"],
        })

    @router.get("/api/v1/posts/{ident}")
    async def status(ident: str, request: Request):
        item = service.authenticate(request)
        rec = service.records.get(ident)
        if rec is None or rec.monitor is None or rec.monitor.integration_id != item.id:
            raise HTTPException(404, "registration not found or ended")
        return JSONResponse(service.status(rec), headers={"Cache-Control": "no-store"})

    return router
