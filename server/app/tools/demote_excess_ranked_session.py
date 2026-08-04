#!/usr/bin/env python3
"""同一セッション (30 分以内・同一プロファイル) で 4 戦目以降の ranked=true を false に戻す。

デフォルトは dry-run。--apply で DB を更新する。

使い方:
  cd server/app
  DATABASE_URL='postgresql+asyncpg://...' PYTHONPATH=. python tools/demote_excess_ranked_session.py
  DATABASE_URL='...' PYTHONPATH=. python tools/demote_excess_ranked_session.py --apply

  fly.io 本番 (dry-run)
  fly ssh console -a asobby -C 'cd /app && PYTHONPATH=/app python tools/demote_excess_ranked_session.py'
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections import defaultdict
from datetime import timezone
from typing import Optional

from sqlalchemy import select, update

import db

GAP_MINUTES = 30
MAX_RANKED_PER_SESSION = 3


def _ts(match: db.Match) -> Optional[float]:
    if match.played_at is None:
        return None
    dt = match.played_at
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _session_key(match: db.Match) -> Optional[tuple]:
    if not match.host_user_id:
        return None
    if not match.host_profile or not match.guest_profile:
        return None
    return (
        match.host_user_id,
        match.host_profile,
        match.guest_profile,
        match.match_rank or "",
    )


async def find_demote_ids() -> list[str]:
    gap_sec = GAP_MINUTES * 60
    async with db.session() as s:
        res = await s.execute(
            select(db.Match)
            .where(
                db.Match.ranked.is_(True),
                db.Match.host_user_id.is_not(None),
                db.Match.played_at.is_not(None),
            )
            .order_by(
                db.Match.host_user_id,
                db.Match.host_profile,
                db.Match.guest_profile,
                db.Match.match_rank,
                db.Match.played_at,
            )
        )
        matches = list(res.scalars().all())

    by_key: dict[tuple, list[db.Match]] = defaultdict(list)
    for m in matches:
        key = _session_key(m)
        if key is None:
            continue
        by_key[key].append(m)

    demote: list[str] = []
    for group in by_key.values():
        session: list[db.Match] = []
        prev_ts: Optional[float] = None
        ranked_in_session = 0
        for m in group:
            ts = _ts(m)
            if ts is None:
                continue
            if prev_ts is not None and ts - prev_ts > gap_sec:
                session = []
                ranked_in_session = 0
            session.append(m)
            prev_ts = ts
            ranked_in_session += 1
            if ranked_in_session > MAX_RANKED_PER_SESSION:
                demote.append(m.id)
    return demote


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="DB を更新する (省略時は dry-run)",
    )
    args = parser.parse_args()

    if not db.is_configured():
        print("DATABASE_URL が未設定です", file=sys.stderr)
        return 1

    demote_ids = await find_demote_ids()
    print(f"demote candidates: {len(demote_ids)}")
    if not demote_ids:
        return 0

    if not args.apply:
        for mid in demote_ids[:20]:
            print(f"  would demote {mid}")
        if len(demote_ids) > 20:
            print(f"  ... and {len(demote_ids) - 20} more")
        print("dry-run のみ (--apply で反映)")
        return 0

    async with db.session() as s:
        await s.execute(
            update(db.Match)
            .where(db.Match.id.in_(demote_ids))
            .values(ranked=False, match_rank=None)
        )
        await s.commit()
    print(f"demoted {len(demote_ids)} matches")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
