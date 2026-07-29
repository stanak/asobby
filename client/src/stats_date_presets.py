"""戦績日付フィルタの JST プリセット。"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))

DATE_PRESETS = ("today", "yesterday", "this_month", "this_year")


def jst_today() -> date:
    return datetime.now(JST).date()


def date_preset_range(preset: str) -> tuple[str, str]:
    today = jst_today()
    if preset == "today":
        s = today.isoformat()
        return s, s
    if preset == "yesterday":
        y = today - timedelta(days=1)
        s = y.isoformat()
        return s, s
    if preset == "this_month":
        return today.replace(day=1).isoformat(), today.isoformat()
    if preset == "this_year":
        return today.replace(month=1, day=1).isoformat(), today.isoformat()
    raise ValueError(f"unknown preset: {preset}")
