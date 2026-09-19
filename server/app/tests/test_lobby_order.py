"""One ordering contract for HTTP, SSE, integration JSON, and the browser fixture."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI, Request

import integrations
import main
from lobby_order import post_sort_key, post_status


CASES = json.loads(Path(__file__).with_name("lobby_order_cases.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("reverse", [False, True])
def test_state_type_time_and_id_priority(reverse):
    posts = copy.deepcopy(CASES["posts"])
    if reverse:
        posts.reverse()
    original = copy.deepcopy(posts)
    assert [p["id"] for p in sorted(posts, key=post_sort_key)] == CASES["expected"]
    assert posts == original


@pytest.mark.parametrize("raw, expected", [
    ({"net_status": 4, "guest_connected": True}, "playing"),
    ({"net_status": 4}, "playing"),
    ({"net_status": 2}, "connecting"),
    ({"net_status": 3, "guest_connected": True}, "connecting"),
    ({"guest_connected": True}, "connecting"),
    ({"net_status": 3, "guest_name": "guest", "match_status": "playing"}, "waiting"),
    ({"net_status": 0}, "unknown"),
    ({}, "unknown"),
])
def test_status_uses_protocol_flags_not_display_text(raw, expected):
    assert post_status(raw) == expected


@pytest.fixture
def records(monkeypatch):
    records = {
        p["id"]: main.PostRecord(post=main.Post(**p, updated_at=main.time.time()),
                                 owner_token="never-export", creator_ip="127.0.0.1")
        for p in CASES["posts"]
    }
    # An accepted UDP registration must stay hidden until its probe succeeds.
    records["unpublished"] = main.PostRecord(
        post=main.Post(id="unpublished", net_status=3, post_type="ranked", created_at=9999),
        owner_token="never-export", creator_ip="127.0.0.1", monitor=SimpleNamespace(published=False),
    )
    monkeypatch.setattr(main, "RECORDS", records)
    monkeypatch.setattr(main, "resolve_session", AsyncMock(return_value={"id": "viewer"}))
    return records


@pytest.mark.asyncio
async def test_native_http_and_sse_share_order_and_exclude_unpublished(records):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="https://test") as client:
        response = await client.get("/posts")
    assert response.status_code == 200
    assert [p["id"] for p in response.json()] == CASES["expected"]
    assert "never-export" not in response.text

    response = await main.sse_posts(Request({"type": "http", "headers": []}))
    try:
        first = await anext(response.body_iterator)
        assert first.startswith("event: snapshot\n")
        posts = json.loads(first.split("data: ", 1)[1])
        assert [p["id"] for p in posts] == CASES["expected"]
    finally:
        await response.body_iterator.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("include_address", [False, True])
async def test_integration_api_order_and_status_changes_invalidate_etag(records, include_address):
    service = integrations.IntegrationService(main.integration_posts, "https://test")
    credentials = await service.create(integrations.IntegrationInput(name="ordering", include_address=include_address))
    app = FastAPI()
    app.include_router(integrations.build_router(service, main.resolve_session, lambda uid: False))
    headers = {"Authorization": "Bearer " + credentials["api_key"]}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test") as client:
        response = await client.get("/api/v1/lobby", headers=headers)
        assert response.status_code == 200
        assert [p["id"] for p in response.json()["posts"]] == CASES["expected"]
        assert all(("addr" in p) is include_address for p in response.json()["posts"])
        assert "never-export" not in response.text
        etag = response.headers["etag"]
        assert (await client.get("/api/v1/lobby", headers={**headers, "If-None-Match": etag})).status_code == 304

        # Insertion order and heartbeats do not affect the response/revision.
        records["waiting-ranked-a"].post.updated_at += 1
        records["waiting-ranked-a"] = records.pop("waiting-ranked-a")
        assert (await client.get("/api/v1/lobby", headers={**headers, "If-None-Match": etag})).status_code == 304

        # A previously playing casual host resumes recruitment above busy Ranked.
        records["playing-casual"].post.net_status = 3
        changed = await client.get("/api/v1/lobby", headers={**headers, "If-None-Match": etag})
        assert changed.status_code == 200 and changed.headers["etag"] != etag
        expected = CASES["expected"].copy()
        expected.remove("playing-casual")
        expected.insert(3, "playing-casual")
        assert [p["id"] for p in changed.json()["posts"]] == expected
        assert [p["id"] for p in main.sorted_public_posts()] == expected

        # guest_connected wins over net_status=3 even with no guest name.
        records["playing-casual"].post.guest_connected = True
        busy = await client.get("/api/v1/lobby", headers={**headers, "If-None-Match": changed.headers["etag"]})
        assert busy.status_code == 200
        assert [p["id"] for p in busy.json()["posts"]] == CASES["expected"]
        assert busy.json()["posts"][-1]["status"] == "connecting"
