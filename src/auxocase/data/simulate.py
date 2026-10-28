"""Replay a month of life with the system in place, on the mock.

Time moves in six-hour ticks. Each tick: the tick's new leads arrive (the
generator's distributions, creation events queued); the scorer reads the
change feed and scores what entered the pool; routing fills whatever SDR
slots are free; the SDRs spend the team's capacity for the tick (about 500
touches a month across 12 reps, as in the brief), each rep taking the
lead in their own queue worth most now (routing.priority), unless one has
waited past two days. Outcomes follow the generator's hidden truth, including BrightPath's
answer 4: a lead touched within a day books about twice as often. A little
of the old habit survives (a rep skips some individual-contributor leads).

At the end of the month the loop syncs outcomes, reads new loss reasons,
runs the monitor and trains a candidate, as the scheduled job would. Calendar
hours, no nights or weekends: the point is the queueing, not the timetable.
"""
from __future__ import annotations

import random
import sqlite3
from datetime import datetime, timedelta

from ..feedback import loop as fb
from ..hubspot.mock import MockHubSpot
from ..hubspot.models import Contact
from ..llm.client import make_llm
from ..routing.engine import Router, priority
from ..scoring.pipeline import Scorer, load_live_model
from ..storage.db import clock, now_iso, set_setting
from .generate import (CLOSE_DAYS, WORKED_PER_MONTH, add_leads_between, loss_reason_text, touch_factor,
                       true_close_probability, true_demo_probability, truth_for)

TICK_HOURS = 6
SLA_JUMP_HOURS = 48          # a lead waiting this long in a rep's queue is their next touch, whatever its rank


def _queues(conn: sqlite3.Connection) -> dict[str, list[dict]]:
    """Each rep's untouched assigned leads (mock tables: this module only runs on the mock)."""
    rows = conn.execute(
        "SELECT c.id, c.hubspot_owner_id owner, COALESCE(c.auxo_probability, 0) p, c.createdate created, r.assigned_at FROM hs_contacts c "
        "JOIN (SELECT contact_id, MAX(created_at) assigned_at FROM routing_log WHERE decision IN ('assigned','holdout_assigned') GROUP BY contact_id) r "
        "ON r.contact_id = c.id WHERE c.hubspot_owner_id IS NOT NULL AND c.hs_lead_status IN ('NEW','OPEN') "
        "AND c.lifecycle_stage IN ('marketingqualifiedlead','salesqualifiedlead')").fetchall()
    out: dict[str, list[dict]] = {}
    for r in rows:
        out.setdefault(r["owner"], []).append(dict(r))
    return out


def _next_touches(conn: sqlite3.Connection, k: int, now: datetime, rng: random.Random) -> list[tuple[str, str]]:
    """Up to k (contact, owner) touches, spread across reps: overdue first, then highest likelihood."""
    overdue = (now - timedelta(hours=SLA_JUMP_HOURS)).isoformat()
    queues = {o: sorted(q, key=lambda r: (r["assigned_at"] > overdue, -priority(conn, r["p"], datetime.fromisoformat(r["created"]), now)))
              for o, q in _queues(conn).items()}
    reps = [o for o in queues if queues[o]]
    rng.shuffle(reps)
    out: list[tuple[str, str]] = []
    while len(out) < k and any(queues[o] for o in reps):
        for o in reps:
            if len(out) >= k:
                break
            if queues[o]:
                out.append((queues[o].pop(0)["id"], o))
    return out


