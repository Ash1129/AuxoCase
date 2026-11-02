"""The Overview: what the pipeline looks like right now.

The pool, the slots and the held queue, the capacity line, the top of the pool, each SDR's desk and the jobs'
latest runs. The line's place and the freshness discount are read from the settings and defaults the router reads
(routing/engine.py), so the page draws the line routing draws rather than a copy of it. Ranks are as the last
scoring wrote them; lead ages run on the service clock (frozen on the demo data), run times on the wall clock (UTC).
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta
from typing import Optional

from ...data.generate import LATE_TOUCH_FACTOR
from ...feedback import loop as fb
from ...hubspot.mock import MockHubSpot
from ...hubspot.models import SDR_LIFECYCLE_STAGES, WAITING_LEAD_STATUSES
from ...pool import max_age_days, passed_gate, ranks_below_gate
from ...routing.engine import (DEFAULT_FIRST_TOUCH_SLA_HOURS, DEFAULT_FRESH_HOURS, DEFAULT_LINE_HORIZON_DAYS,
                               DEFAULT_TEAM_CAPACITY_PER_MONTH)
from ...capacity import touches_per_month
from ...storage.db import clock, clock_frozen, get_setting, loads
from .. import tz
from ..scheduler import DEFAULT_JOBS
from .held import overdue_clocks
from .lead import FIRST_DAY_HOURS, calibration_band, first_day
from .queue import QUEUE_DAYS

TOP_SHOWN = 8            # pool rows shown above the line
SLOT_MARKS = 6           # a desk draws one square per slot up to this many
CHANNEL_NAMES = {"website": "Website", "paid_ads": "Paid ads", "webinar": "Webinar", "email": "Email", "events": "Events",
                 "referral": "Referral"}
_WORDS = ("no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve")


def words(n: int) -> str:
    """'eight' up to twelve, figures after: how the page counts in a sentence."""
    return _WORDS[n] if 0 <= n < len(_WORDS) else f"{n:,}"


def _age(hours: float) -> str:
    """'41 m', '7 h 30 m', '19 days'."""
    minutes = max(0, round(hours * 60))
    if minutes < 60:
        return f"{minutes} m"
    if minutes < 48 * 60:
        return f"{minutes // 60} h {minutes % 60} m"
    return f"{minutes // (24 * 60)} days"


def _window(hours: float) -> str:
    """A setting's window in words: 'a day', 'a week', '3 days', '36 hours'."""
    if hours == 24:
        return "a day"
    if hours == 7 * 24:
        return "a week"
    return f"{hours / 24:g} days" if hours % 24 == 0 else f"{hours:g} hours"


def _wall(iso: Optional[str], ref_day: Optional[str], seconds: bool = False) -> Optional[str]:
    """A wall-clock log time: '03:42' (or '03:42:46'), with the date in front when it is not on `ref_day`."""
    if not iso:
        return None
    t = datetime.fromisoformat(iso)
    hm = t.strftime("%H:%M:%S" if seconds else "%H:%M")
    return hm if t.date().isoformat() == ref_day else f"{t.day} {t.strftime('%b')} {hm}"


def _plural(n: int, one: str, many: Optional[str] = None) -> str:
    return f"{n:,} {one if n == 1 else (many or one + 's')}"


