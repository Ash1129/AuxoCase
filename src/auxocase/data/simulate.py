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

With phase 3 switched on, each tick also runs the automated band: leads
below the line get the sequence, a lead replies (or not) by the generator's
`TIER2_REPLY_FACTOR` of its hidden demo chance, a few days later, and the
reply hands it to an SDR, whose touch then books at `TIER2_DEMO_GIVEN_REPLY`.
Those touches come out of the same 500 a month: the band spends SDR time on
replies, it does not add any.

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
from ..costs import unit
from ..routing.tier2 import detect_replies, run_tier2, send_share
from ..scoring.pipeline import Scorer, load_live_model
from ..storage.db import clock, now_iso, set_setting
from .generate import (CLOSE_DAYS, TIER2_DEMO_GIVEN_REPLY, TIER2_REPLY_DAYS, TIER2_REPLY_FACTOR, WORKED_PER_MONTH,
                       add_leads_between, loss_reason_text, touch_factor, true_close_probability, true_demo_probability,
                       truth_for)

TICK_HOURS = 6
SLA_JUMP_HOURS = 48          # a lead waiting this long in a rep's queue is their next touch, whatever its rank


def _queues(conn: sqlite3.Connection) -> dict[str, list[dict]]:
    """Each rep's untouched assigned leads (mock tables: this module only runs on the mock)."""
    # A lead that replied to the automated sequence is warm: its old below-line score is no longer what touching it
    # is worth, and the rep answers it first (the queue page shows it the same way).
    rows = conn.execute(
        "SELECT c.id, c.hubspot_owner_id owner, CASE WHEN t.status = 'replied' THEN ? ELSE COALESCE(c.auxo_probability, 0) END p, "
        "t.status = 'replied' replied, c.createdate created, r.assigned_at FROM hs_contacts c "
        "JOIN (SELECT contact_id, MAX(created_at) assigned_at FROM routing_log WHERE decision IN ('assigned','holdout_assigned') GROUP BY contact_id) r "
        "ON r.contact_id = c.id LEFT JOIN tier2 t ON t.contact_id = c.id WHERE c.hubspot_owner_id IS NOT NULL AND c.hs_lead_status IN ('NEW','OPEN') "
        "AND c.lifecycle_stage IN ('marketingqualifiedlead','salesqualifiedlead')", (TIER2_DEMO_GIVEN_REPLY,)).fetchall()
    out: dict[str, list[dict]] = {}
    for r in rows:
        out.setdefault(r["owner"], []).append(dict(r))
    return out


def _next_touches(conn: sqlite3.Connection, budget: float, now: datetime, rng: random.Random,
                  reply_cost: float = 1.0) -> tuple[list[tuple[str, str]], float]:
    """(contact, owner) touches worth up to `budget` cold touches, spread across reps: overdue first, then highest
    likelihood. Answering a reply to the automated sequence costs `reply_cost` of a cold touch (the cost settings'
    reply minutes over touch minutes), so the simulation spends SDR time the way the cost report prices it. Returns
    the touches and the budget left over."""
    overdue = (now - timedelta(hours=SLA_JUMP_HOURS)).isoformat()
    queues = {o: sorted(q, key=lambda r: (r["assigned_at"] > overdue, -priority(conn, r["p"], datetime.fromisoformat(r["created"]), now)))
              for o, q in _queues(conn).items()}
    reps = [o for o in queues if queues[o]]
    rng.shuffle(reps)
    out: list[tuple[str, str]] = []
    while any(queues[o] for o in reps):
        took = False
        for o in reps:
            if not queues[o]:
                continue
            cost = reply_cost if queues[o][0]["replied"] else 1.0
            if cost > budget + 1e-9:
                continue
            r = queues[o].pop(0)
            out.append((r["id"], o))
            budget -= cost
            took = True
        if not took:
            break
    return out, budget


def _replied_at(conn: sqlite3.Connection, contact_id: str) -> datetime | None:
    r = conn.execute("SELECT replied_at FROM tier2 WHERE contact_id=? AND status='replied'", (contact_id,)).fetchone()
    return datetime.fromisoformat(r["replied_at"]) if r and r["replied_at"] else None


def _work(hs: MockHubSpot, conn: sqlite3.Connection, c: Contact, owner_id: str, at: datetime, rng: random.Random,
          tally: dict) -> None:
    co = hs.get_company(c.company_id) if c.company_id else None
    truth = truth_for(conn, c.id)
    size = (truth or {}).get("num_employees") or (co.num_employees if co and co.num_employees else 50)
    replied = _replied_at(conn, c.id)
    if c.seniority == "individual" and rng.random() < 0.25 and not c.auxo_holdout and replied is None:
        conn.execute("INSERT INTO overrides(contact_id, owner_id, action, note, score_at_time, rank_at_time, created_at) VALUES(?,?,?,?,?,?,?)",
                     (c.id, owner_id, "skipped", "wrong fit", c.auxo_score, c.auxo_rank, now_iso()))
        hs.simulate_sdr_activity(c.id, owner_id, "junk", at)
        tally["skipped_by_rep"] += 1
        return
    hs.simulate_sdr_activity(c.id, owner_id, "worked", at)
    tally["worked"] += 1
    delay_days = (at - c.createdate).total_seconds() / 86400
    tally["touched_within_24h"] += int(delay_days <= 1)
    if replied is not None:     # a warm lead: the chance is the reply's, discounted if the SDR let the reply wait
        p = TIER2_DEMO_GIVEN_REPLY * touch_factor((at - replied).total_seconds() / 86400)
    else:
        p = true_demo_probability(c, co, rng, truth) * touch_factor(delay_days)
    if rng.random() >= p:
        hs.simulate_sdr_activity(c.id, owner_id, "junk", at + timedelta(days=rng.randint(1, 7)))
        return
    demo_at = at + timedelta(days=rng.randint(1, 5))
    amount = round(size * 160, -2)
    hs.simulate_sdr_activity(c.id, owner_id, "demo_booked", demo_at, amount=amount)
    tally["booked"] += 1
    tally["tier2_booked"] += int(replied is not None)
    close_at = demo_at + timedelta(days=rng.randint(*CLOSE_DAYS))
    if rng.random() < true_close_probability(c, co, rng, truth):
        hs.simulate_sdr_activity(c.id, owner_id, "closed_won", close_at, amount=amount)
        tally["won"] += 1
    else:
        hs.simulate_sdr_activity(c.id, owner_id, "closed_lost", close_at,
                                 loss_reason=loss_reason_text(rng, size, co.uses_payroll_vendor if co else ""))


