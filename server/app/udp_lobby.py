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
        if not self.enabled():
            raise HTTPException(503, "UDP host verification is disabled")
        if not self.stream_allowed(body.stream_url):
            raise HTTPException(422, "stream_url must be youtube, twitch, or niconico")
        # Omitting the new optional field must retain pre-upgrade retry hashes.
        fingerprint = hashlib.sha256(integrations.encode_json(body.model_dump(exclude_none=True))).hexdigest()
        async with self._lock:
            if not self.permitted(item.id):
                raise HTTPException(403, "listing creation is not allowed for this integration")
            managed = [rec for rec in self.records.values() if rec.monitor is not None]
            owned = [rec for rec in managed if rec.monitor.integration_id == item.id]
            for rec in owned:
                if rec.monitor.request_id == body.request_id:
                    if rec.monitor.fingerprint != fingerprint:
                        raise HTTPException(409, "request_id was already used with different data")
                    return self.status(rec)
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
            # Reserve the endpoint before the async disk write so a native
            # client finishing its probe cannot publish the same host midway.
            # Pending records remain invisible and tick() takes this lock.
            committed = False
            self.records[rec.post.id] = rec

            def accepted():
                nonlocal committed
                committed = True
                recent.append(now)

            try:
                await self._durable(lambda: self.save(rec), accepted)
            except BaseException:
                if not committed and self.records.get(rec.post.id) is rec:
                    self.records.pop(rec.post.id)
                raise
            return self.status(rec)

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
            records = [rec for rec in self.records.values() if rec.monitor is not None]
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
        # Only one bounded thread at a time; it shares the legacy probe lock.
        self._probe_task = asyncio.create_task(asyncio.to_thread(
            self.probe, rec.post.addr,
            prefer_autopunch=rec.post.autopunch and not rec.post.direct_reachable,
            detect_giuroll=detect_giuroll,
        ))
        try:
            result = await asyncio.shield(self._probe_task)
        except asyncio.CancelledError:
            await self._probe_task
            raise
        except Exception:
            result = Result(inconclusive=True)
        finally:
            self._probe_task = None
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
                # Do not equate a spectator error or a Giuroll pong with a
                # free player slot. Never infer identities from UDP addresses.
                post.guest_connected = result.state == "connecting"
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

    async def _run(self):
        while True:
            try:
                await self.tick()
            except Exception:
                print("UDP lobby monitor tick failed")
            await asyncio.sleep(MIN_TICK)


def build_router(service: Service):
    router = APIRouter()

    @router.post("/api/v1/posts", status_code=202)
    async def register(body: Registration, request: Request):
        item = service.authenticate(request)
        result = await service.register(item, body)
        return JSONResponse(result, status_code=202, headers={"Cache-Control": "no-store"})

    @router.get("/api/v1/posts/{ident}")
    async def status(ident: str, request: Request):
        item = service.authenticate(request)
        rec = service.records.get(ident)
        if rec is None or rec.monitor is None or rec.monitor.integration_id != item.id:
            raise HTTPException(404, "registration not found or ended")
        return JSONResponse(service.status(rec), headers={"Cache-Control": "no-store"})

    return router