def run_sentence(job: str, status: str, detail: Optional[str], mock: bool) -> str:
    """What one scheduled run did, from the dict its job returned (service/scheduler.py). A skipped or failed run's
    detail is already a sentence (the reason or the error), and anything unexpected is shown as logged."""
    try:
        d = json.loads(detail) if detail else {}
    except ValueError:
        d = None
    if status != "ok" or not isinstance(d, dict):
        return detail or ""
    if job == "score":
        return (f"Scored {_plural(d.get('scored', 0), 'new or changed lead')} with {d.get('model') or 'the points table'}"
                f"{' (reader ' + d['reader'] + ')' if d.get('reader') else ''}; refreshed the pool's ranks.")
    if job == "rescore":
        if not d.get("pending") and "rescored" not in d:
            return "No rescore pending."
        left = d.get("left", 0)
        return f"Rescored {_plural(d.get('rescored', 0), 'pool lead')} on {d.get('model')}; " + (f"{left:,} left." if left else "the whole pool is on it.")
    if job == "route":
        if not d:
            return "Placed no lead and held none."
        parts = []
        if d.get("assigned"):
            parts.append(f"placed {_plural(d['assigned'], 'lead')} from the top of the pool")
        if d.get("holdout_assigned"):
            parts.append(f"{words(d['holdout_assigned'])} in the exploration slice")
        if d.get("held"):
            parts.append(f"held {d['held']:,} for the manager")
        if d.get("incomplete"):
            parts.append(f"{words(d['incomplete'])} missing a field")
        if d.get("already_owned"):
            parts.append(f"{words(d['already_owned'])} already owned")
        text = "; ".join(parts) or json.dumps(d)
        return text[0].upper() + text[1:] + "."
    if job == "touches":
        n, k = d.get("read", 0), d.get("first_touches", 0)
        where = "the mock portal" if mock else "HubSpot"
        text = f"Read {_plural(n, 'activity', 'activities')} from {where}; " + (f"{_plural(k, 'new first touch', 'new first touches')}" if k else "no new first touch")
        return text + (f", {_plural(d['assigned'], 'slot')} refilled." if d.get("assigned") else ".")
    if job == "outreach":
        seq = d.get("sequence")
        text = f"Drafted {_plural(d.get('drafted', 0), 'email')}, enrolled {d.get('enrolled', 0):,}" + (f" in sequence {seq}" if seq and seq != "none set" else " (no sequence set)")
        extra = [f"{d['refused']:,} refused by the sequence" if d.get("refused") else "", f"{d['passed_over']:,} passed over" if d.get("passed_over") else ""]
        return text + "".join(f"; {e}" for e in extra if e) + "."
    if job == "remind":
        n = d.get("reminders", 0)
        return f"Created {_plural(n, 'reminder task')} for leads untouched past the first-touch SLA." if n else "No lead untouched past the first-touch SLA."
    if job == "sync":
        return (f"Read outcomes back for {d.get('mqls', 0):,} MQLs: {d.get('worked', 0):,} worked, {d.get('booked', 0):,} booked a demo, "
                f"{d.get('won', 0):,} won.")
    if job == "monitor":
        names = {"ok": "within limits", "above": "above them", "below": "below them", "held": "too few leads to judge"}
        parts = [f"{d[k]:,} {names[k]}" for k in names if d.get(k)]
        return "Checked each channel's demo rate against the model's" + (": " + ", ".join(parts) if parts else "") + "."
    if job == "train":
        lift = d.get("lift")
        return (f"Trained candidate {d.get('candidate')}" + (f" (lift {lift:.2f})" if isinstance(lift, (int, float)) else "")
                + "; it goes live only when marketing and sales both approve.")
    return detail or ""


def _runs(st, mock: bool) -> list[dict]:
    """The latest run of each scheduled job, newest first. One query per job, newest row first, so the frequent jobs
    stop at once and the monthly one reads back no further than its last run."""
    rows = []
    for job in DEFAULT_JOBS:
        r = st.conn.execute("SELECT * FROM run_log WHERE job=? ORDER BY id DESC LIMIT 1", (f"scheduled.{job.name}",)).fetchone()
        if r:
            rows.append(dict(r, name=job.name))
    rows.sort(key=lambda r: r["id"], reverse=True)
    for r in rows:
        r["what"] = run_sentence(r["name"], r["status"], r["detail"], mock)
    return rows


def _lead_line(st, c) -> dict:
    co = st.hs.get_company(c.company_id) if c.company_id else None
    return {"name": " ".join(p for p in (c.first_name, c.last_name) if p) or f"Contact #{c.id}", "title": c.job_title,
            "company": co.name if co else ""}


