"""ゲストプローブのスケジューリング helpers のテスト。"""
from __future__ import annotations

from dataclasses import dataclass, field
from time import time
import asyncio
import threading
import pytest

import main


@dataclass
class FakePost:
    id: str
    addr: str
    updated_at: float = field(default_factory=time)
    autopunch: bool = False
    net_status: int = main.NET_CHECKING


@dataclass
class FakeRecord:
    post: FakePost
    owner_token: str = "tok"
    owner_user_id: str = "u1"
    guest_ip: str = ""
    guest_user_id: str = ""


def test_parse_probe_addr_valid():
    post = FakePost(id="a", addr="203.0.113.10:10800")
    assert main.parse_probe_addr(post) == ("203.0.113.10", 10800)


def test_parse_probe_addr_invalid():
    assert main.parse_probe_addr(FakePost(id="a", addr="bad")) is None
    assert main.parse_probe_addr(FakePost(id="a", addr="203.0.113.10:0")) is None


def test_guest_probe_tick_sleep_scales_with_post_count(monkeypatch):
    monkeypatch.setattr(main, "GUEST_PROBE_ROUND_SEC", 10.0)
    monkeypatch.setattr(main, "GUEST_PROBE_MIN_TICK_SEC", 0.4)

    assert main.guest_probe_tick_sleep_sec(0) == 10.0
    assert main.guest_probe_tick_sleep_sec(1) == 10.0
    assert main.guest_probe_tick_sleep_sec(5) == 2.0
    assert main.guest_probe_tick_sleep_sec(25) == 0.4


def test_probe_target_records_sorted_and_filtered(monkeypatch):
    main.RECORDS.clear()
    main.RANKED_SESSIONS.clear()
    try:
        good = FakeRecord(FakePost(id="b", addr="203.0.113.10:10800"))
        bad = FakeRecord(FakePost(id="a", addr="invalid"))
        main.RECORDS["b"] = good
        main.RECORDS["a"] = bad
        assert [rec.post.id for rec in main.probe_target_records()] == ["b"]
    finally:
        main.RECORDS.clear()
        main.RANKED_SESSIONS.clear()


@pytest.mark.asyncio
async def test_guest_probe_is_single_flight_and_throttled(monkeypatch):
    rec = main.PostRecord(
        post=main.Post(id="host", addr="203.0.113.1:10800", net_status=main.NET_CHECKING),
        owner_token="tok", creator_ip="203.0.113.1",
    )
    monkeypatch.setattr(main, "RECORDS", {"host": rec})
    started, release = threading.Event(), threading.Event()
    calls = []
    def probe(*args, **kwargs):
        calls.append(1)
        started.set()
        assert release.wait(2)
        return None
    monkeypatch.setattr(main, "probe_post_status", probe)
    first = asyncio.create_task(main.probe_guest_for_record(rec))
    assert await asyncio.to_thread(started.wait, 1)
    try:
        await main.probe_guest_for_record(rec, force=True)
        assert len(calls) == 1
    finally:
        release.set()
        await first
    await main.probe_guest_for_record(rec)
    assert len(calls) == 1 and not rec.guest_probe_pending


@pytest.mark.asyncio
async def test_cancelled_guest_probe_retains_single_flight_until_worker_finishes(monkeypatch):
    rec = main.PostRecord(
        post=main.Post(id="host", addr="203.0.113.1:10800", net_status=main.NET_CHECKING),
        owner_token="tok", creator_ip="203.0.113.1",
    )
    monkeypatch.setattr(main, "RECORDS", {"host": rec})
    started, release = threading.Event(), threading.Event()
    def probe(*args, **kwargs):
        started.set()
        assert release.wait(2)
        return None
    monkeypatch.setattr(main, "probe_post_status", probe)
    job = asyncio.create_task(main.probe_guest_for_record(rec))
    assert await asyncio.to_thread(started.wait, 1)
    job.cancel()
    await asyncio.sleep(0)
    assert rec.guest_probe_pending and not job.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await job
    assert not rec.guest_probe_pending


def test_guest_probe_removed_while_queued_never_sends(monkeypatch):
    def forbidden(*args):
        raise AssertionError("closed listing must not open a transport")
    monkeypatch.setattr(main.udp_probe, "get_transport", forbidden)
    assert main.probe_post_status("203.0.113.1", 10800, False, still_current=lambda: False) is None


def test_known_ip_but_unidentified_guest_remains_eligible():
    rec = main.PostRecord(
        post=main.Post(id="host", net_status=main.NET_CHECKING),
        owner_token="tok", creator_ip="203.0.113.1", guest_ip="203.0.113.2",
    )
    assert not main.guest_probe_paused(rec)
