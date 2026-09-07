"""Optional, admin-managed lobby integrations. No downstream product dependency.

This is a latest-state notification service, not an audit/event-history stream.
One in-flight request per destination preserves order. A restart sends a fresh
snapshot signal to every enabled destination, recovering interrupted delivery.
Like the lobby itself, this service assumes a single application process.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import re
import secrets
import socket
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

import post_redis

MAX_INTEGRATIONS = 20
MIN_DELIVERY_INTERVAL = 5.0
API_REQUESTS_PER_MINUTE = 60
# Explicit allowlist: no owner credentials, Discord IDs, heartbeat timestamps,
# guest IPs, private message queues or user settings leave this API.
LOBBY_FIELDS = (
    "id", "owner_name", "rank", "post_type", "rating", "comment", "created_at",
    "stream_url", "giuroll", "autopunch", "direct_reachable",
    "reachability_uncertain", "reachability_lost", "match_status", "guest_name",
    "ranked_active", "country_code", "country_name",
)


def encode_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def normalize_webhook_url(value: str) -> str:
    value = value.strip()
    if not value:  # API-only integration
        return ""
    if len(value) > 2048 or any(ord(c) <= 32 or ord(c) == 127 for c in value):
        raise ValueError("Webhook URL contains invalid characters")
    try:
        url = httpx.URL(value)
        if (
            url.scheme != "https" or not url.host or url.userinfo
            or url.fragment or "#" in value or not 1 <= (url.port or 443) <= 65535
        ):
            raise ValueError()
    except (ValueError, httpx.InvalidURL):
        raise ValueError("Webhook URL must be HTTPS without credentials or a fragment") from None
    return str(url)


async def resolve_webhook_target(value: str) -> tuple[httpx.URL, str, str]:
    """Resolve once, reject non-public addresses and pin the actual connection."""
    url = httpx.URL(normalize_webhook_url(value))
    host = url.raw_host.decode("ascii")
    try:
        records = await asyncio.wait_for(
            asyncio.get_running_loop().getaddrinfo(host, url.port or 443, type=socket.SOCK_STREAM),
            timeout=5,
        )
    except (OSError, TimeoutError):
        raise ValueError("Webhook hostname could not be resolved") from None
    addresses = list(dict.fromkeys(record[4][0] for record in records))
    if not addresses:
        raise ValueError("Webhook hostname has no addresses")
    for raw in addresses:
        address = ipaddress.ip_address(raw)
        if not address.is_global or address.is_multicast:
            raise ValueError("Webhook destination must use public IP addresses")
        if isinstance(address, ipaddress.IPv6Address):
            embedded = address.ipv4_mapped
            if address in ipaddress.IPv6Network("64:ff9b::/96"):
                embedded = ipaddress.IPv4Address(int(address) & 0xFFFFFFFF)
            if address.sixtofour or address.teredo or (
                embedded and (not embedded.is_global or embedded.is_multicast)
            ):
                raise ValueError("Webhook destination must use public IP addresses")
    # IPv4 first, when available, for hosts with no IPv6 egress.
    addresses.sort(key=lambda raw: ipaddress.ip_address(raw).version)
    authority = f"[{host}]" if ":" in host else host
    if url.port and url.port != 443:
        authority += f":{url.port}"
    return url.copy_with(host=addresses[0]), authority, host


class IntegrationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    webhook_url: str = ""
    enabled: bool = True
    include_address: bool = False
    allow_posting: bool = False

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("name is required")
        return value.strip()

    @field_validator("webhook_url")
    @classmethod
    def clean_url(cls, value: str) -> str:
        return normalize_webhook_url(value)


class IntegrationPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=80)
    webhook_url: str | None = None
    enabled: bool | None = None
    include_address: bool | None = None
    allow_posting: bool | None = None


class Integration(IntegrationInput):
    id: str
    api_key_hash: str = Field(repr=False)
    signing_secret: str = Field(repr=False)
    generation: str
    created_at: float
    last_success_at: float | None = None
    last_error: str | None = None
    last_status: int | None = None

    def public(self) -> dict[str, Any]:
        # URLs may carry bearer credentials in their path/query (e.g. hooks).
        # Never return the full URL or persisted secrets in a list response.
        return {
            "id": self.id, "name": self.name, "enabled": self.enabled,
            "include_address": self.include_address,
            "allow_posting": self.allow_posting,
            "webhook_host": httpx.URL(self.webhook_url).host if self.webhook_url else "",
            "created_at": self.created_at, "last_success_at": self.last_success_at,
            "last_error": self.last_error, "last_status": self.last_status,
        }


@dataclass
class Pending:
    event: dict[str, Any] | None = None
    attempts: int = 0
    next_at: float = 0


def retry_after_seconds(raw: str) -> float:
    try:
        seconds = float(raw)
    except ValueError:
        try:
            date = parsedate_to_datetime(raw)
            seconds = date.timestamp() - time.time()
        except (ValueError, TypeError, OverflowError):
            return 0
    if not 0 < seconds < float("inf"):
        return 0
    return min(seconds, 3600)


async def deliver(item: Integration, event: dict[str, Any]) -> tuple[int, float]:
    """No redirects/proxies, verified TLS/SNI, no response-body buffering."""
    async with asyncio.timeout(15):
        url, authority, host = await resolve_webhook_target(item.webhook_url)
        body = encode_json(event)
        timestamp = str(int(time.time()))
        signature = hmac.new(
            item.signing_secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256,
        ).hexdigest()
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(10, connect=5), trust_env=False, follow_redirects=False,
        ) as client:
            async with client.stream(
                "POST", url, content=body,
                headers={
                    "Host": authority, "Content-Type": "application/json",
                    "User-Agent": "asobby-webhook/1",
                    "X-Asobby-Event": event["type"],
                    "X-Asobby-Delivery": event["id"],
                    "X-Asobby-Timestamp": timestamp,
                    "X-Asobby-Signature": f"sha256={signature}",
                },
                extensions={"sni_hostname": host},
            ) as response:
                return response.status_code, retry_after_seconds(response.headers.get("Retry-After", ""))


class IntegrationService:
    def __init__(self, read_posts: Callable[[], list[dict[str, Any]]], base_url: str):
        self.read_posts = read_posts
        self.base_url = base_url.rstrip("/")
        self.items: dict[str, Integration] = {}
        self.pending: dict[str, Pending] = {}
        self.api_reads: dict[str, deque[float]] = {}
        self._revision: str | None = None
        self._lock = asyncio.Lock()
        self._jobs: dict[str, asyncio.Task] = {}
        self._task: asyncio.Task | None = None
        self.store_error = False
        self.available = True

    def snapshot(self, *, include_address: bool = False) -> dict[str, Any]:
        posts = []
        for raw in self.read_posts():
            post = {key: raw[key] for key in LOBBY_FIELDS if key in raw}
            net_status = raw.get("net_status")
            post["status"] = (
                "playing" if net_status == 4 else
                "connecting" if raw.get("guest_connected") or net_status == 2 else
                "waiting" if net_status == 3 else "unknown"
            )
            if include_address:
                post["addr"] = raw.get("addr", "")
            posts.append(post)
        posts.sort(key=lambda p: (-float(p.get("created_at", 0)), str(p.get("id", ""))))
        revision = hashlib.sha256(encode_json(posts)).hexdigest()
        return {
            "schema_version": 1, "revision": revision, "count": len(posts),
            "posts": posts, "lobby_url": self.base_url + "/",
        }

    def event(self) -> dict[str, Any]:
        # Global change signal contains no host addresses or listing contents.
        snapshot = self.snapshot(include_address=True)
        return {
            "schema_version": 1, "id": uuid4().hex, "type": "lobby.changed",
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "source": self.base_url,
            "data": {
                "count": snapshot["count"],
                "snapshot_url": self.base_url + "/api/v1/lobby",
            },
        }

    async def start(self) -> None:
        self.pending.clear()
        self.api_reads.clear()
        try:
            self.items = {item.id: item for item in (
                Integration.model_validate(raw)
                for raw in await asyncio.to_thread(post_redis.load_integrations)
            )}
            if len(self.items) > MAX_INTEGRATIONS:
                raise ValueError("too many persisted integrations")
        except Exception:
            self.items = {}
            self.store_error = True
            self.available = False
            print("integration storage could not be loaded; integrations disabled")
            return
        self.available = True
        self.store_error = False
        self._revision = None
        self._task = asyncio.create_task(self._run(), name="lobby-integrations")

    async def stop(self) -> None:
        tasks = [task for task in [self._task, *self._jobs.values()] if task]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._task = None
        self._jobs.clear()

    async def _commit(self, items: dict[str, Integration]) -> None:
        if not self.available:
            raise HTTPException(503, "integration storage unavailable")
        write = asyncio.create_task(asyncio.to_thread(post_redis.save_integrations, [
            item.model_dump() for item in items.values()
        ]))
        cancelled = False
        try:
            try:
                await asyncio.shield(write)
            except asyncio.CancelledError:
                # A thread cannot be cancelled. Keep the caller's lock until
                # its atomic write finishes so a later write cannot be lost.
                cancelled = True
                await write
        except Exception:
            self.store_error = True
            raise HTTPException(503, "integration storage unavailable") from None
        self.store_error = False
        previous = self.items
        self.items = items
        for ident, item in items.items():
            if ident not in previous or previous[ident].generation != item.generation:
                self._reset(item)
        for ident in previous.keys() - items.keys():
            if task := self._jobs.get(ident):
                task.cancel()
            self.pending.pop(ident, None)
            self.api_reads.pop(ident, None)
        if cancelled:
            raise asyncio.CancelledError

    def _reset(self, item: Integration) -> None:
        task = self._jobs.get(item.id)
        if task:
            task.cancel()
        self.pending[item.id] = Pending(
            event=self.event() if item.enabled and item.webhook_url else None,
            next_at=time.monotonic() + MIN_DELIVERY_INTERVAL,
        )
        self.api_reads.pop(item.id, None)

    async def create(self, body: IntegrationInput) -> dict[str, Any]:
        if body.webhook_url:
            await resolve_webhook_target(body.webhook_url)
        async with self._lock:
            if len(self.items) >= MAX_INTEGRATIONS:
                raise HTTPException(409, f"at most {MAX_INTEGRATIONS} integrations are allowed")
            ident = uuid4().hex
            api_key = f"asi_{ident}.{secrets.token_urlsafe(32)}"
            item = Integration(
                **body.model_dump(), id=ident, generation=uuid4().hex,
                api_key_hash=hashlib.sha256(api_key.encode()).hexdigest(),
                signing_secret=secrets.token_urlsafe(32), created_at=time.time(),
            )
            await self._commit({**self.items, ident: item})
            return {"integration": item.public(), "api_key": api_key, "signing_secret": item.signing_secret}

    async def update(self, ident: str, patch: IntegrationPatch) -> dict[str, Any]:
        changes = patch.model_dump(exclude_unset=True)
        if any(value is None for value in changes.values()):
            raise HTTPException(422, "fields must not be null")
        if "webhook_url" in changes:
            changes["webhook_url"] = normalize_webhook_url(changes["webhook_url"])
            if changes["webhook_url"]:
                await resolve_webhook_target(changes["webhook_url"])
        async with self._lock:
            previous = self.get(ident)
            item = Integration.model_validate({
                **previous.model_dump(), **changes, "generation": uuid4().hex,
                "last_error": None, "last_status": None,
            })
            await self._commit({**self.items, ident: item})
            return item.public()

    async def rotate(self, ident: str) -> dict[str, Any]:
        async with self._lock:
            previous = self.get(ident)
            api_key = f"asi_{ident}.{secrets.token_urlsafe(32)}"
            item = previous.model_copy(update={
                "api_key_hash": hashlib.sha256(api_key.encode()).hexdigest(),
                "signing_secret": secrets.token_urlsafe(32), "generation": uuid4().hex,
            })
            await self._commit({**self.items, ident: item})
            return {"integration": item.public(), "api_key": api_key, "signing_secret": item.signing_secret}

    async def delete(self, ident: str) -> None:
        async with self._lock:
            self.get(ident)
            await self._commit({key: value for key, value in self.items.items() if key != ident})

    def get(self, ident: str) -> Integration:
        if ident not in self.items:
            raise HTTPException(404, "integration not found")
        return self.items[ident]

    def authenticate(self, request: Request) -> Integration:
        if not self.available:
            raise HTTPException(503, "integration storage unavailable")
        scheme, _, token = request.headers.get("Authorization", "").partition(" ")
        match = re.fullmatch(r"asi_([0-9a-f]{32})\.[A-Za-z0-9_-]{43}", token)
        item = self.items.get(match[1]) if match else None
        if (
            scheme.lower() != "bearer" or item is None or not item.enabled
            or not hmac.compare_digest(hashlib.sha256(token.encode()).hexdigest(), item.api_key_hash)
        ):
            raise HTTPException(401, "invalid integration key", headers={"WWW-Authenticate": "Bearer"})
        recent = self.api_reads.setdefault(item.id, deque())
        now = time.monotonic()
        while recent and recent[0] <= now - 60:
            recent.popleft()
        if len(recent) >= API_REQUESTS_PER_MINUTE:
            raise HTTPException(429, "too many requests", headers={"Retry-After": "60"})
        recent.append(now)
        return item

    def queue_test(self, ident: str) -> None:
        item = self.get(ident)
        if not item.enabled or not item.webhook_url:
            raise HTTPException(409, "enable a webhook destination first")
        pending = self.pending.setdefault(ident, Pending())
        pending.event = {**self.event(), "test": True}
        # Tests coalesce and never bypass an existing retry/cooldown.
        pending.next_at = max(pending.next_at, time.monotonic() + MIN_DELIVERY_INTERVAL)

    async def tick(self) -> None:
        if not self._jobs and not any(item.enabled and item.webhook_url for item in self.items.values()):
            self._revision = None
            return
        revision = self.snapshot(include_address=True)["revision"]
        if revision != self._revision:
            self._revision = revision
            event = self.event()
            for item in self.items.values():
                if item.enabled and item.webhook_url:
                    pending = self.pending.setdefault(item.id, Pending())
                    if pending.event is None:
                        pending.next_at = max(pending.next_at, time.monotonic() + MIN_DELIVERY_INTERVAL)
                    pending.event = event
        for ident, task in list(self._jobs.items()):
            if task.done():
                # _send handles delivery failures; cancellation is normal on edits.
                del self._jobs[ident]
                if not task.cancelled() and task.exception() is not None:
                    print("integration delivery task failed")
                    if pending := self.pending.get(ident):
                        pending.next_at = time.monotonic() + MIN_DELIVERY_INTERVAL
        for item in list(self.items.values()):
            pending = self.pending.get(item.id)
            if (
                len(self._jobs) < 4 and item.id not in self._jobs
                and item.enabled and item.webhook_url and pending and pending.event
                and pending.next_at <= time.monotonic()
            ):
                self._jobs[item.id] = asyncio.create_task(
                    self._send(item, pending, pending.event), name=f"webhook-{item.id}",
                )

    async def _send(self, item: Integration, pending: Pending, event: dict[str, Any]) -> None:
        status = None
        retry_after = 0.0
        error = None
        try:
            status, retry_after = await deliver(item, event)
            if not 200 <= status < 300:
                error = f"HTTP {status}"
        except ValueError:
            error = "unsafe or unresolvable destination"
        except Exception:
            # Exception text can contain the entire secret-bearing URL.
            error = "connection or delivery failed"
        async with self._lock:
            current = self.items.get(item.id)
            if current is None or current.generation != item.generation:
                return
            updates: dict[str, Any] = {"last_error": error, "last_status": status}
            if error is None:
                updates["last_success_at"] = time.time()
            try:
                await self._commit({**self.items, item.id: current.model_copy(update=updates)})
            except HTTPException:
                error = "delivery status could not be persisted"
            if error is None:
                pending.attempts = 0
                if pending.event and pending.event["id"] == event["id"]:
                    pending.event = None
                pending.next_at = time.monotonic() + MIN_DELIVERY_INTERVAL
            else:
                pending.attempts += 1
                pending.next_at = time.monotonic() + max(
                    min(300, 5 * 2 ** min(pending.attempts - 1, 6)), retry_after,
                )

    async def _run(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:
                # Keep the lobby and notification loop alive; never log URLs/keys.
                print("integration worker tick failed")
            await asyncio.sleep(1)


def build_router(service: IntegrationService, resolve_session, is_admin_user) -> APIRouter:
    router = APIRouter()

    async def admin(request: Request) -> None:
        session = await resolve_session(request)
        if session is None:
            raise HTTPException(401, "login required")
        if not is_admin_user(session["id"]):
            raise HTTPException(403, "admin only")
        if request.method != "GET":
            expected = urlsplit(service.base_url)
            origins = {
                f"{expected.scheme}://{expected.netloc}",
                str(request.base_url).rstrip("/"),
            }
            if request.headers.get("sec-fetch-site") == "cross-site" or (
                request.headers.get("origin") and request.headers["origin"] not in origins
            ):
                raise HTTPException(403, "cross-origin admin request rejected")

    @router.get("/admin/integrations")
    async def list_integrations(request: Request):
        await admin(request)
        return JSONResponse({
            "integrations": [item.public() for item in service.items.values()],
            "api_url": service.base_url + "/api/v1/lobby",
            "storage_error": service.store_error,
        }, headers={"Cache-Control": "no-store"})

    @router.post("/admin/integrations", status_code=201)
    async def create_integration(body: IntegrationInput, request: Request):
        await admin(request)
        try:
            result = await service.create(body)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        return JSONResponse(result, status_code=201, headers={"Cache-Control": "no-store"})

    @router.patch("/admin/integrations/{ident}")
    async def update_integration(ident: str, body: IntegrationPatch, request: Request):
        await admin(request)
        try:
            result = await service.update(ident, body)
        except ValueError:
            raise HTTPException(422, "invalid integration configuration") from None
        return JSONResponse({"integration": result}, headers={"Cache-Control": "no-store"})

    @router.delete("/admin/integrations/{ident}", status_code=204)
    async def delete_integration(ident: str, request: Request):
        await admin(request)
        await service.delete(ident)
        return Response(status_code=204)

    @router.post("/admin/integrations/{ident}/rotate")
    async def rotate_keys(ident: str, request: Request):
        await admin(request)
        return JSONResponse(await service.rotate(ident), headers={"Cache-Control": "no-store"})

    @router.post("/admin/integrations/{ident}/test", status_code=202)
    async def test_webhook(ident: str, request: Request):
        await admin(request)
        service.queue_test(ident)
        return {"queued": True}

    @router.get("/api/v1/lobby")
    async def lobby_snapshot(request: Request):
        item = service.authenticate(request)
        snapshot = service.snapshot(include_address=item.include_address)
        etag = f'"{snapshot["revision"]}"'
        headers = {"ETag": etag, "Cache-Control": "private, no-store", "Vary": "Authorization"}
        supplied = request.headers.get("If-None-Match", "").split(",")
        if any(value.strip().removeprefix("W/") in {etag, "*"} for value in supplied):
            return Response(status_code=304, headers=headers)
        return JSONResponse(snapshot, headers=headers)

    return router