def _waiting_by_owner(st, now: datetime) -> dict[str, list]:
    """Each SDR's waiting leads, oldest arrival first, by the queue page's rule (views/queue.py): handed out by AuxoCase
    in the last QUEUE_DAYS, still theirs, NEW/OPEN, no decision and no first touch seen. The leads with a decision
    or a touch are dropped in the database before any is read from HubSpot, since this page reads every desk."""
    since = (now - timedelta(days=QUEUE_DAYS)).isoformat(timespec="seconds")
    rows = st.conn.execute(
        "SELECT DISTINCT owner_id, contact_id FROM routing_log WHERE decision IN ('assigned','holdout_assigned') AND created_at >= ? "
        "AND contact_id NOT IN (SELECT contact_id FROM touches) "
        "AND contact_id NOT IN (SELECT contact_id FROM overrides WHERE action IN ('worked','junk','skipped'))", (since,)).fetchall()
    out: dict[str, list] = {}
    for r in rows:
        c = st.hs.get_contact(r["contact_id"])
        if c is not None and c.hubspot_owner_id == r["owner_id"] and c.hs_lead_status in WAITING_LEAD_STATUSES \
                and c.lifecycle_stage in SDR_LIFECYCLE_STAGES:
            out.setdefault(r["owner_id"], []).append(c)
    for leads in out.values():
        leads.sort(key=lambda c: c.createdate)
    return out


