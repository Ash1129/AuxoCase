"""Phase 3: automated outreach for the leads below the capacity line.

The 12 SDRs reach about 500 leads a month; the other 1,000 MQLs are never
contacted (the brief). This job puts some of them in an automated HubSpot
sequence instead of leaving them untouched: Claude (or the template) drafts
the first email, the draft is written to the lead's `auxo_email_subject` and
`auxo_email_body` properties, and the lead is enrolled in the sequence set by
`AUXO_TIER2_SEQUENCE_ID`, whose first step sends those tokens automatically
from an SDR's inbox, in rotation. The lead leaves the SDRs' pool while the
sequence runs. A reply (an incoming email HubSpot logs on the contact) hands
the lead to that SDR at once, slot permitting, with a task saying why; a
sequence that ends unanswered puts the lead back where it was.

It is an experiment before it is a process. Every eligible lead is drawn
into one of two arms at the moment it becomes eligible: `emailed`, or
`control` (left to the ordinary process, which rarely reaches it). Both are
recorded with the score and rank the lead had, so the comparison has a
denominator: the demo rate of the emailed arm against the control arm, with
the model's own prediction beside them. Two switches, both off by default
(`run.py tier2`): `--explore 0.2` emails that share of the eligible leads;
`--band on` emails all but a holdback share, and is refused until
`TIER2_READY_MATURED` emailed leads have matured labels. Emailed leads are
kept out of the model's training table: an automated email is a different
treatment from an SDR's touch, and the model predicts the latter.

Nothing here adds SDR capacity: a reply costs an SDR a follow-up, and the
cost report (`evaluation.costs`) prices that against the demos it brings.
"""
from __future__ import annotations

import os
import random
import sqlite3
from datetime import datetime, timedelta
from typing import Optional

from ..hubspot.client import HubSpotClient
from ..hubspot.models import Contact, Owner
from ..llm import usage
from ..llm.client import LLM
from ..llm.emails import draft_email, next_touch, sender_first_name
from ..storage.db import clock, get_setting, loads, now_iso, set_setting
from .engine import REPLY_REASON, Router, priority

EXPLORE_SHARE = "tier2_explore_share"      # share of eligible leads emailed while the band is off (0 = off)
BAND = "tier2_band"                        # "on": every eligible lead but the holdback is emailed
HOLDBACK = "tier2_holdback"                # share kept as the control arm while the band is on
DAILY_LIMIT = "tier2_daily_limit"          # most leads emailed in a day of the service clock
MAX_AGE_DAYS = "tier2_max_age_days"        # a lead older than this is not emailed: the email would not be timely
SEQUENCE_DAYS = "tier2_sequence_days"      # unanswered this long after the email, the sequence is over
REPLY_CURSOR = "tier2_reply_cursor"
DEFAULT_HOLDBACK = 0.1
DEFAULT_DAILY_LIMIT = 60                   # about the 1,000 untouched MQLs a month, in a working month
DEFAULT_MAX_AGE_DAYS = 7
DEFAULT_SEQUENCE_DAYS = 14
TIER2_READY_MATURED = 200                  # matured emailed leads before the band is judged
ROTATION = "tier2_rotation"


def sequence_id(conn: sqlite3.Connection) -> str:
    return os.environ.get("AUXO_TIER2_SEQUENCE_ID") or get_setting(conn, "tier2_sequence_id") or ""


def explore_share(conn: sqlite3.Connection) -> float:
    v = get_setting(conn, EXPLORE_SHARE)
    return min(1.0, max(0.0, float(v))) if v else 0.0


def band_on(conn: sqlite3.Connection) -> bool:
    return (get_setting(conn, BAND) or "").lower() == "on"


def holdback(conn: sqlite3.Connection) -> float:
    v = get_setting(conn, HOLDBACK)
    return min(1.0, max(0.0, float(v))) if v else DEFAULT_HOLDBACK


def send_share(conn: sqlite3.Connection) -> float:
    """The share of newly eligible leads that is emailed: the band's share when it is on, else the explore share."""
    return 1.0 - holdback(conn) if band_on(conn) else explore_share(conn)


def _int_setting(conn: sqlite3.Connection, key: str, default: int) -> int:
    v = get_setting(conn, key)
    return int(v) if v else default