def _automated_band(conn: sqlite3.Connection, hs: MockHubSpot, llm, now: datetime, rng: random.Random, tally: dict,
                    pending: list[tuple[datetime, str, str]]) -> None:
    """Phase 3 on the mock: the band's pass, the replies the generator decides on as each email goes out, and the
    replies due this tick landing in HubSpot and handing their leads to SDRs."""
    before = {r["contact_id"] for r in conn.execute("SELECT contact_id FROM tier2 WHERE status='sent'")}
    out = run_tier2(hs, conn, llm, now=now, rng=rng)
    tally["tier2_emailed"] += out["emailed"]
    tally["tier2_control"] += out["control"]
    for r in conn.execute("SELECT contact_id, subject FROM tier2 WHERE status='sent'").fetchall():
        if r["contact_id"] in before:
            continue
        c = hs.get_contact(r["contact_id"])
        co = hs.get_company(c.company_id) if c and c.company_id else None
        if c and rng.random() < true_demo_probability(c, co, rng, truth_for(conn, c.id)) * TIER2_REPLY_FACTOR:
            pending.append((now + timedelta(days=rng.uniform(*TIER2_REPLY_DAYS)), c.id, r["subject"] or ""))
    due = [p for p in pending if p[0] <= now]
    for when, contact_id, subject in due:
        pending.remove((when, contact_id, subject))
        hs.log_engagement(contact_id, "EMAIL", "INCOMING", when, None, subject=f"Re: {subject}")
    if due:
        tally["tier2_replied"] += detect_replies(hs, conn, now=now)["replied"]


def simulate_month(conn: sqlite3.Connection, hs: MockHubSpot, seed: int = 101, new_leads: int = 6000,
                   as_of: datetime | None = None, days: int = 30) -> dict:
    rng = random.Random(seed)
    start = as_of or clock(conn)        # the data clock: where the last replayed month (or the history) ended
    ticks = days * 24 // TICK_HOURS
    per_tick_leads = new_leads / ticks
    per_tick_touches = WORKED_PER_MONTH * TICK_HOURS / (30.4 * 24)
    reply_cost = unit(conn, "sdr_minutes_reply") / unit(conn, "sdr_minutes_touch")
    scorer = Scorer(hs, conn, load_live_model(conn), make_llm(conn=conn))
    router = Router(hs, conn, seed=seed)
    tally = {"new_leads": 0, "scored": 0, "assigned": 0, "held": 0, "worked": 0, "skipped_by_rep": 0,
             "touched_within_24h": 0, "booked": 0, "won": 0,
             "tier2_emailed": 0, "tier2_control": 0, "tier2_replied": 0, "tier2_booked": 0}
    credit = 0.0
    llm = make_llm(conn=conn)
    pending_replies: list[tuple[datetime, str, str]] = []      # (when, contact, subject): replies the generator decided on
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
        if send_share(conn) > 0:
            _automated_band(conn, hs, llm, t1, rng, tally, pending_replies)
        credit += per_tick_touches
        touches, credit = _next_touches(conn, credit, t1, rng, reply_cost)
        for contact_id, owner_id in touches:
            c = hs.get_contact(contact_id)
            if c is not None:
                _work(hs, conn, c, owner_id, t1 + timedelta(minutes=rng.randrange(TICK_HOURS * 60)), rng, tally)

    end = start + timedelta(days=days)
    set_setting(conn, "clock", end.isoformat())
    scorer.refresh_ranks()
    sync = fb.sync_outcomes(hs, conn)
    losses = fb.classify_losses(hs, conn, make_llm(conn=conn))
    monitor = fb.channel_monitor(hs, conn, as_of=end)
    flags = {f"{p.period}/{p.channel}": p.flag for p in monitor if p.flag not in ("ok", "held")}
    try:
        model, m = fb.train_candidate(hs, conn, as_of=end, notes=f"simulated month, seed {seed}")
        candidate = {"version": model.version, "precision_at_k": m.precision_at_k, "lift": m.lift_at_k, "ece": m.ece}
    except ValueError as e:
        candidate = {"error": str(e)}
    return {"month": start.strftime("%Y-%m-%d"), **tally, "sync": sync, "losses_read": losses,
            "monitor_flags": flags, "candidate": candidate}
