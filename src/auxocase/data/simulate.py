"""Replay a month of life with the system in place, on the mock.

Each simulated month: new leads arrive (the generator's distributions),
the service scores and routes them, SDRs work what is in their queue (with a
small amount of the old cherry-picking left in: a rep skips some low-seniority
leads), outcomes follow the generator's hidden true propensities, the loop
syncs outcomes, runs the monitor and trains a candidate. This is how the
feedback loop is exercised end to end without a real HubSpot, and how the
exploration slice's labels accumulate.

The queue carries over: a month starts by filling the free slots, the reps
clear everything in their queues, and the slots are filled again for the month
after. With 12 reps and 45 open leads each that is about 540 leads worked a
month, the brief's "about 500".
"""
from __future__ import annotations

import random
import sqlite3
import time
from datetime import datetime, timedelta

from ..feedback import loop as fb
from ..hubspot.mock import MockHubSpot
from ..hubspot.models import OPEN_LEAD_STATUSES, Contact
from ..llm.client import make_llm
from ..routing.engine import Router
from ..scoring.pipeline import Scorer, load_live_model
from ..storage.db import clock, now_iso, set_setting

try:  # the generator owns the hidden truth; fall back to a plain proxy if it isn't exposed
    from .generate import true_close_probability, true_demo_probability
except ImportError:  # pragma: no cover
    def true_demo_probability(contact, company, rng):
        base = 0.3 + (0.25 if contact.channel == "referral" else 0) + (0.2 if contact.webinar_attended else 0)
        return min(0.95, base)

    def true_close_probability(contact, company, rng):
        return 0.2


def open_queue(hs: MockHubSpot) -> list[Contact]:
    """What the reps have in hand: owned, still a lead (not yet an opportunity), status open."""
    return [c for c in hs.list_contacts()
            if c.hubspot_owner_id and c.lifecycle_stage in ("marketingqualifiedlead", "salesqualifiedlead")
            and c.hs_lead_status in OPEN_LEAD_STATUSES]


def simulate_month(conn: sqlite3.Connection, hs: MockHubSpot, seed: int = 101, new_leads: int = 6000,
                   as_of: datetime | None = None) -> dict:
    from .generate import add_month_of_leads
    rng = random.Random(seed)
    as_of = as_of or clock(conn)        # the data clock: the first day of the month being replayed
    seconds: dict[str, float] = {}
    t = time.perf_counter()

    def lap(name: str) -> None:
        nonlocal t
        seconds[name] = round(time.perf_counter() - t, 1)
        t = time.perf_counter()

    added = add_month_of_leads(conn, hs, n=new_leads, start=as_of, seed=seed)
    lap("new_leads")

    scorer = Scorer(hs, conn, load_live_model(conn), make_llm())
    scored = scorer.rescore_pool()
    lap("score")
    router = Router(hs, conn, seed=seed)
    decisions = router.run()                       # fill whatever slots are free at the start of the month
    lap("route")

    worked = booked = won = skipped = 0
    for c in open_queue(hs):
        co = hs.get_company(c.company_id) if c.company_id else None
        owner_id = c.hubspot_owner_id
        # A little of the old habit survives: reps skip some individual-contributor leads unless the reason is strong.
        if c.seniority == "individual" and rng.random() < 0.25 and not c.auxo_holdout:
            conn.execute("INSERT INTO overrides(contact_id, owner_id, action, note, score_at_time, rank_at_time, created_at) VALUES(?,?,?,?,?,?,?)",
                         (c.id, owner_id, "skipped", "wrong fit", c.auxo_score, c.auxo_rank, now_iso()))
            hs.simulate_sdr_activity(c.id, owner_id, "junk", as_of + timedelta(days=rng.randint(1, 20)))
            skipped += 1
            continue
        touch_delay_days = rng.choice([0, 0, 0, 1, 1, 2, 3, 6])
        at = as_of + timedelta(days=rng.randint(0, 20) + touch_delay_days)
        p_demo = true_demo_probability(c, co, rng) * (0.7 if touch_delay_days >= 5 else 1.0)
        worked += 1
        if rng.random() < p_demo:
            amount = round(((co.num_employees if co else None) or 50) * 160, -2)
            hs.simulate_sdr_activity(c.id, owner_id, "demo_booked", at + timedelta(days=2), amount=amount)
            booked += 1
            if rng.random() < true_close_probability(c, co, rng):
                hs.simulate_sdr_activity(c.id, owner_id, "closed_won", at + timedelta(days=rng.randint(30, 120)), amount=amount)
                won += 1
            else:
                hs.simulate_sdr_activity(c.id, owner_id, "closed_lost", at + timedelta(days=rng.randint(30, 120)))
        else:
            hs.simulate_sdr_activity(c.id, owner_id, "junk", at)
        conn.execute("INSERT INTO outcomes(contact_id, worked, first_touch_at, updated_at) VALUES(?,?,?,?) ON CONFLICT(contact_id) DO UPDATE SET worked=1, first_touch_at=COALESCE(outcomes.first_touch_at, excluded.first_touch_at), updated_at=excluded.updated_at",
                     (c.id, 1, at.isoformat(), now_iso()))
    lap("work")
    refill = router.run()                          # the queues are empty again: fill them for next month
    decisions += refill
    lap("refill")

    sync = fb.sync_outcomes(hs, conn)
    lap("sync")
    monitor = fb.channel_monitor(hs, conn, as_of=as_of + timedelta(days=31))
    flags = {f"{p.period}/{p.channel}": p.flag for p in monitor if p.flag not in ("ok", "held")}
    lap("monitor")
    candidate = None
    try:
        model, m = fb.train_candidate(hs, conn, as_of=as_of + timedelta(days=31), notes=f"simulated month, seed {seed}")
        candidate = {"version": model.version, "precision_at_k": m.precision_at_k, "lift": m.lift_at_k, "ece": m.ece}
    except ValueError as e:
        candidate = {"error": str(e)}
    lap("train")
    set_setting(conn, "clock", (as_of + timedelta(days=31)).isoformat())   # the month is over
    assigned = sum(1 for d in decisions if d.decision in ("assigned", "holdout_assigned"))
    return {"month": as_of.strftime("%Y-%m"), "new_leads": added, "scored": len(scored), "assigned": assigned,
            "worked": worked, "skipped_by_rep": skipped, "booked": booked, "won": won, "sync": sync,
            "monitor_flags": flags, "candidate": candidate, "seconds": seconds}
