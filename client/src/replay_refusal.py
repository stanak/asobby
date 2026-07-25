"""Replay upload refusal helpers (client-side)."""
from __future__ import annotations

import time

REPLAY_REFUSAL_INACTIVE = 0.0
REPLAY_REFUSAL_PERMANENT = -1.0
REPLAY_REFUSAL_DURATIONS = {
    "30m": 30 * 60,
    "1h": 60 * 60,
    "3h": 3 * 60 * 60,
}


def normalize_replay_refusal_until(raw: object) -> float:
    if raw is None:
        return REPLAY_REFUSAL_INACTIVE
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return REPLAY_REFUSAL_INACTIVE
    if value == REPLAY_REFUSAL_PERMANENT:
        return REPLAY_REFUSAL_PERMANENT
    if value <= 0:
        return REPLAY_REFUSAL_INACTIVE
    return value


def is_replay_refusal_active(until: float, *, now: float | None = None) -> bool:
    value = normalize_replay_refusal_until(until)
    if value == REPLAY_REFUSAL_INACTIVE:
        return False
    if value == REPLAY_REFUSAL_PERMANENT:
        return True
    now = time.time() if now is None else now
    return now < value


def replay_refusal_until_from_duration(seconds: float, *, now: float | None = None) -> float:
    if seconds == REPLAY_REFUSAL_PERMANENT or seconds == float("inf"):
        return REPLAY_REFUSAL_PERMANENT
    if seconds <= 0:
        return REPLAY_REFUSAL_INACTIVE
    now = time.time() if now is None else now
    return now + seconds