def active_contact_ids(conn: sqlite3.Connection) -> set[str]:
    """Leads in a running sequence: out of the SDRs' pool until they reply or the sequence ends."""
    return {r["contact_id"] for r in conn.execute("SELECT contact_id FROM tier2 WHERE status='sent'")}


def emailed_contact_ids(conn: sqlite3.Connection) -> set[str]:
    """Every lead the sequence ever emailed: a different treatment, kept out of the training table."""
    return {r["contact_id"] for r in conn.execute("SELECT contact_id FROM tier2 WHERE arm='emailed'")}


def eligible(hs: HubSpotClient, conn: sqlite3.Connection, now: Optional[datetime] = None) -> list[Contact]:
    """Scored pool leads below the capacity line, fresh enough to email, with an address, not yet drawn into an arm.
    Best first, so a daily limit takes the most valuable."""
    now = now or clock(conn)
    line = loads(get_setting(conn, "capacity_line_probability") or "null")
    if line is None:
        return []
    seen = {r["contact_id"] for r in conn.execute("SELECT contact_id FROM tier2")}
    held = {r["contact_id"] for r in conn.execute("SELECT contact_id FROM held_queue WHERE released_at IS NULL")}
    oldest = now - timedelta(days=_int_setting(conn, MAX_AGE_DAYS, DEFAULT_MAX_AGE_DAYS))
    out = [c for c in Router(hs, conn).pool(now)
           if c.id not in seen and c.id not in held and c.email and c.channel and c.createdate >= oldest
           and priority(conn, c.auxo_probability, c.createdate, now) < line]
    return out


def _sent_today(conn: sqlite3.Connection, now: datetime) -> int:
    day = now.strftime("%Y-%m-%d")
    return conn.execute("SELECT COUNT(*) n FROM tier2 WHERE arm='emailed' AND substr(created_at, 1, 10)=?", (day,)).fetchone()["n"]


def _next_sender(conn: sqlite3.Connection, owners: list[Owner]) -> Optional[Owner]:
    """Round robin over the active SDRs: the email goes out from a real inbox, and a reply lands with its owner."""
    if not owners:
        return None
    order = sorted(owners, key=lambda o: o.id)
    last = get_setting(conn, ROTATION)
    ids = [o.id for o in order]
    o = order[(ids.index(last) + 1) % len(order)] if last in ids else order[0]
    set_setting(conn, ROTATION, o.id)
    return o


def _latest_signals(conn: sqlite3.Connection, contact_id: str) -> list[dict]:
    r = conn.execute("SELECT signals FROM scores WHERE contact_id=? ORDER BY id DESC LIMIT 1", (contact_id,)).fetchone()
    return ((loads(r["signals"]) or {}).get("signals", []) if r else []) or []


