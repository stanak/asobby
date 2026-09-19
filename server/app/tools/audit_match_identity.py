#!/usr/bin/env python3
"""Read-only, narrowly scoped evidence export. Never infer or apply a merge.

PYTHONPATH=. python tools/audit_match_identity.py --user DISCORD_ID \
  --from 2026-09-18T19:29:00+00:00 --to 2026-09-18T19:51:00+00:00
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta
import json
import os

from sqlalchemy import inspect, or_, select, text

import db


def instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("UTC offset is required")
    return parsed


async def audit(user: str, start: datetime, end: datetime) -> dict:
    if end <= start or end - start > timedelta(days=1):
        raise ValueError("choose a positive interval of at most 24 hours")
    engine = db.make_engine(os.environ["DATABASE_URL"])
    try:
        async with engine.connect() as connection:
            if connection.dialect.name == "postgresql":
                await connection.execute(text("SET TRANSACTION READ ONLY"))
                await connection.execute(text("SET LOCAL statement_timeout = '10s'"))
            else:
                await connection.execute(text("PRAGMA query_only = ON"))
            m = db.Match.__table__
            matches = (await connection.execute(select(
                m.c.id, m.c.host_user_id, m.c.guest_user_id, m.c.winner,
                m.c.played_at, m.c.created_at, m.c.source, m.c.ranked,
                m.c.match_rank, m.c.host_wins, m.c.guest_wins,
            ).where(
                (m.c.host_user_id == user) | (m.c.guest_user_id == user),
                or_(m.c.played_at.between(start, end), m.c.created_at.between(start, end)),
            ).order_by(m.c.played_at).limit(1000))).mappings().all()
            reports = []
            has_reports = await connection.run_sync(lambda c: inspect(c).has_table("match_reports"))
            if has_reports:
                r = db.MatchReport.__table__
                reports = (await connection.execute(select(
                    r.c.user_id, r.c.client_id, r.c.match_id, r.c.side,
                    r.c.client_played_at, r.c.received_at, r.c.status, r.c.reason,
                    r.c.conflict_payload.is_not(None).label("has_conflict"),
                ).where(
                    r.c.user_id == user,
                    or_(r.c.received_at.between(start, end), r.c.client_played_at.between(start, end)),
                ).order_by(r.c.received_at).limit(1000))).mappings().all()
            return {"read_only": True, "user": user, "from": start, "to": end,
                    "report_table_present": has_reports,
                    "matches": [dict(row) for row in matches],
                    "reports": [dict(row) for row in reports],
                    "note": "Time proximity alone is not evidence of duplication. Compare both client logs before repair."}
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user", required=True)
    parser.add_argument("--from", dest="start", type=instant, required=True)
    parser.add_argument("--to", dest="end", type=instant, required=True)
    args = parser.parse_args()
    result = asyncio.run(audit(args.user, args.start, args.end))
    print(json.dumps(result, ensure_ascii=False, indent=2, default=lambda value: value.isoformat()))


if __name__ == "__main__":
    main()
