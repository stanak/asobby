"""Clock-independent match identity and durable, idempotent result ingestion.

Start announcements are paired only within an authenticated lobby connection.
Results never search the match history by profiles, winner or wall clock.
Pending reports are deliberately separate from the public/ranked matches table.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import trueskill
from sqlalchemy import select

import db

START_JOIN_SECONDS = 3
CORE_FIELDS = ("winner", "host_char", "guest_char", "host_profile", "guest_profile", "host_wins", "guest_wins")


def random_choice(reports: list[db.MatchReport], side: str) -> bool | None:
    """Prefer the participant's own observation; old clients omit this field.

    These flags are NOT result identity fields: actual fighters still must
    agree. Use the first persisted report, never a retry's changed selection.
    """
    own = next((r for r in reports if r.side == side), None)
    if own is not None and type(own.payload.get(f"{side}_random")) is bool:
        return own.payload[f"{side}_random"]
    values = {r.payload[f"{side}_random"] for r in reports
              if type(r.payload.get(f"{side}_random")) is bool}
    return values.pop() if len(values) == 1 else None


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def client_time(payload: dict) -> datetime | None:
    try:
        ts = float(payload.get("played_at") or 0)
        return datetime.fromtimestamp(ts, timezone.utc) if ts > 0 else None
    except (ValueError, TypeError, OverflowError, OSError):
        return None


async def lock_users(s, user_ids: list[str]):
    # A database lock, not merely a process-local asyncio.Lock: serializes starts,
    # result confirmation and ratings across workers. Always use a stable order.
    return list((await s.scalars(select(db.User).where(db.User.id.in_(user_ids)).order_by(db.User.id).with_for_update())).all())


async def announce(*, user_id: str, client_id: str, side: str, post_id: str = "",
                   host_user_id: str | None = None, guest_user_id: str | None = None,
                   host_profile: str = "", guest_profile: str = "", match_rank: str | None = None,
                   ended: bool = False, start_age_sec: float = 0) -> str | None:
    now = db.utcnow()
    started_at = now - timedelta(seconds=start_age_sec)
    async with db.session() as s, s.begin():
        await lock_users(s, sorted({x for x in (user_id, host_user_id, guest_user_id) if x}))
        ticket = await s.get(db.BattleTicket, (user_id, client_id))
        if ticket is not None:
            if ticket.side != side or ticket.host_profile != host_profile or ticket.guest_profile != guest_profile:
                raise ValueError("battle start ID reused with different players")
            if ended and ticket.closed_at is None:
                ticket.closed_at = now
            return ticket.match_id
        if ended:
            return None  # Never manufacture a start from a delayed end/retry.
        if start_age_sec > 30:
            return None  # A late first announcement is not reliable start evidence.

        # A new game closes this player's previous starts (including the peer).
        old = list((await s.scalars(select(db.BattleTicket).where(
            db.BattleTicket.user_id == user_id, db.BattleTicket.closed_at.is_(None)
        ))).all())
        for previous in old:
            siblings = (await s.scalars(select(db.BattleTicket).where(db.BattleTicket.match_id == previous.match_id))).all()
            for sibling in siblings:
                sibling.closed_at = now
        await s.flush()

        match_id = uuid4().hex
        if post_id and host_user_id and guest_user_id and host_user_id != guest_user_id:
            other_side = "guest" if side == "host" else "host"
            peer_uid = guest_user_id if side == "host" else host_user_id
            candidates = list((await s.scalars(select(db.BattleTicket).where(
                db.BattleTicket.user_id == peer_uid,
                db.BattleTicket.side == other_side,
                db.BattleTicket.post_id == post_id,
                db.BattleTicket.host_user_id == host_user_id,
                (db.BattleTicket.guest_user_id == guest_user_id) | db.BattleTicket.guest_user_id.is_(None),
                db.BattleTicket.host_profile == host_profile,
                db.BattleTicket.guest_profile == guest_profile,
                db.BattleTicket.closed_at.is_(None),
                db.BattleTicket.started_at >= started_at - timedelta(seconds=START_JOIN_SECONDS),
                db.BattleTicket.started_at <= started_at + timedelta(seconds=START_JOIN_SECONDS),
            ))).all())
            if len(candidates) == 1:
                peer = candidates[0]
                occupied = await s.scalar(select(db.BattleTicket).where(db.BattleTicket.match_id == peer.match_id, db.BattleTicket.side == side))
                if occupied is None:
                    match_id = peer.match_id
                    peer.guest_user_id = guest_user_id
        s.add(db.BattleTicket(
            user_id=user_id, client_id=client_id, match_id=match_id, side=side,
            post_id=post_id, host_user_id=host_user_id, guest_user_id=guest_user_id,
            host_profile=host_profile, guest_profile=guest_profile, match_rank=match_rank,
            started_at=started_at,
        ))
        return match_id


def response(report: db.MatchReport, *, created: bool = False, ranked: bool = False) -> dict:
    confirmed = report.status == "confirmed"
    return {"ok": True, "accepted": True, "recorded": confirmed,
            "status": report.status, "reason": report.reason,
            "match_id": report.match_id, "ranked": ranked,
            "duplicate": confirmed and not created, "newly_recorded": created}


async def quarantine(user_id: str, side: str, payload: dict, reason: str, client_id: str = "") -> dict:
    """Old/ambiguous reports are acknowledged durably, never inserted into stats."""
    key = client_id or hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    async with db.session() as s, s.begin():
        await lock_users(s, [user_id])
        report = await s.get(db.MatchReport, (user_id, key))
        if report is None:
            report = db.MatchReport(user_id=user_id, client_id=key, side=side,
                payload=payload, client_played_at=client_time(payload), received_at=db.utcnow(),
                status="pending", reason=reason)
            s.add(report)
        return response(report)


async def _rate(s, match: db.Match, users: dict[str, db.User]) -> None:
    """Apply Ph ratings in the SAME transaction as the unique match insertion."""
    host, guest = users[match.host_user_id], users[match.guest_user_id]
    host.rank_locked = guest.rank_locked = True
    if host.rank != "ph" or guest.rank != "ph" or match.host_char not in db.PH_CHAR_IDS or match.guest_char not in db.PH_CHAR_IDS:
        return
    selected = []
    for user, char in ((host, match.host_char), (guest, match.guest_char)):
        ratings = list((await s.scalars(select(db.UserCharRating).where(db.UserCharRating.user_id == user.id))).all())
        by_char = {r.char_id: r for r in ratings}
        for cid in db.PH_CHAR_IDS:
            if cid not in by_char:
                by_char[cid] = db.UserCharRating(user_id=user.id, char_id=cid, ts_mu=user.ts_mu, ts_sigma=user.ts_sigma)
                s.add(by_char[cid])
        selected.append((user, by_char[char], by_char))
    hr, gr = (trueskill.Rating(mu=x[1].ts_mu, sigma=x[1].ts_sigma) for x in selected)
    if match.winner == "guest":
        gr, hr = trueskill.rate_1vs1(gr, hr)
    else:
        hr, gr = trueskill.rate_1vs1(hr, gr, drawn=match.winner == "draw")
    for (user, rating, all_ratings), new in zip(selected, (hr, gr)):
        rating.ts_mu, rating.ts_sigma = new.mu, new.sigma
        user.ts_mu = sum(r.ts_mu for r in all_ratings.values()) / len(all_ratings)
        user.ts_sigma = sum(r.ts_sigma for r in all_ratings.values()) / len(all_ratings)


async def submit(*, user_id: str, client_id: str, match_id: str, side: str, payload: dict,
                 ranked_limit: int = 5, gap_minutes: int = 30) -> dict:
    async with db.session() as s:
        ticket = await s.get(db.BattleTicket, (user_id, client_id))
    if ticket is None:
        return await quarantine(user_id, side, payload, "missing_battle_start", client_id)
    if ticket.side != side or (match_id and ticket.match_id != match_id):
        raise ValueError("match ID does not belong to this report")
    now = db.utcnow()
    async with db.session() as s, s.begin():
        users = {u.id: u for u in await lock_users(s, sorted({x for x in (user_id, ticket.host_user_id, ticket.guest_user_id) if x}))}
        # Re-read after acquiring the pair lock; the peer may just have joined.
        ticket = await s.get(db.BattleTicket, (user_id, client_id), populate_existing=True)
        ticket.closed_at = ticket.closed_at or now
        report = await s.get(db.MatchReport, (user_id, client_id))
        if report is not None:
            changed = any(report.payload.get(k) != payload.get(k) for k in CORE_FIELDS)
            if changed:
                report.conflict_payload = payload
                # Never rewrite an already confirmed result based on a changed retry.
                if report.status != "confirmed":
                    report.status, report.reason = "conflict", "changed_result"
                return {**response(report), "status": "conflict", "reason": "changed_result", "recorded": False}
            if report.status == "confirmed":
                match = await s.get(db.Match, ticket.match_id)
                return response(report, ranked=bool(match and match.ranked))
        else:
            report = db.MatchReport(user_id=user_id, client_id=client_id, match_id=ticket.match_id,
                side=side, payload=payload, client_played_at=client_time(payload), received_at=now,
                status="pending", reason="waiting_for_peer")
            s.add(report)
        if report.match_id is None:
            report.match_id = ticket.match_id
        if (payload.get("host_profile", "") != ticket.host_profile or payload.get("guest_profile", "") != ticket.guest_profile):
            report.status, report.reason = "conflict", "players_changed"
        await s.flush()
        tickets = list((await s.scalars(select(db.BattleTicket).where(db.BattleTicket.match_id == ticket.match_id))).all())
        reports = list((await s.scalars(select(db.MatchReport).where(db.MatchReport.match_id == ticket.match_id))).all())
        if any(r.status == "conflict" for r in reports):
            for r in reports:
                r.status, r.reason = "conflict", "result_disagreement"
            return response(report)
        expected_peer = ticket.guest_user_id if side == "host" else ticket.host_user_id
        if len(tickets) < 2 and expected_peer:
            report.reason = "missing_peer_start"
            return response(report)
        if len(tickets) == 2 and len(reports) < 2:
            return response(report)
        if len(reports) == 2 and any(reports[0].payload.get(k) != reports[1].payload.get(k) for k in CORE_FIELDS):
            for r in reports:
                r.status, r.reason = "conflict", "result_disagreement"
            return response(report)

        # Start receipt + monotonic battle duration, not the PC wall clock.
        # Delayed sync MUST NOT turn the time of receipt into the time played.
        times = []
        for r in reports:
            origin = next(t for t in tickets if t.user_id == r.user_id)
            duration = r.payload.get("duration_sec")
            if duration is None or not (0 <= duration <= 7200):
                r.reason = "missing_battle_duration"
                return response(report)
            ended_at = utc(origin.started_at) + timedelta(seconds=duration)
            if ended_at > now + timedelta(seconds=10):
                r.reason = "invalid_battle_duration"
                return response(report)
            times.append(min(ended_at, now))
        played_at = min(times)
        host_ticket = next((t for t in tickets if t.side == "host"), None)
        rank = host_ticket.match_rank if host_ticket else None
        host_id, guest_id = ticket.host_user_id, ticket.guest_user_id
        ranked = bool(len(tickets) == 2 and rank and host_id in users and guest_id in users
                      and users[host_id].rank == rank and users[guest_id].rank == rank)
        if ranked:
            base_query = select(db.Match).where(
                db.Match.ranked.is_(True), db.Match.match_rank == rank,
                (db.Match.host_user_id.in_((host_id, guest_id))) | (db.Match.guest_user_id.in_((host_id, guest_id))),
            )
            previous = list((await s.scalars(base_query.where(db.Match.played_at < played_at)
                           .order_by(db.Match.played_at.desc()).limit(ranked_limit + 1))).all())
            following = list((await s.scalars(base_query.where(db.Match.played_at >= played_at)
                            .order_by(db.Match.played_at).limit(ranked_limit + 1))).all())
            # Delayed reports may arrive in reverse order. Count the connected
            # session on BOTH sides of the battle time, not just earlier rows.
            count = 0
            for neighbors in (previous, following):
                last = played_at
                for m in neighbors:
                    if {m.host_user_id, m.guest_user_id} != {host_id, guest_id} or abs((last - utc(m.played_at)).total_seconds()) > gap_minutes * 60:
                        break
                    count += 1
                    last = utc(m.played_at)
            ranked = count < ranked_limit
        host_random, guest_random = (random_choice(reports, side) for side in ("host", "guest"))
        host_actual, guest_actual = payload.get("host_char"), payload.get("guest_char")
        # An observed Random selection is meaningful only with a real fighter.
        host_random = host_random if type(host_actual) is int and 0 <= host_actual < 20 else None
        guest_random = guest_random if type(guest_actual) is int and 0 <= guest_actual < 20 else None
        match = db.Match(id=ticket.match_id, host_user_id=host_id, guest_user_id=guest_id,
            winner=payload["winner"], host_char=20 if host_random else host_actual,
            guest_char=20 if guest_random else guest_actual,
            host_actual_char=host_actual, guest_actual_char=guest_actual,
            host_random=host_random, guest_random=guest_random,
            host_profile=ticket.host_profile, guest_profile=ticket.guest_profile,
            host_wins=payload.get("host_wins"), guest_wins=payload.get("guest_wins"),
            ranked=ranked, match_rank=rank if ranked else None,
            source="host" if host_ticket else "guest", played_at=played_at)
        s.add(match)
        if ranked:
            await _rate(s, match, users)
        for r in reports:
            r.status, r.reason = "confirmed", ""
        await s.flush()  # Unique primary key + transaction also protect rank effects.
        return response(report, created=True, ranked=ranked)
