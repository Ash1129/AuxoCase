"""Held & overdue: the sales manager's page.

Two lists. Held: strong leads no SDR had room for, best first, each placed by hand on a named person's say.
Overdue: leads AuxoCase assigned that still wait on their SDR for a first touch past the SLA, and the ones next to
pass it, each with both clocks: since assignment (what the SLA counts) and since arrival (what bookings follow).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from ...data.generate import LATE_TOUCH_FACTOR
from ...feedback.loop import speed_effect
from ...hubspot.mock import MockHubSpot
from ...hubspot.models import SDR_LIFECYCLE_STAGES, WAITING_LEAD_STATUSES, Contact
from ...pool import max_age_days, pool_contacts
from ...routing.engine import DEFAULT_FIRST_TOUCH_SLA_HOURS
from ...storage.db import clock, get_setting, loads
from ..scheduler import jobs_from_env
from .lead import FIRST_DAY_HOURS, SMALL_BAND, WARN_AFTER_HOURS

SHOWN = 10            # held leads listed before "show the other N"
NEXT_SHOWN = 5        # waiting leads listed as the next to pass the SLA


def _when(dt: datetime) -> str:
    """'30 Oct 06:00'."""
    return f"{dt.day} {dt:%b %H:%M}"


def _span(hours: float) -> str:
    """'18 h', '22 h 22 m', '19 days'. Rounded down, so a clock never reads later than it is."""
    hours = max(0.0, hours)
    if hours >= 48:
        return f"{int(hours // 24)} days"
    h, m = int(hours), int((hours % 1) * 60)
    return f"{h} h {m} m" if m else f"{h} h"


def _hours(a: datetime, b: datetime) -> float:
    return (b - a).total_seconds() / 3600


def _months(months: list[str]) -> str:
    """['2028-05', '2028-06'] -> 'May and June 2028'."""
    ds = [datetime.strptime(m, "%Y-%m") for m in months]
    if not ds:
        return ""
    if len({d.year for d in ds}) == 1:
        names = [f"{d:%B}" for d in ds]
        return (", ".join(names[:-1]) + " and " + names[-1] if len(names) > 1 else names[0]) + f" {ds[0].year}"
    names = [f"{d:%B %Y}" for d in ds]
    return ", ".join(names[:-1]) + " and " + names[-1]


def _who(c: Optional[Contact], contact_id: str) -> dict:
    """name ('Jordan Ali'), label ('Jordan Ali, Operations Manager') and source ('website', 'referral via Northstar',
    'event: SHRM Annual') for one lead."""
    if c is None:
        return {"name": f"Contact #{contact_id}", "label": f"Contact #{contact_id}", "source": ""}
    name = " ".join(x for x in (c.first_name, c.last_name) if x) or f"Contact #{c.id}"
    if c.channel == "referral" and c.referral_partner:
        source = f"referral via {c.referral_partner}"
    elif c.channel == "events" and (c.hs_analytics_source_data_1 or "").startswith("Event: "):
        source = "event: " + c.hs_analytics_source_data_1[len("Event: "):]
    else:
        source = (c.channel or "no channel recorded").replace("_", " ")
    return {"name": name, "label": f"{name}, {c.job_title}" if c.job_title else name, "source": source}


def _bands(metrics: Optional[dict], lo: float, hi: float) -> list[dict]:
    """The live model's calibration bins that the held leads' scores fall in, lowest first."""
    out = []
    for b in (metrics or {}).get("calibration_bins") or []:
        if b["hi"] > lo and b["lo"] <= hi:
            out.append({"lo": round(100 * b["lo"]), "hi": round(100 * b["hi"]), "n": b["n"],
                        "booked": round(100 * b["observed"]), "too_few": b["n"] < SMALL_BAND})
    return out


def _arrival(created: datetime, now: datetime) -> dict:
    """The first-day clock in this page's words: text, sub (when it arrived, or when its first day ends), state."""
    h = _hours(created, now)
    state = "late" if h >= FIRST_DAY_HOURS else ("warn" if h >= WARN_AFTER_HOURS else "fresh")
    sub = f"first day ends in {_span(FIRST_DAY_HOURS - h)}" if state == "warn" else f"arrived {_when(created)}"
    return {"text": _span(h), "sub": sub, "state": state}


def _limits(team: list, counts: dict) -> dict:
    """Where the SDRs stand against their limits: n, at_limit, uniform (every SDR has the same waiting/limit, as
    {waiting, limit}, or None) and room (the SDRs with a free slot, as {name, waiting, limit})."""
    pairs = {(counts.get(o.id, 0), o.capacity_limit) for o in team}
    at_limit = [o for o in team if counts.get(o.id, 0) >= o.capacity_limit]
    uniform = {"waiting": next(iter(pairs))[0], "limit": next(iter(pairs))[1]} if len(pairs) == 1 else None
    room = [{"name": o.name, "waiting": counts.get(o.id, 0), "limit": o.capacity_limit} for o in team if o not in at_limit]
    return {"n": len(team), "at_limit": len(at_limit), "all": bool(team) and len(at_limit) == len(team),
            "uniform": uniform, "room": room}


