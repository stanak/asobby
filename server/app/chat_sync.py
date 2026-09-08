"""Opt-in, product-independent lobby chat bridge (single application process).

Receipts live in retained chat records, surviving restarts with the chat store.
They are never exposed through SSE, API responses or outgoing notifications.
"""
from __future__ import annotations

import asyncio
import hashlib
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from integrations import encode_json


class ChatInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=1, max_length=128)
    external_user_id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=550)
    discord_user_id: str | None = Field(default=None, min_length=1, max_length=20, pattern=r"^[1-9][0-9]{0,19}$")

    @field_validator("request_id", "external_user_id", "name")
    @classmethod
    def nonblank(cls, value: str) -> str:
        value = value.strip()
        if not value or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError("a nonempty value without control characters is required")
        return value

    @field_validator("discord_user_id")
    @classmethod
    def discord_snowflake(cls, value):
        if value is not None and int(value) > 2**64 - 1:
            raise ValueError("discord_user_id must be a positive unsigned 64-bit ID string")
        return value


def public_message(raw: dict[str, Any]) -> dict[str, Any]:
    """Explicit allowlist also normalizes pre-bridge persisted messages."""
    source = "integration" if raw.get("source") == "integration" else "asobby"
    uid = raw.get("user_id", "") if source == "asobby" else ""
    discord_id = (raw.get("discord_user_id") if source == "integration" else uid) or None
    return {
        "id": raw["id"], "user_id": uid, "name": raw.get("name", ""),
        "avatar": raw.get("avatar", "") if source == "asobby" else "",
        "text": raw.get("text", ""), "mentions": raw.get("mentions", []) if source == "asobby" else [],
        "lang": raw.get("lang", "ja"), "ts": raw["ts"], "source": source,
        "discord_user_id": discord_id,
        "discord_user_id_source": ("oauth" if source == "asobby" else "integration") if discord_id else None,
    }


async def durable(coroutine):
    """Finish publication before releasing its lock even if the HTTP client leaves."""
    task = asyncio.create_task(coroutine)
    cancelled = False
    while True:
        try:
            result = await asyncio.shield(task)
            break
        except asyncio.CancelledError:
            if task.cancelled():
                raise
            cancelled = True
    if cancelled:
        raise asyncio.CancelledError
    return result


class Service:
    def __init__(self, *, integrations, read, append, validate, lock):
        self.integrations = integrations
        self.read = read
        self.append = append
        self.validate = validate
        self.lock = lock

    async def create(self, body: ChatInput, item):
        async with self.lock:
            current = self.integrations.items.get(item.id)
            if not self.integrations.available:
                raise HTTPException(503, "integration storage unavailable")
            if current is None or current.generation != item.generation or not current.enabled:
                raise HTTPException(401, "integration key changed")
            if not current.allow_chat_posting:
                raise HTTPException(403, "chat posting is not allowed")
            text = self.validate(body.text)
            fingerprint = hashlib.sha256(encode_json({**body.model_dump(), "text": text})).hexdigest()
            messages = self.read()
            for raw in messages:
                receipt = raw.get("_chat_sync", {})
                if receipt.get("integration_id") == item.id and receipt.get("request_id") == body.request_id:
                    if receipt.get("fingerprint") != fingerprint:
                        raise HTTPException(409, "request_id was already used with different data")
                    return {"ok": True, "duplicate": True, "message": public_message(raw)}, 200
            now = time.time()
            if any(
                raw.get("_chat_sync", {}).get("integration_id") == item.id
                and raw["_chat_sync"].get("external_user_id") == body.external_user_id
                and now - raw["ts"] < 3 for raw in messages
            ):
                raise HTTPException(429, "please wait before sending another message", headers={"Retry-After": "3"})
            # Deterministic IDs let consumers recognize retries even after history eviction.
            ident = hashlib.sha256(encode_json([item.id, body.request_id])).hexdigest()[:32]
            raw = {
                "id": ident, "user_id": "", "name": body.name, "avatar": "",
                "text": text, "mentions": [], "lang": "ja", "ts": now,
                "source": "integration", "discord_user_id": body.discord_user_id,
                "_chat_sync": {
                    "integration_id": item.id, "request_id": body.request_id,
                    "external_user_id": body.external_user_id, "fingerprint": fingerprint,
                },
            }
            await self.append(raw)
            return {"ok": True, "duplicate": False, "message": public_message(raw)}, 201


def build_router(service: Service) -> APIRouter:
    router = APIRouter()
    headers = {"Cache-Control": "private, no-store", "Vary": "Authorization"}

    @router.get("/api/v1/chat")
    async def history(request: Request):
        item = service.integrations.authenticate(request)
        if not item.include_chat:
            raise HTTPException(403, "chat access is not allowed")
        messages = [public_message(raw) for raw in service.read()]
        return JSONResponse({"schema_version": 1, "count": len(messages), "messages": messages}, headers=headers)

    @router.post("/api/v1/chat", status_code=201)
    async def incoming(body: ChatInput, request: Request):
        item = service.integrations.authenticate(request)
        result, status = await durable(service.create(body, item))
        return JSONResponse(result, status_code=status, headers=headers)

    return router