def run_tier2(hs: HubSpotClient, conn: sqlite3.Connection, llm: LLM, now: Optional[datetime] = None,
              rng: Optional[random.Random] = None, limit: Optional[int] = None) -> dict:
    """One pass: draw every newly eligible lead into an arm, email the emailed ones up to the day's limit, and close
    the sequences that have run their course. A no-op while both switches are off."""
    now = now or clock(conn)
    counts = {"eligible": 0, "emailed": 0, "control": 0, "refused": 0, "deferred": 0, "expired": expire(conn, now)}
    share = send_share(conn)
    if share <= 0:
        return counts
    rng = rng or random.Random()
    room = max(0, _int_setting(conn, DAILY_LIMIT, DEFAULT_DAILY_LIMIT) - _sent_today(conn, now))
    if limit is not None:
        room = min(room, limit)
    seq = sequence_id(conn)
    owners = [o for o in hs.list_owners(active_only=True) if o.team == "sdr"]
    line = loads(get_setting(conn, "capacity_line_probability") or "null")
    pool = eligible(hs, conn, now)
    counts["eligible"] = len(pool)
    # The day's limit bounds the draw, not only the sends: both arms come from the same best-first slice of the
    # eligible leads, so the control arm is the emailed arm's like for like. The rest wait, undrawn, for tomorrow.
    n_draw = min(len(pool), int(room / share)) if room else 0
    counts["deferred"] = len(pool) - n_draw
    for c in pool[:n_draw]:
        arm = "emailed" if rng.random() < share else "control"
        if arm == "control":
            counts["control"] += 1
            _record(conn, c, "control", "control", line, now)
            continue
        if room <= 0:
            counts["deferred"] += 1     # the draw ran past the limit: this lead stays eligible, undrawn
            continue
        sender = _next_sender(conn, owners)
        history = hs.engagements_for_contact(c.id)
        touch, _ = next_touch(history)
        if touch != 1 or sender is None:
            counts["refused"] += 1      # already emailed by someone, or nobody to send from
            _record(conn, c, "emailed", "refused", line, now, sender)
            continue
        co = hs.get_company(c.company_id) if c.company_id else None
        with usage.reference(c.id):
            draft = draft_email(llm, first_name=c.first_name, job_title=c.job_title, company=co.name if co else "", channel=c.channel,
                                referral_partner=c.referral_partner, reason=c.auxo_reason or "", signals=_latest_signals(conn, c.id),
                                notes=c.notes, sender=sender_first_name(sender.name), touch=1, history=history,
                                booking_link=sender.meetings_link, now=now)
        hs.batch_update_contacts([(c.id, {"auxo_email_subject": draft.subject, "auxo_email_body": draft.body, "auxo_route": "automated"})])
        enrollment = hs.enroll_in_sequence(c.id, seq, sender.email) if seq else None
        status = "sent" if (enrollment or not seq) else "refused"
        _record(conn, c, "emailed", status, line, now, sender, draft, seq, enrollment.id if enrollment else None)
        if status == "sent":
            sent = getattr(hs, "simulate_sequence_send", None)     # the mock has no sequence engine: log the send
            if sent is not None:
                sent(c.id, sender.id, draft.subject, now)
            counts["emailed"] += 1
            room -= 1
        else:
            counts["refused"] += 1
    return counts


def _record(conn: sqlite3.Connection, c: Contact, arm: str, status: str, line: Optional[float], now: datetime,
            sender: Optional[Owner] = None, draft=None, seq: str = "", enrollment_id: Optional[str] = None) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO tier2(contact_id, arm, status, probability, rank, line_probability, owner_id, subject, body, source, rejected, "
        "sequence_id, enrollment_id, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (c.id, arm, status, c.auxo_probability, c.auxo_rank, line, sender.id if sender else None,
         draft.subject if draft else None, draft.body if draft else None, draft.source if draft else None,
         draft.rejected if draft else None, seq or None, enrollment_id, now.isoformat(timespec="seconds")))


def expire(conn: sqlite3.Connection, now: Optional[datetime] = None) -> int:
    """Sequences that ran their length unanswered: the lead returns to the pool (and has usually aged out of it)."""
    now = now or clock(conn)
    cutoff = (now - timedelta(days=_int_setting(conn, SEQUENCE_DAYS, DEFAULT_SEQUENCE_DAYS))).isoformat(timespec="seconds")
    cur = conn.execute("UPDATE tier2 SET status='no_reply', resolved_at=? WHERE status='sent' AND created_at <= ?",
                       (now.isoformat(timespec="seconds"), cutoff))
    return cur.rowcount


def detect_replies(hs: HubSpotClient, conn: sqlite3.Connection, now: Optional[datetime] = None) -> dict:
    """An incoming email from a lead in the sequence is the reply: hand the lead to the SDR it heard from (or the
    next free one, or the held queue), and record when. HubSpot unenrols a replying contact itself."""
    now = now or clock(conn)
    active = active_contact_ids(conn)
    out = {"read": 0, "replied": 0, "assigned": 0, "held": 0}
    if not active:
        return out
    # Paged by when HubSpot recorded the activity, which is wall time, as the first-touch read does; `now` (the
    # service clock, simulated on the mock) stamps the hand-out.
    cursor = get_setting(conn, REPLY_CURSOR)
    since = datetime.fromisoformat(cursor) - timedelta(minutes=5) if cursor else datetime.utcnow() - timedelta(days=1)
    engagements = hs.engagements_logged_since(since)
    out["read"] = len(engagements)
    router = Router(hs, conn)
    for e in sorted(engagements, key=lambda x: (x.timestamp, x.logged_at)):
        if e.contact_id not in active or e.direction != "INCOMING" or e.type != "EMAIL":
            continue
        row = conn.execute("SELECT owner_id FROM tier2 WHERE contact_id=? AND status='sent'", (e.contact_id,)).fetchone()
        if row is None:
            continue
        at = e.timestamp.isoformat(timespec="seconds")
        conn.execute("UPDATE tier2 SET status='replied', replied_at=? WHERE contact_id=?", (at, e.contact_id))
        active.discard(e.contact_id)
        out["replied"] += 1
        c = hs.get_contact(e.contact_id)
        if c is None or c.hubspot_owner_id:
            continue
        d = router.assign_now(c, REPLY_REASON, prefer_owner_id=row["owner_id"], now=now)
        if d.decision == "assigned":
            conn.execute("UPDATE tier2 SET assigned_at=? WHERE contact_id=?", (now.isoformat(timespec="seconds"), e.contact_id))
            out["assigned"] += 1
        elif d.decision == "held":
            out["held"] += 1
    if engagements:
        set_setting(conn, REPLY_CURSOR, max(e.logged_at for e in engagements).isoformat(timespec="seconds"))
    elif not cursor:
        set_setting(conn, REPLY_CURSOR, since.isoformat(timespec="seconds"))
    return out


