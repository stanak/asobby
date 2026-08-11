"""Lobby auto-post pause persistence tests."""
from __future__ import annotations

import time

from detect_pause import (
    DETECT_PAUSE_INACTIVE,
    DETECT_PAUSE_PERMANENT,
    detect_pause_until_from_duration,
    is_detect_pause_active,
    normalize_detect_pause_until,
)


def test_normalize_detect_pause_until():
    assert normalize_detect_pause_until(None) == DETECT_PAUSE_INACTIVE
    assert normalize_detect_pause_until(0) == DETECT_PAUSE_INACTIVE
    assert normalize_detect_pause_until(-1) == DETECT_PAUSE_PERMANENT
    assert normalize_detect_pause_until(123.5) == 123.5
    assert normalize_detect_pause_until("bad") == DETECT_PAUSE_INACTIVE


def test_is_detect_pause_active():
    now = 1_000_000.0
    assert not is_detect_pause_active(DETECT_PAUSE_INACTIVE, now=now)
    assert is_detect_pause_active(DETECT_PAUSE_PERMANENT, now=now)
    assert is_detect_pause_active(now + 60, now=now)
    assert not is_detect_pause_active(now - 1, now=now)


def test_detect_pause_until_from_duration():
    now = 1_000_000.0
    assert detect_pause_until_from_duration(0, now=now) == DETECT_PAUSE_INACTIVE
    assert (
        detect_pause_until_from_duration(DETECT_PAUSE_PERMANENT, now=now)
        == DETECT_PAUSE_PERMANENT
    )
    assert detect_pause_until_from_duration(1800, now=now) == now + 1800
