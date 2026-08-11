"""Lobby auto-post pause helpers (client-side, persisted in config)."""
from __future__ import annotations

import time

DETECT_PAUSE_INACTIVE = 0.0
DETECT_PAUSE_PERMANENT = -1.0


def normalize_detect_pause_until(raw: object) -> float:
    if raw is None:
        return DETECT_PAUSE_INACTIVE
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DETECT_PAUSE_INACTIVE
    if value == DETECT_PAUSE_PERMANENT:
        return DETECT_PAUSE_PERMANENT
    if value <= 0:
        return DETECT_PAUSE_INACTIVE
    return value


def is_detect_pause_active(until: float, *, now: float | None = None) -> bool:
    value = normalize_detect_pause_until(until)
    if value == DETECT_PAUSE_INACTIVE:
        return False
    if value == DETECT_PAUSE_PERMANENT:
        return True
    now = time.time() if now is None else now
    return now < value


def detect_pause_until_from_duration(seconds: float, *, now: float | None = None) -> float:
    if seconds == DETECT_PAUSE_PERMANENT or seconds == float("inf"):
        return DETECT_PAUSE_PERMANENT
    if seconds <= 0:
        return DETECT_PAUSE_INACTIVE
    now = time.time() if now is None else now
    return now + seconds