def readiness(conn: sqlite3.Connection, now: Optional[datetime] = None) -> dict:
    """The experiment so far, by arm: leads drawn, replies, matured labels, demos, observed against predicted. A
    label is matured `DEMO_MATURITY_DAYS` after the draw. The band waits for `TIER2_READY_MATURED` matured emailed
    leads; the arms are then compared on the same months."""
    from ..scoring.model import DEMO_MATURITY_DAYS
    now = now or clock(conn)
    mature_before = (now - timedelta(days=DEMO_MATURITY_DAYS)).isoformat(timespec="seconds")
    arms: dict[str, dict] = {}
    for arm in ("emailed", "control"):
        rows = conn.execute(
            "SELECT t.status, t.probability, t.created_at, t.replied_at, COALESCE(o.worked, 0) worked, COALESCE(o.demo_booked, 0) booked "
            "FROM tier2 t LEFT JOIN outcomes o ON o.contact_id = t.contact_id WHERE t.arm=? AND t.status != 'refused'", (arm,)).fetchall()
        mature = [r for r in rows if r["created_at"] <= mature_before]
        booked = sum(r["booked"] for r in mature)
        preds = [r["probability"] for r in mature if r["probability"] is not None]
        arms[arm] = {"leads": len(rows), "replied": sum(1 for r in rows if r["replied_at"]),
                     "worked_by_sdr": sum(r["worked"] for r in rows), "matured": len(mature), "booked": booked,
                     "observed_rate": round(booked / len(mature), 3) if mature else None,
                     "predicted_rate": round(sum(preds) / len(preds), 3) if preds else None}
    refused = conn.execute("SELECT COUNT(*) n FROM tier2 WHERE status='refused'").fetchone()["n"]
    em, co = arms["emailed"], arms["control"]
    lift = (round(em["observed_rate"] - co["observed_rate"], 3)
            if em["observed_rate"] is not None and co["observed_rate"] is not None else None)
    return {"explore_share": explore_share(conn), "band": band_on(conn), "holdback": holdback(conn), "send_share": send_share(conn),
            "sequence": sequence_id(conn) or None, "daily_limit": _int_setting(conn, DAILY_LIMIT, DEFAULT_DAILY_LIMIT),
            "arms": arms, "refused": refused, "lift": lift, "needed": TIER2_READY_MATURED,
            "ready": em["matured"] >= TIER2_READY_MATURED}


def switch(conn: sqlite3.Connection, explore: Optional[float] = None, band: Optional[bool] = None, force: bool = False) -> dict:
    """The two phase-3 switches (`run.py tier2`). The band is refused until the experiment has matured labels, unless
    forced; switching it off leaves the sequences already running to finish."""
    if explore is not None and not 0 <= explore <= 1:
        raise ValueError("the explore share is a share of the eligible leads, between 0 and 1")
    if band and not band_on(conn):
        status = readiness(conn)
        if not status["ready"] and not force:
            raise ValueError(f"not switched on: {status['arms']['emailed']['matured']} of {status['needed']} emailed leads have "
                             "matured labels. Run the experiment first (--explore), or force it.")
    if explore is not None:
        set_setting(conn, EXPLORE_SHARE, str(explore))
    if band is not None:
        set_setting(conn, BAND, "on" if band else "off")
    return readiness(conn)