def _sdr_rows(st, sdrs: list, counts: dict, waiting: dict, now: datetime) -> list[dict]:
    """One row an SDR for the manager's table, most worked first: {o, href, waiting, limit, oldest (the age of the
    oldest waiting lead, or None), late (waiting leads past their first day), received (handed out in the last
    QUEUE_DAYS), worked (their leads with a first touch in the window, from the outcomes synced), booked (of those,
    the demos booked), rate (booked per 100 worked, or None), skipped, junk (their own calls in the window), hours
    (the median hours from hand-out to first touch, or None)}."""
    conn = st.conn
    since = (now - timedelta(days=QUEUE_DAYS)).isoformat(timespec="seconds")
    received = {r["owner_id"]: r["n"] for r in conn.execute(
        "SELECT owner_id, COUNT(DISTINCT contact_id) n FROM routing_log WHERE decision IN ('assigned','holdout_assigned') AND created_at >= ? "
        "GROUP BY owner_id", (since,))}
    worked = {r["owner_id"]: dict(r) for r in conn.execute(
        "SELECT c.hubspot_owner_id owner_id, COUNT(*) worked, COALESCE(SUM(o.demo_booked), 0) booked FROM outcomes o "
        "JOIN hs_contacts c ON c.id = o.contact_id WHERE o.worked = 1 AND o.first_touch_at >= ? AND c.hubspot_owner_id IS NOT NULL "
        "GROUP BY c.hubspot_owner_id", (since,))}
    calls = {(r["owner_id"], r["action"]): r["n"] for r in conn.execute(
        "SELECT owner_id, action, COUNT(DISTINCT contact_id) n FROM overrides WHERE created_at >= ? AND action IN ('skipped','junk') "
        "GROUP BY owner_id, action", (since,))}
    speed: dict[str, list[float]] = {}
    for r in conn.execute(
            "SELECT r.owner_id, r.created_at handed, COALESCE(t.touched_at, o.first_touch_at) touched FROM routing_log r "
            "LEFT JOIN touches t ON t.contact_id = r.contact_id LEFT JOIN outcomes o ON o.contact_id = r.contact_id "
            "WHERE r.decision IN ('assigned','holdout_assigned') AND r.created_at >= ?", (since,)):
        if r["touched"] and r["touched"] >= r["handed"]:
            try:
                speed.setdefault(r["owner_id"], []).append(
                    (datetime.fromisoformat(r["touched"][:19]) - datetime.fromisoformat(r["handed"][:19])).total_seconds() / 3600)
            except ValueError:
                pass
    rows = []
    for o in sdrs:
        mine = waiting.get(o.id, [])
        w = worked.get(o.id) or {"worked": 0, "booked": 0}
        hours = sorted(speed.get(o.id, []))
        rows.append({"o": o, "href": f"/queue/{o.id}", "waiting": counts.get(o.id, 0), "limit": o.capacity_limit,
                     "oldest": _age((now - mine[0].createdate).total_seconds() / 3600) if mine else None,
                     "late": sum(1 for c in mine if first_day(c.createdate, now)["state"] == "late"),
                     "received": received.get(o.id, 0), "worked": w["worked"], "booked": w["booked"],
                     "rate": round(100 * w["booked"] / w["worked"]) if w["worked"] else None,
                     "skipped": calls.get((o.id, "skipped"), 0), "junk": calls.get((o.id, "junk"), 0),
                     "hours": round(hours[len(hours) // 2], 1) if hours else None})
    rows.sort(key=lambda r: (-r["worked"], -r["booked"], r["o"].name))
    return rows


def _bands(metrics: Optional[dict], rows: list[dict]) -> list[dict]:
    """The live model's calibration bands the shown rows fall in, in order of first appearance, with how many."""
    out: dict[tuple, dict] = {}
    for r in rows:
        b = r["band"]
        if b is None:
            continue
        key = (b["lo"], b["hi"])
        out.setdefault(key, {**b, "observed_pct": round(100 * b["observed"]), "count": 0})["count"] += 1
    return list(out.values())


def overview_page(st) -> dict:
    """What the Overview shows. Times: `now_text` is the service clock ('Mon 30 Oct 2028, 00:00'), `frozen` whether
    that clock is a stored one (the demo data's) rather than wall time; `run_day` the newest run's wall-clock date.

    pool_size: leads in the ranked pool; pool_label 'MQLs' (or 'MQLs and plain leads' when the pool ranks below the
    gate) and pool_noun its singular; pool_days its age window; models: Counter of the model versions that scored
    it. held: leads in the manager's queue; hold_after: how long an above-line lead waits before it is held.
    team: {sdrs, slots (None when an SDR has no limit), waiting, free, at_limit, limit (the shared limit, or None
    when limits differ or there is none), unlimited (an SDR without a limit), reach (the team's, routing/engine.py), overdue
    (leads still waiting on their SDR past the first-touch SLA: the Held & overdue page's overdue list, counted)}.
    per_month: the team's monthly capacity ('500'); horizon: the line's horizon in words ('a week'), horizon_work
    ("a week's work"). cap: the capacity line, or None when the pool is empty: {place (1-based: the engine's k, or
    the pool's end when it is shorter), k, value (the line routing stored, a probability, or None before the first
    route run), set_at (that run's wall time), above (places before it), below (leads after it), follow_from and
    follow_to (the above-line places the table does not show, or None)}.
    top: up to TOP_SHOWN rows above the line, each {place, c, name, title, company, age, channel, pct, below_gate,
    flag, band}; ranked_at: the wall time of the last scheduled score run (when the ranks were taken), or None;
    first_rank: the top row's rank. bands: the calibration bands the rows fall in ({lo, hi, n, observed_pct,
    too_few, count}); band_model: the live version they are from; gate_shown: rows below HubSpot's gate;
    below_gate_on: whether the pool ranks below it.
    fresh: {factor (stale_lead_factor), window ('a day'), hours, discounted, done ('halved'), do ("halve a lead's
    touch-now value"), first ('in their first day')}; speed: fb.speed_effect plus `ratio` (first-day
    rate over later) and `words` ('about twice as often') when measured; gen_factor: the mock generator's own
    first-day factor (for the footnote).
    desks: one per SDR, oldest waiting lead first, then the empty desks: {o, waiting, limit, marks (True per used
    slot square), lead (Contact or None), more (other waiting leads), who (name/title/company), age, state
    (fresh|warn|late), touch_by ('00:37'), holdout, below_gate, href}.
    runs: the latest run of each scheduled job, newest first, each a run_log row plus name, what (a sentence) and
    when ('03:42:46', dated when not on run_day). words: the number-to-word helper; queue_days: QUEUE_DAYS.
    """
    conn = st.conn
    mock = isinstance(st.hs, MockHubSpot)
    now = clock(conn)
    router = st.router()
    pool = router.pool(now)
    held = conn.execute("SELECT COUNT(*) n FROM held_queue WHERE released_at IS NULL").fetchone()["n"]

    # the slots: the router's own count, so the page and routing agree on who is free. Overdue is the Held & overdue
    # page's own list (views/held.py), so the count here is the number of leads that page shows under it.
    counts, sdrs, free = router.free_slots()
    limits = {o.capacity_limit for o in sdrs}
    overdue, _ = overdue_clocks(st, router, now, DEFAULT_FIRST_TOUCH_SLA_HOURS, {o.id: o for o in st.hs.list_owners(active_only=False)},
                         lambda c: "")
    unlimited = any(o.capacity_limit is None for o in sdrs)
    team = {"sdrs": len(sdrs), "slots": None if unlimited else sum(o.capacity_limit for o in sdrs),
            "waiting": sum(counts.get(o.id, 0) for o in sdrs), "free": free,
            "at_limit": sum(1 for o in sdrs if o.capacity_limit is not None and counts.get(o.id, 0) >= o.capacity_limit),
            "limit": limits.pop() if len(limits) == 1 else None, "overdue": len(overdue),
            "unlimited": unlimited, "reach": router.reach(sdrs) if sdrs else 0}

    # the jobs' latest runs; their times, and the times the ranks and the line were taken, are wall-clock UTC
    runs = _runs(st, mock)
    run_day = max(r["finished_at"] or "" for r in runs)[:10] if runs else None
    for r in runs:
        r["when"] = _wall(r["finished_at"], run_day, seconds=True)
    scored = next((r for r in runs if r["name"] == "score" and r["status"] == "ok"), None) or conn.execute(
        "SELECT * FROM run_log WHERE job='scheduled.score' AND status='ok' ORDER BY id DESC LIMIT 1").fetchone()

    # the capacity line, as routing/engine.py draws it: the value at place k, the team's reach in the horizon
    per_month = touches_per_month(conn, len(sdrs))
    days = float(get_setting(conn, "capacity_line_days") or DEFAULT_LINE_HORIZON_DAYS)
    horizon = _window(days * 24)
    k = max(1, int(round(per_month * days / 30.4)))
    cap = None
    if pool:
        place = min(k, len(pool))
        cap = {"place": place, "k": k, "value": loads(get_setting(conn, "capacity_line_probability")),
               "set_at": _wall(get_setting(conn, "last_routing_run"), run_day), "above": place - 1, "below": len(pool) - place,
               "follow_from": TOP_SHOWN + 1 if place - 1 > TOP_SHOWN else None, "follow_to": place - 1 if place - 1 > TOP_SHOWN else None}

    live = conn.execute("SELECT version, metrics FROM model_registry WHERE status='live' ORDER BY promoted_at DESC LIMIT 1").fetchone()
    metrics = loads(live["metrics"]) if live else None
    top = []
    for i, c in enumerate(pool[:min(TOP_SHOWN, cap["above"]) if cap else 0]):
        top.append({"place": i + 1, "c": c, **_lead_line(st, c), "age": _age((now - c.createdate).total_seconds() / 3600),
                    "channel": CHANNEL_NAMES.get(c.channel, c.channel.replace("_", " ").capitalize() if c.channel else "[NOT RECORDED]"),
                    "pct": round(100 * (c.auxo_probability or 0)), "below_gate": not passed_gate(c), "flag": c.auxo_review_flag,
                    "band": calibration_band(metrics, c.auxo_probability)})

    fresh_hours = float(get_setting(conn, "fresh_hours") or DEFAULT_FRESH_HOURS)
    factor = float(get_setting(conn, "stale_lead_factor") or 1.0)
    window = _window(fresh_hours)
    fresh = {"factor": factor, "window": window, "hours": fresh_hours, "discounted": factor < 1,
             "done": "halved" if factor == 0.5 else f"cut to {round(100 * factor)}%",
             "do": "halve a lead's touch-now value" if factor == 0.5 else f"cut a lead's touch-now value to {round(100 * factor)}%",
             "first": "in their first day" if fresh_hours == 24 else f"within {window} of arriving"}
    speed = fb.speed_effect(conn, fresh_hours)
    if speed.get("rate_slow"):
        ratio = speed["rate_fast"] / speed["rate_slow"]
        speed["ratio"] = ratio
        speed["words"] = ("about twice as often" if 1.75 <= ratio < 2.25 else "about three times as often" if 2.75 <= ratio < 3.25
                          else "about as often as later ones" if 0.9 <= ratio < 1.1 else f"{ratio:.1f} times as often")

    desks = []
    waiting = _waiting_by_owner(st, now)
    for o in sdrs:
        mine, n = waiting.get(o.id, []), counts.get(o.id, 0)
        d = {"o": o, "waiting": n, "limit": o.capacity_limit, "marks": [i < n for i in range(min(n if o.capacity_limit is None else o.capacity_limit, SLOT_MARKS))],
             "lead": None, "more": max(0, len(mine) - 1), "href": f"/queue/{o.id}"}
        if mine:
            c = mine[0]
            d.update(lead=c, who=_lead_line(st, c), age=_age((now - c.createdate).total_seconds() / 3600),
                     state=first_day(c.createdate, now)["state"], touch_by=(c.createdate + timedelta(hours=FIRST_DAY_HOURS)).strftime("%H:%M"),
                     holdout=c.auxo_holdout, below_gate=not passed_gate(c), href=f"/queue/{o.id}?lead={c.id}")
        desks.append(d)
    desks.sort(key=lambda d: (d["lead"] is None, d["lead"].createdate if d["lead"] else datetime.max))
    sdr_rows = _sdr_rows(st, sdrs, counts, waiting, now)

    frozen = clock_frozen(conn)
    return {"now_text": now.strftime("%a %d %b %Y, %H:%M") if frozen else tz.clock_text(now, weekday=True),
            "now_iso": tz.clock_iso(now), "frozen": frozen,
            "run_day": datetime.fromisoformat(run_day).strftime("%d %b %Y").lstrip("0") if run_day else None,
            "pool_size": len(pool), "pool_label": "MQLs and plain leads" if ranks_below_gate(conn) else "MQLs",
            "pool_noun": "MQL or plain lead" if ranks_below_gate(conn) else "MQL", "pool_days": max_age_days(conn),
            "per_month": f"{per_month:,.0f}" if per_month == int(per_month) else f"{per_month:,}", "horizon": horizon,
            "horizon_work": horizon + ("'s work" if horizon.startswith("a ") else " of work"),
            "models": Counter(c.auxo_model_version or "the points table" for c in pool), "held": held, "hold_after": _window(router.hold_after_hours),
            "team": team, "cap": cap, "top": top, "first_rank": top[0]["c"].auxo_rank if top else None,
            "ranked_at": _wall(scored["finished_at"], run_day) if scored else None,
            "bands": _bands(metrics, top), "band_model": live["version"] if live else None,
            "gate_shown": sum(1 for r in top if r["below_gate"]), "below_gate_on": ranks_below_gate(conn),
            "fresh": fresh, "speed": speed, "gen_factor": LATE_TOUCH_FACTOR, "desks": desks, "sdr_rows": sdr_rows, "runs": runs, "words": words,
            "queue_days": QUEUE_DAYS}
