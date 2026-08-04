import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import httpx
import pytest

from api_client import ApiClient, CREATE_POST_RETRIES


@pytest.mark.asyncio
async def test_create_retries_transport_errors():
    http = httpx.AsyncClient()
    api = ApiClient(http, "https://example.test")
    calls = {"n": 0}

    async def fake_post(*_args, **_kwargs):
        calls["n"] += 1
        request = httpx.Request("POST", "https://example.test/posts")
        if calls["n"] < CREATE_POST_RETRIES:
            raise httpx.ConnectError("temporary", request=request)
        return httpx.Response(
            200,
            request=request,
            json={"post": {"id": "p1"}, "owner_token": "tok"},
        )

    with patch.object(http, "post", side_effect=fake_post):
        with patch("api_client.asyncio.sleep", new=AsyncMock()):
            data = await api.create({"post_type": "casual", "addr": "1.2.3.4:10800"})

    assert data["post"]["id"] == "p1"
    assert calls["n"] == CREATE_POST_RETRIES
    await http.aclose()


@pytest.mark.asyncio
async def test_create_does_not_retry_http_status_errors():
    http = httpx.AsyncClient()
    api = ApiClient(http, "https://example.test")

    async def fake_post(*_args, **_kwargs):
        request = httpx.Request("POST", "https://example.test/posts")
        response = httpx.Response(409, request=request, json={"detail": {"message": "host not reachable"}})
        raise httpx.HTTPStatusError("conflict", request=request, response=response)

    with patch.object(http, "post", side_effect=fake_post):
        with pytest.raises(httpx.HTTPStatusError) as exc:
            await api.create({"post_type": "casual", "addr": "1.2.3.4:10800"})
        assert exc.value.response.status_code == 409

    await http.aclose()
