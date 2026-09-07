"""Keep README's complete examples and schema fields aligned with the API.

No additional runtime/test packages are needed: Pydantic checks documented
field types; real serializers/routes check examples, optional fields and HTTP.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import get_type_hints

import httpx
import pytest
from fastapi import FastAPI
from pydantic import TypeAdapter, ValidationError

import integrations
import main
import udp_lobby


README = Path(__file__).resolve().parents[2] / "README.md"


def doc_block(name):
    marker = re.escape(f"<!-- api-doc:{name} -->")
    matches = re.findall(marker + r"\s*```json\n(.*?)\n```", README.read_text(encoding="utf-8"), re.S)
    assert len(matches) == 1, f"Missing or duplicate README block: {name}"
    return json.loads(matches[0])


def sample_post():
    return main.Post(
        id="0123456789abcdef0123456789abcdef", owner_name="プレイヤー",
        rank="normal", rank_status="unset", ranked_games=0,
        addr="203.0.113.10:10800", comment="対戦募集",
        created_at=1788825600.0, updated_at=1788825600.0, net_status=3, direct_reachable=True,
    )


def schema_types(schema):
    if "anyOf" in schema:
        return set().union(*(schema_types(branch) for branch in schema["anyOf"]))
    value = schema["type"]
    return {value} if isinstance(value, str) else set(value)


def test_lobby_schema_fields_and_types_match_public_allowlist():
    schema = doc_block("lobby-schema")
    item = schema["properties"]["posts"]["items"]
    required = set(integrations.LOBBY_FIELDS) | {"status"}
    assert set(item["required"]) == required
    assert set(item["properties"]) == required | {"addr"}
    assert schema["additionalProperties"] is False and item["additionalProperties"] is False
    assert schema["type"] == item["type"] == "object"
    assert schema["properties"]["posts"]["type"] == "array"
    hints = get_type_hints(main.Post)
    hints.update(discord_user_id=str | None, discord_user_id_source=str | None)
    for field in set(integrations.LOBBY_FIELDS) | {"addr"}:
        assert schema_types(item["properties"][field]) == schema_types(TypeAdapter(hints[field]).json_schema()), field
    assert set(item["properties"]["rank"]["enum"]) == {""} | set(main.RANK_LADDER)
    assert set(item["properties"]["post_type"]["enum"]) == {"casual", "ranked"}
    assert set(item["properties"]["rank_status"]["enum"]) == {"unset", "initial", "provisional", "ranked", "unknown"}
    assert set(item["properties"]["status"]["enum"]) == {"waiting", "connecting", "playing", "unknown"}
    assert schema["properties"]["schema_version"] == {"type": "integer", "const": 1}
    assert schema["properties"]["count"] == {"type": "integer", "minimum": 0}
    assert item["properties"]["ranked_games"]["minimum"] == 0
    assert set(item["properties"]["discord_user_id_source"]["enum"]) == {"oauth", "integration", None}


@pytest.mark.asyncio
async def test_readme_complete_lobby_example_matches_http_and_ip_permissions():
    posts = [{**asdict(sample_post()), "discord_user_id": "123456789012345678", "discord_user_id_source": "oauth"}]
    service = integrations.IntegrationService(lambda: posts, "https://asobby.com")
    credentials = await service.create(integrations.IntegrationInput(name="docs"))
    async def no_session(request):
        return None
    app = FastAPI()
    app.include_router(integrations.build_router(service, no_session, lambda user: False))
    headers = {"Authorization": "Bearer " + credentials["api_key"]}
    schema = doc_block("lobby-schema")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://asobby.com") as client:
        response = await client.get("/api/v1/lobby", headers=headers)
        assert response.status_code == 200
        assert response.json() == doc_block("lobby-example")
        assert set(response.json()) == set(schema["properties"]) == set(schema["required"])
        assert re.fullmatch(schema["properties"]["revision"]["pattern"], response.json()["revision"])
        assert response.headers["etag"] == '"' + response.json()["revision"] + '"'
        cached = await client.get("/api/v1/lobby", headers={**headers, "If-None-Match": response.headers["etag"]})
        assert cached.status_code == 304 and cached.content == b""
        for item in (response, cached):
            assert item.headers["cache-control"] == "private, no-store"
            assert item.headers["vary"] == "Authorization"
        await service.update(credentials["integration"]["id"], integrations.IntegrationPatch(include_address=True))
        addressed = await client.get("/api/v1/lobby", headers=headers)
        post = addressed.json()["posts"][0]
        assert set(post) == set(schema["properties"]["posts"]["items"]["properties"])
        assert post["addr"] == sample_post().addr
        assert addressed.json()["revision"] != response.json()["revision"]
        posts.clear()
        empty = (await client.get("/api/v1/lobby", headers=headers)).json()
        assert empty["count"] == 0 and empty["posts"] == []
        assert service.snapshot(include_address=False)["revision"] == service.snapshot(include_address=True)["revision"]
        unauthorized = await client.get("/api/v1/lobby")
        assert unauthorized.status_code == 401
        assert unauthorized.json() == {"detail": "invalid integration key"}


@pytest.mark.parametrize("net_status,connected,expected", [
    (4, True, "playing"), (4, False, "playing"), (3, True, "connecting"),
    (2, False, "connecting"), (0, True, "connecting"), (3, False, "waiting"), (0, False, "unknown"),
])
def test_documented_status_priority(net_status, connected, expected):
    post = asdict(sample_post())
    post.update(net_status=net_status, guest_connected=connected)
    service = integrations.IntegrationService(lambda: [post], "https://asobby.com")
    assert service.snapshot()["posts"][0]["status"] == expected
    assert expected in doc_block("lobby-schema")["properties"]["posts"]["items"]["properties"]["status"]["enum"]


@pytest.mark.asyncio
async def test_readme_webhook_matches_event_and_test_notification():
    service = integrations.IntegrationService(lambda: [], "https://asobby.com")
    event, example, schema = service.event(), doc_block("webhook-example"), doc_block("webhook-schema")
    assert set(event) == set(example) == set(schema["required"])
    assert set(schema["properties"]) == set(event) | {"test"}
    assert event["data"] == example["data"]
    data_schema = schema["properties"]["data"]
    assert set(event["data"]) == set(data_schema["required"])
    assert set(data_schema["properties"]) == set(event["data"]) | {"post_id"}
    for key in ("schema_version", "type", "source"):
        assert event[key] == example[key]
    assert datetime.fromisoformat(example["occurred_at"]).utcoffset().total_seconds() == 0
    assert datetime.fromisoformat(event["occurred_at"]).utcoffset().total_seconds() == 0
    assert schema["properties"]["test"] == {"type": "boolean", "const": True}
    assert set(schema["properties"]["type"]["enum"]) == {"lobby.changed", "post.created"}
    credentials = await service.create(integrations.IntegrationInput(name="docs"))
    ident = credentials["integration"]["id"]
    # Queue-only: do not resolve a real webhook destination or start delivery.
    service.items[ident] = service.items[ident].model_copy(update={"webhook_url": "https://receiver.example/hook"})
    service.queue_test(ident)
    test_event = service.pending[ident].event
    assert test_event["test"] is True and set(test_event) == set(schema["properties"])
    assert test_event["data"] == event["data"]
    created_example = doc_block("webhook-created-example")
    service.post_created(created_example["data"]["post_id"])
    created = service.pending[ident].created[0]
    assert set(created) == set(created_example) == set(schema["required"])
    assert set(created["data"]) == set(created_example["data"]) == set(data_schema["properties"])
    assert created["type"] == created_example["type"] == "post.created"
    assert created["data"]["post_id"] == created_example["data"]["post_id"]
    assert created["data"]["snapshot_url"] == created_example["data"]["snapshot_url"]


def test_readme_registration_examples_are_full_raw_posts_not_lobby_projection(monkeypatch):
    active = doc_block("registration-active")
    checking = doc_block("registration-checking")
    post = main.Post(
        id="fedcba9876543210fedcba9876543210", rank="", owner_name="プレイヤー",
        addr="203.0.113.10:10800", comment="対戦募集",
        created_at=1788825600.0, updated_at=1788825600.0, net_status=3,
        direct_reachable=True, supports_messages=False, ping_warn_enabled=False,
    )
    rec = main.PostRecord(
        post=post, owner_token="not-public", creator_ip="",
        monitor=udp_lobby.Monitor("test", "player-456", "test", "test", published=True, discord_user_id="123456789012345678"),
    )
    monkeypatch.setattr(main.UDP_LOBBY.integrations, "base_url", "https://asobby.com")
    assert main.UDP_LOBBY.status(rec) == active
    assert set(active["post"]) == set(asdict(main.Post())) | {"discord_user_id", "discord_user_id_source"}
    assert TypeAdapter(main.Post).validate_python(active["post"]).ranked_games is None
    rec.monitor.published = False
    assert main.UDP_LOBBY.status(rec) == checking
    assert "status" not in active["post"]
    assert "supports_messages" not in integrations.LOBBY_FIELDS
    assert not {"owner_token", "monitor", "integration_id", "external_user_id"} & set(active["post"])


def test_registration_request_example_requires_replacing_placeholder():
    body = doc_block("registration-request")
    assert set(body) == set(udp_lobby.Registration.model_fields)
    with pytest.raises(ValidationError):
        udp_lobby.Registration.model_validate(body)
    # Validation only: never register this endpoint or send a UDP packet.
    valid = udp_lobby.Registration.model_validate({**body, "addr": "8.8.8.8:10800"})
    assert valid.comment == body["comment"] and valid.stream_url == ""