def overdue_clocks(st, router, now: datetime, sla_hours: int, owners: dict, company) -> tuple[list, list]:
    """(overdue, upcoming): leads whose latest assignment still waits on its SDR for a first touch (the SDR still
    owns it, it is NEW/OPEN in an SDR's stage, no first touch seen, no decision), oldest assignment first. Overdue
    are the router's stale assignments (past the SLA, the ones the reminder job chases); upcoming were assigned
    inside the SLA. `company` names a contact's company."""
    since = now - timedelta(hours=sla_hours)
    latest: dict[str, dict] = {}
    rows = list(router.stale_assignments(sla_hours, now)) + [dict(r) for r in st.conn.execute(
        "SELECT contact_id, owner_id, created_at FROM routing_log WHERE decision IN ('assigned','holdout_assigned') AND created_at > ? "
        "ORDER BY id", (since.isoformat(timespec="seconds"),))]
    for r in rows:
        if r["contact_id"] not in latest or r["created_at"] >= latest[r["contact_id"]]["created_at"]:
            latest[r["contact_id"]] = r
    decided = {r["contact_id"] for r in st.conn.execute("SELECT DISTINCT contact_id FROM overrides WHERE action IN ('worked','junk','skipped')")}
    touched = {r["contact_id"] for r in st.conn.execute("SELECT contact_id FROM touches")}
    overdue, upcoming = [], []
    for cid, r in latest.items():
        if cid in decided or cid in touched:
            continue
        c = st.hs.get_contact(cid)
        if (c is None or c.hubspot_owner_id != r["owner_id"] or c.hs_lead_status not in WAITING_LEAD_STATUSES
                or c.lifecycle_stage not in SDR_LIFECYCLE_STAGES):
            continue
        assigned = datetime.fromisoformat(r["created_at"])
        due = assigned + timedelta(hours=sla_hours)
        entry = {"c": c, **_who(c, cid), "company": company(c), "owner_id": r["owner_id"], "owner": owners.get(r["owner_id"]),
                 "assigned_at": assigned, "assigned": _when(assigned), "since_assignment": _span(_hours(assigned, now)),
                 "arrival": _arrival(c.createdate, now), "due": _when(due),
                 "due_in": f"in {_span(_hours(now, due))}" if due > now else f"{_span(_hours(due, now))} ago"}
        (overdue if assigned <= since else upcoming).append(entry)
    overdue.sort(key=lambda e: e["assigned_at"])
    upcoming.sort(key=lambda e: e["assigned_at"])
    return overdue, upcoming