def _work(hs: MockHubSpot, conn: sqlite3.Connection, c: Contact, owner_id: str, at: datetime, rng: random.Random,
          tally: dict) -> None:
    co = hs.get_company(c.company_id) if c.company_id else None
    truth = truth_for(conn, c.id)
    size = (truth or {}).get("num_employees") or (co.num_employees if co and co.num_employees else 50)
    if c.seniority == "individual" and rng.random() < 0.25 and not c.auxo_holdout:
        conn.execute("INSERT INTO overrides(contact_id, owner_id, action, note, score_at_time, rank_at_time, created_at) VALUES(?,?,?,?,?,?,?)",
                     (c.id, owner_id, "skipped", "wrong fit", c.auxo_score, c.auxo_rank, now_iso()))
        hs.simulate_sdr_activity(c.id, owner_id, "junk", at)
        tally["skipped_by_rep"] += 1
        return
    hs.simulate_sdr_activity(c.id, owner_id, "worked", at)
    tally["worked"] += 1
    delay_days = (at - c.createdate).total_seconds() / 86400
    tally["touched_within_24h"] += int(delay_days <= 1)
    if rng.random() >= true_demo_probability(c, co, rng, truth) * touch_factor(delay_days):
        hs.simulate_sdr_activity(c.id, owner_id, "junk", at + timedelta(days=rng.randint(1, 7)))
        return
    demo_at = at + timedelta(days=rng.randint(1, 5))
    amount = round(size * 160, -2)
    hs.simulate_sdr_activity(c.id, owner_id, "demo_booked", demo_at, amount=amount)
    tally["booked"] += 1
    close_at = demo_at + timedelta(days=rng.randint(*CLOSE_DAYS))
    if rng.random() < true_close_probability(c, co, rng, truth):
        hs.simulate_sdr_activity(c.id, owner_id, "closed_won", close_at, amount=amount)
        tally["won"] += 1
    else:
        hs.simulate_sdr_activity(c.id, owner_id, "closed_lost", close_at,
                                 loss_reason=loss_reason_text(rng, size, co.uses_payroll_vendor if co else ""))


def simulate_month(conn: sqlite3.Connection, hs: MockHubSpot, seed: int = 101, new_leads: int = 6000,
                   as_of: datetime | None = None, days: int = 30) -> dict:
    rng = random.Random(seed)
    start = as_of or clock(conn)        # the data clock: where the last replayed month (or the history) ended
    ticks = days * 24 // TICK_HOURS
    per_tick_leads = new_leads / ticks
    per_tick_touches = WORKED_PER_MONTH * TICK_HOURS / (30.4 * 24)
    scorer = Scorer(hs, conn, load_live_model(conn), make_llm())
    router = Router(hs, conn, seed=seed)
    tally = {"new_leads": 0, "scored": 0, "assigned": 0, "held": 0, "worked": 0, "skipped_by_rep": 0,
             "touched_within_24h": 0, "booked": 0, "won": 0}
    credit = 0.0
    for i in range(ticks):
        t0 = start + timedelta(hours=TICK_HOURS * i)
        t1 = t0 + timedelta(hours=TICK_HOURS)
        n = int(per_tick_leads) + (1 if rng.random() < per_tick_leads - int(per_tick_leads) else 0)
        tally["new_leads"] += add_leads_between(conn, hs, n, t0, t1, seed=seed * 10_007 + i)
        set_setting(conn, "clock", t1.isoformat())
        tally["scored"] += len(scorer.process_events(max_events=10_000))
        for d in router.run(now=t1):
            tally["assigned"] += d.decision in ("assigned", "holdout_assigned")
            tally["held"] += d.decision == "held"
        credit += per_tick_touches
        k, credit = int(credit), credit - int(credit)
        for contact_id, owner_id in _next_touches(conn, k, t1, rng):
            c = hs.get_contact(contact_id)
            if c is not None:
                _work(hs, conn, c, owner_id, t1 + timedelta(minutes=rng.randrange(TICK_HOURS * 60)), rng, tally)

    end = start + timedelta(days=days)
    set_setting(conn, "clock", end.isoformat())
    scorer.refresh_ranks()
    sync = fb.sync_outcomes(hs, conn)
    losses = fb.classify_losses(hs, conn, make_llm())
    monitor = fb.channel_monitor(hs, conn, as_of=end)
    flags = {f"{p.period}/{p.channel}": p.flag for p in monitor if p.flag not in ("ok", "held")}
    try:
        model, m = fb.train_candidate(hs, conn, as_of=end, notes=f"simulated month, seed {seed}")
        candidate = {"version": model.version, "precision_at_k": m.precision_at_k, "lift": m.lift_at_k, "ece": m.ece}
    except ValueError as e:
        candidate = {"error": str(e)}
    return {"month": start.strftime("%Y-%m-%d"), **tally, "sync": sync, "losses_read": losses,
            "monitor_flags": flags, "candidate": candidate}
