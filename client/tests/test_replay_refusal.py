"""Replay upload refusal tests."""
from __future__ import annotations

import time

from replay_refusal import (
    REPLAY_REFUSAL_INACTIVE,
    REPLAY_REFUSAL_PERMANENT,
    is_replay_refusal_active,
    normalize_replay_refusal_until,
    replay_refusal_until_from_duration,
)


def test_normalize_replay_refusal_until():
    assert normalize_replay_refusal_until(None) == REPLAY_REFUSAL_INACTIVE
    assert normalize_replay_refusal_until(0) == REPLAY_REFUSAL_INACTIVE
    assert normalize_replay_refusal_until(-1) == REPLAY_REFUSAL_PERMANENT
    assert normalize_replay_refusal_until(123.5) == 123.5
    assert normalize_replay_refusal_until("bad") == REPLAY_REFUSAL_INACTIVE


def test_is_replay_refusal_active():
    now = 1_000_000.0
    assert not is_replay_refusal_active(REPLAY_REFUSAL_INACTIVE, now=now)
    assert is_replay_refusal_active(REPLAY_REFUSAL_PERMANENT, now=now)
    assert is_replay_refusal_active(now + 60, now=now)
    assert not is_replay_refusal_active(now - 1, now=now)


def test_replay_refusal_until_from_duration():
    now = 1_000_000.0
    assert replay_refusal_until_from_duration(0, now=now) == REPLAY_REFUSAL_INACTIVE
    assert (
        replay_refusal_until_from_duration(REPLAY_REFUSAL_PERMANENT, now=now)
        == REPLAY_REFUSAL_PERMANENT
    )
    assert replay_refusal_until_from_duration(1800, now=now) == now + 1800
