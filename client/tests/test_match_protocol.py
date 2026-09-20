import json

import httpx
import pytest

from api_client import ApiClient
from stats_window import compute_summary, aggregate_by_my_char, _result_symbol


@pytest.mark.asyncio
async def test_old_server_never_receives_v2_result_or_sync():
    paths = []

    def respond(request):
        paths.append(request.url.path)
        return httpx.Response(404, json={"detail": "not found"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        api = ApiClient(http, "https://test.invalid")
        with pytest.raises(httpx.HTTPStatusError):
            await api.report_guest_match("host", report_version=2, client_id="a"*32)
        with pytest.raises(httpx.HTTPStatusError):
            await api.sync_matches([{"report_version": 2, "client_id": "a"*32}])
    assert paths == ["/matches/protocol", "/matches/protocol"]


@pytest.mark.asyncio
async def test_protocol_fields_are_kept_on_immediate_and_batch_reports():
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"report_version": 2, "random_selection": True, "ok": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        api = ApiClient(http, "https://test.invalid")
        identity = dict(report_version=2, client_id="a"*32, match_id="b"*32, duration_sec=12.5)
        choices = dict(host_random=True, guest_random=False)
        await api.report_result("post", "owner", "host", **identity, **choices)
        await api.report_guest_match("host", **identity, **choices)
        await api.sync_matches([{**identity, **choices, "winner": "host"}])
        await api.upload_replay(b"replay", client_id=identity["client_id"], match_id=identity["match_id"])
    assert len([r for r in requests if r.url.path == "/matches/protocol"]) == 1
    for request in requests[1:3]:
        body = json.loads(request.content)
        assert all(body[k] == v for k, v in identity.items())
        assert all(body[k] == v for k, v in choices.items())
    assert json.loads(requests[3].content)["matches"][0]["client_id"] == "a"*32
    assert requests[4].url.params["match_id"] == "b"*32


@pytest.mark.asyncio
async def test_v2_server_without_random_support_cannot_silently_drop_selection():
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"report_version": 2})
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        api = ApiClient(http, "https://test.invalid")
        with pytest.raises(httpx.RequestError, match="Random"):
            await api.report_guest_match("host", report_version=2, host_random=True)
        with pytest.raises(httpx.RequestError, match="Random"):
            await api.sync_matches([{"report_version": 2, "host_random": True}])
    assert all(r.method == "GET" for r in requests)


def test_pending_and_conflicting_reports_are_not_counted_in_local_stats():
    base = dict(my_side="host", winner="host", host_char=0, guest_char=5, played_at=1)
    rows = [{**base, "report_status": status} for status in ("confirmed", "pending", "conflict")]
    summary = compute_summary(rows)
    assert summary.total == summary.wins == 1
    assert aggregate_by_my_char(rows)[0].total == 1
    assert _result_symbol(rows[1]) == "…" and _result_symbol(rows[2]) == "!"


def test_random_statistics_do_not_double_count_resolved_character():
    from stats_window import _char_label
    rows = [dict(my_side="host", winner="host", host_char=20, guest_char=5,
                 host_actual_char=0, host_random=True, played_at=1, report_status="confirmed")]
    buckets = aggregate_by_my_char(rows)
    assert len(buckets) == 1 and buckets[0].total == 1
    assert _char_label(20) == "Random"
    assert compute_summary(rows).total == 1
