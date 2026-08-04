#!/usr/bin/env python3
"""guest/sync 等の重複 match 行をプロファイル照合でクラスタリングし、1 行にマージする。

デフォルトは dry-run（変更なし）。--apply で DB を更新する。

使い方:
  cd server/app
  DATABASE_URL='postgresql+asyncpg://...' PYTHONPATH=. python tools/merge_duplicate_matches.py
  DATABASE_URL='...' PYTHONPATH=. python tools/merge_duplicate_matches.py --apply

  # fly.io 本番 (dry-run; イメージに同梱後)
  fly ssh console -a asobby -C 'cd /app && python tools/merge_duplicate_matches.py'
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import delete, select

import db

WINDOW_SEC = 600


def _ts(match: db.Match) -> Optional[float]:
    if match.played_at is None:
        return None
    dt = match.played_at
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _score(match: db.Match, *, has_replay: bool) -> int:
    score = 0
    if match.source == "host":
        score += 100
    elif match.source == "sync":
        score += 30
    elif match.source == "guest":
        score += 10
    if match.ranked:
        score += 50
    if match.host_user_id and match.guest_user_id:
        score += 20
    if match.match_rank:
        score += 5
    if has_replay:
        score += 25
    return score


def _same_players(a: db.Match, b: db.Match) -> bool:
    ids_a = {x for x in (a.host_user_id, a.guest_user_id) if x}
    ids_b = {x for x in (b.host_user_id, b.guest_user_id) if x}
    if not ids_a or not ids_b:
        return False
    return ids_a == ids_b


def _should_cluster(a: db.Match, b: db.Match) -> bool:
    if a.id == b.id:
        return False
    if not a.host_profile or not a.guest_profile:
        return False
    if a.host_profile != b.host_profile or a.guest_profile != b.guest_profile:
        return False
    if not a.winner or a.winner != b.winner:
        return False

    sources = {a.source, b.source}
    if sources <= {"host"}:
        t1, t2 = _ts(a), _ts(b)
        if t1 is None or t2 is None:
            return False
        return abs(t1 - t2) <= 90

    if "guest" not in sources and "sync" not in sources:
        return False

    t1, t2 = _ts(a), _ts(b)
    if t1 is not None and t2 is not None and abs(t1 - t2) <= WINDOW_SEC:
        return True
    return _same_players(a, b) and ("guest" in sources and "sync" in sources)


def _cluster_bucket(group: list[db.Match]) -> list[list[db.Match]]:
    """同一プロファイル・勝敗バケット内だけ union-find (O(k log k))。"""
    if len(group) < 2:
        return []

    timed = [m for m in group if _ts(m) is not None]
    untimed = [m for m in group if _ts(m) is None]

    clusters: list[list[db.Match]] = []
    if len(timed) >= 2:
        timed.sort(key=_ts)  # type: ignore[arg-type]
        n = len(timed)
        parent = list(range(n))

        def find(i: int) -> int:
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        def union(i: int, j: int) -> None:
            ri, rj = find(i), find(j)
            if ri != rj:
                parent[rj] = ri

        for i in range(n):
            ti = _ts(timed[i])
            assert ti is not None
            for j in range(i + 1, n):
                tj = _ts(timed[j])
                assert tj is not None
                if tj - ti > WINDOW_SEC:
                    break
                if _should_cluster(timed[i], timed[j]):
                    union(i, j)

        grouped: dict[int, list[db.Match]] = defaultdict(list)
        for i, m in enumerate(timed):
            grouped[find(i)].append(m)
        clusters.extend(g for g in grouped.values() if len(g) > 1)

    if len(untimed) >= 2:
        for i in range(len(untimed)):
            for j in range(i + 1, len(untimed)):
                if _should_cluster(untimed[i], untimed[j]):
                    clusters.append([untimed[i], untimed[j]])

    return clusters


def _cluster_matches(matches: list[db.Match]) -> list[list[db.Match]]:
    buckets: dict[tuple[str, str, str], list[db.Match]] = defaultdict(list)
    for m in matches:
        if not m.host_profile or not m.guest_profile or not m.winner:
            continue
        buckets[(m.host_profile, m.guest_profile, m.winner)].append(m)

    out: list[list[db.Match]] = []
    for group in buckets.values():
        out.extend(_cluster_bucket(group))
    return out


def _pick_played_at(keeper: db.Match, donor: db.Match) -> Optional[datetime]:
    order = {"sync": 3, "host": 2, "guest": 1}
    k_rank = order.get(keeper.source, 0)
    d_rank = order.get(donor.source, 0)
    if d_rank > k_rank and donor.played_at is not None:
        return donor.played_at
    if keeper.played_at is None:
        return donor.played_at
    if donor.played_at is None:
        return keeper.played_at
    if keeper.source == "guest" and donor.source == "sync":
        return donor.played_at
    return min(keeper.played_at, donor.played_at, key=lambda d: d.timestamp())


def _merge_fields(keeper: db.Match, donor: db.Match) -> None:
    if not keeper.host_user_id and donor.host_user_id:
        keeper.host_user_id = donor.host_user_id
    if not keeper.guest_user_id and donor.guest_user_id:
        keeper.guest_user_id = donor.guest_user_id
    if not keeper.host_ip and donor.host_ip:
        keeper.host_ip = donor.host_ip
    if not keeper.guest_ip and donor.guest_ip:
        keeper.guest_ip = donor.guest_ip
    if donor.ranked and not keeper.ranked:
        keeper.ranked = True
        keeper.match_rank = donor.match_rank
    elif donor.ranked and keeper.ranked and not keeper.match_rank and donor.match_rank:
        keeper.match_rank = donor.match_rank
    if keeper.host_char is None and donor.host_char is not None:
        keeper.host_char = donor.host_char
    if keeper.guest_char is None and donor.guest_char is not None:
        keeper.guest_char = donor.guest_char
    if donor.source == "host" or (
        keeper.source in ("guest", "sync") and donor.ranked
    ):
        keeper.source = "host"
    elif keeper.source == "guest" and donor.source == "sync":
        keeper.source = "sync"
    keeper.played_at = _pick_played_at(keeper, donor)


def _fmt_match(m: db.Match) -> str:
    ts = _ts(m)
    ts_s = f"{ts:.0f}" if ts is not None else "?"
    return (
        f"{m.id[:8]}.. src={m.source} ranked={int(m.ranked)} "
        f"played_at={ts_s} h={m.host_user_id or '-'} g={m.guest_user_id or '-'}"
    )


async def _load_replay_map(session) -> dict[str, set[str]]:
    res = await session.execute(
        select(db.Replay.match_id, db.Replay.content_sha256)
    )
    out: dict[str, set[str]] = defaultdict(set)
    for match_id, sha in res.all():
        out[str(match_id)].add(str(sha))
    return out


async def run(*, apply: bool, limit: Optional[int]) -> int:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        print("DATABASE_URL is required", file=sys.stderr)
        return 1

    db.init_engine(url)
    delete_ids: list[str] = []
    merge_plans: list[tuple[str, list[str]]] = []

    try:
        async with db.session() as session:
            res = await session.execute(
                select(db.Match)
                .where(db.Match.winner != "")
                .order_by(db.Match.played_at.asc())
            )
            matches = list(res.scalars().all())
            replay_map = await _load_replay_map(session)

        clusters = _cluster_matches(matches)
        if limit is not None:
            clusters = clusters[:limit]

        print(
            f"Scanned {len(matches)} matches, found {len(clusters)} duplicate cluster(s)",
            flush=True,
        )
        if not clusters:
            return 0

        async with db.session() as session:
            for idx, group in enumerate(clusters, 1):
                scored = sorted(
                    group,
                    key=lambda m: _score(m, has_replay=bool(replay_map.get(m.id))),
                    reverse=True,
                )
                keeper = scored[0]
                donors = scored[1:]
                donor_ids = [d.id for d in donors]

                hp = keeper.host_profile
                gp = keeper.guest_profile
                print(
                    f"\n[{idx}] keep {_fmt_match(keeper)} "
                    f"profiles={hp}/{gp} winner={keeper.winner}"
                )
                for d in donors:
                    print(f"     drop {_fmt_match(d)}")

                merge_plans.append((keeper.id, donor_ids))
                delete_ids.extend(donor_ids)

                if not apply:
                    continue

                keeper_row = await session.get(db.Match, keeper.id)
                if keeper_row is None:
                    print(f"  skip: keeper {keeper.id} missing", file=sys.stderr)
                    continue

                keeper_shas = set(replay_map.get(keeper.id, set()))
                for donor in donors:
                    donor_row = await session.get(db.Match, donor.id)
                    if donor_row is None:
                        continue
                    _merge_fields(keeper_row, donor_row)

                    for sha in replay_map.get(donor.id, set()):
                        if sha in keeper_shas:
                            await session.execute(
                                delete(db.Replay).where(
                                    db.Replay.match_id == donor.id,
                                    db.Replay.content_sha256 == sha,
                                )
                            )
                        else:
                            res = await session.execute(
                                select(db.Replay).where(
                                    db.Replay.match_id == donor.id,
                                    db.Replay.content_sha256 == sha,
                                )
                            )
                            rep = res.scalar_one_or_none()
                            if rep is not None:
                                rep.match_id = keeper.id
                                keeper_shas.add(sha)

                    await session.delete(donor_row)

            if apply:
                await session.commit()
    finally:
        await db.dispose()

    print(
        f"\nSummary: {len(merge_plans)} cluster(s), "
        f"{len(delete_ids)} row(s) to delete"
    )
    if apply:
        print("Applied.")
    else:
        print("Dry-run only. Re-run with --apply to write changes.")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually merge and delete (default: dry-run)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Process at most N clusters (for testing)",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(apply=args.apply, limit=args.limit)))


if __name__ == "__main__":
    main()