def held_page(st) -> dict:
    """What the held & overdue page shows.

    now / now_text: the service clock and '30 Oct 2028, 00:00'; frozen: that clock is the generator's simulated one,
    not wall time. mock: the demo stand-in for HubSpot is on.
    held: unreleased held_queue rows, best first by pool rank, each {id, c (Contact or None), name, label, source,
    company, rank, p, pct (whole percent), held_at (text), held_on_day (bool: held on the clock's day), held_time,
    reason, arrived ('24 days'), age_out (the date it passes the pool's age limit), in_pool (False: left the pool or
    found an owner; routing drops it at its next pass)}. shown: how
    many to list before the rest fold away. held_span: {first, last} held_at texts. pool_size: leads in the pool the
    ranks count within; ranked_at: when the last scheduled scoring pass that refreshed ranks finished ('03:38 UTC'),
    or None; score_every / remind_every: those jobs' intervals in minutes. p_lo / p_hi: the held leads' whole-percent
    score range. bands: the live model's calibration bins over that range ({lo, hi, n, booked, too_few});
    live_version, months_text, held_out: the months those bins were measured on and whether the model was tested on
    them unseen. age_out: {date, n, today} for the next day held leads in the pool pass its age limit (max_age_days;
    a lead still in the pool has not passed it yet, so the earliest is today at the soonest), or None.
    owners: the routing team (SDRs) each with waiting/limit in `counts`; other_owners: active owners outside it.
    limits: see _limits. task_due_hours: when a release's HubSpot task falls due. overdue / upcoming: see overdue_clocks,
    with upcoming cut to the next NEXT_SHOWN (never splitting one assignment time) and upcoming_rest {n, first, last}
    for the ones left out. sla_hours; sla_since: the clock less the SLA, as text; hold_after_hours: how long a lead
    waits above the line before it can be held. speed: the measured first-day effect (rate_fast, rate_slow,
    n_fast, n_slow, factor; rates may be missing with too few leads). generator_factor: the mock generator's built-in
    late-touch factor.
    """
    now = clock(st.conn)
    router = st.router()
    sla_hours = DEFAULT_FIRST_TOUCH_SLA_HOURS
    rows = [dict(r) for r in st.conn.execute("SELECT * FROM held_queue WHERE released_at IS NULL ORDER BY held_at").fetchall()]
    pool = {c.id: c for c in pool_contacts(st.hs, st.conn)}
    max_age = max_age_days(st.conn)
    companies: dict[str, str] = {}

    def company(c: Optional[Contact]) -> str:
        if c is None or not c.company_id:
            return ""
        if c.company_id not in companies:
            co = st.hs.get_company(c.company_id)
            companies[c.company_id] = co.name if co else ""
        return companies[c.company_id]

    held = []
    for r in rows:
        c = pool.get(r["contact_id"])
        in_pool = c is not None
        if c is None:
            c = st.hs.get_contact(r["contact_id"])
        at = datetime.fromisoformat(r["held_at"])
        held.append({"id": r["contact_id"], "c": c, **_who(c, r["contact_id"]), "company": company(c),
                     "rank": c.auxo_rank if c else None,
                     "p": c.auxo_probability if c else None,
                     "pct": round(100 * c.auxo_probability) if c and c.auxo_probability is not None else None,
                     "held_at": _when(at), "held_time": f"{at:%H:%M}", "held_on_day": at.date() == now.date(),
                     "reason": r["reason"] or "", "arrived": _span(_hours(c.createdate, now)) if c else None,
                     "age_out": (c.createdate + timedelta(days=max_age)).date() if c else None,
                     "in_pool": in_pool})
    held.sort(key=lambda h: (h["rank"] is None, h["rank"] or 0, -(h["p"] or 0)))

    probs = [h["p"] for h in held if h["p"] is not None]
    live = st.conn.execute("SELECT version, metrics FROM model_registry WHERE status='live' ORDER BY promoted_at DESC LIMIT 1").fetchone()
    metrics = loads(live["metrics"]) if live else None
    months = (metrics or {}).get("test_months") or (metrics or {}).get("validation_months") or []
    outs = sorted({h["age_out"] for h in held if h["in_pool"] and h["age_out"] is not None})
    ranked = st.conn.execute("SELECT finished_at FROM run_log WHERE job='scheduled.score' AND status='ok' ORDER BY id DESC LIMIT 1").fetchone()
    ranked_at = None
    if ranked:
        t = datetime.fromisoformat(ranked["finished_at"])
        ranked_at = f"{t:%H:%M} UTC" if t.date() == datetime.utcnow().date() else f"{_when(t)} UTC"
    every = {j.name: j.every_min for j in jobs_from_env()}
    held_ats = [r["held_at"] for r in rows]

    counts = st.hs.waiting_lead_counts()
    active = st.hs.list_owners()
    team = [o for o in active if o.team == router.team]
    overdue, upcoming = overdue_clocks(st, router, now, sla_hours, {o.id: o for o in st.hs.list_owners(active_only=False)}, company)
    cut = min(NEXT_SHOWN, len(upcoming))
    while 0 < cut < len(upcoming) and upcoming[cut]["assigned_at"] == upcoming[cut - 1]["assigned_at"]:
        cut -= 1               # an assignment pass goes in whole or not at all, so the sentence after can name its time
    if cut == 0:
        cut = min(NEXT_SHOWN, len(upcoming))
    rest = upcoming[cut:]
    return {
        "now": now, "now_text": f"{now.day} {now:%b %Y, %H:%M}", "mock": isinstance(st.hs, MockHubSpot),
        "frozen": bool(get_setting(st.conn, "clock") or get_setting(st.conn, "generated_as_of")),
        "held": held, "shown": SHOWN,
        "held_span": {"first": _when(datetime.fromisoformat(min(held_ats))), "last": _when(datetime.fromisoformat(max(held_ats)))} if held_ats else None,
        "pool_size": len(pool), "ranked_at": ranked_at, "score_every": every.get("score"), "remind_every": every.get("remind"),
        "p_lo": round(100 * min(probs)) if probs else None, "p_hi": round(100 * max(probs)) if probs else None,
        "bands": _bands(metrics, min(probs), max(probs)) if probs else [],
        "live_version": live["version"] if live else None, "months_text": _months(months),
        "held_out": bool((metrics or {}).get("test_months")) and (metrics or {}).get("held_out", True),
        "age_out": {"date": f"{outs[0].day} {outs[0]:%b}", "n": sum(1 for h in held if h["in_pool"] and h["age_out"] == outs[0]),
                    "today": outs[0] == now.date()} if outs else None,
        "max_age_days": max_age,
        "owners": team, "other_owners": [o for o in active if o.team != router.team], "counts": counts,
        "limits": _limits(team, counts), "task_due_hours": router.task_due_hours,
        "overdue": overdue, "upcoming": upcoming[:cut],
        "upcoming_rest": {"n": len(rest), "first": rest[0]["assigned"], "last": rest[-1]["assigned"]} if rest else None,
        "sla_hours": sla_hours, "sla_since": _when(now - timedelta(hours=sla_hours)), "hold_after_hours": router.hold_after_hours,
        "speed": speed_effect(st.conn), "generator_factor": LATE_TOUCH_FACTOR,
    }
