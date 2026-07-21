"""ゲストプローブのスケジューリング helpers のテスト。"""
from __future__ import annotations

from dataclasses import dataclass, field
from time import time

import main


@dataclass
class FakePost:
    id: str
    addr: str
    updated_at: float = field(default_factory=time)
    autopunch: bool = False


@dataclass
class FakeRecord:
    post: FakePost
    owner_token: str = "tok"
    owner_user_id: str = "u1"


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
    try:
        good = FakeRecord(FakePost(id="b", addr="203.0.113.10:10800"))
        bad = FakeRecord(FakePost(id="a", addr="invalid"))
        main.RECORDS["b"] = good
        main.RECORDS["a"] = bad

        targets = main.probe_target_records()
        assert [rec.post.id for rec in targets] == ["b"]
    finally:
        main.RECORDS.clear()
