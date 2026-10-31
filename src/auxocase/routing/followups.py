"""The follow-up and the last email, sent without the SDR.

The sequence is three emails. The SDR reads, edits and sends the first (outreach.py drafts it on assignment).
With `followups_auto` on (the Leadership page's capacity section), the service takes the other two: once the
first email is seen in HubSpot as the lead's first touch, the follow-up and the last email are put on the calendar
(`followup_days`, '3,7': days after the first email), and when each falls due it is drafted from the lead's
activity, as the SDR would have drafted it on the lead page, and sent from the SDR. A lead who replied, booked, or
was moved on gets no more; the SDR can stop them from the lead page, and an email the SDR sent themselves counts
as that step. The minutes the SDR no longer spends come back as capacity (capacity.py).

Sending: on a real portal the SDR's sequence sends steps two and three itself, on its own delays, from the same
two contact tokens the first email used (`auxo_email_subject`, `auxo_email_body`), so sending here means writing
the fresh draft to those properties before the step fires (go-live step 6). On the mock there is no sequence
engine, so the send is logged on the contact as HubSpot would log it: an outgoing email from the SDR.

Only leads first emailed after the switch went on are taken (`followups_since`): turning it on does not start
emailing everyone touched last quarter.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from typing import Optional

from ..capacity import AUTO_SINCE, followups_auto
from ..hubspot.client import HubSpotClient
from ..hubspot.models import SDR_LIFECYCLE_STAGES
from ..llm import usage
from ..llm.client import LLM
from ..llm.emails import LAST_TOUCH, next_touch
from ..storage.db import clock, get_setting, now_iso
from .outreach import draft_for

DEFAULT_DAYS = "3,7"          # the follow-up three days after the first email, the last email a week after
DEFAULT_LIMIT = 10            # drafts a run: with Claude on, each is a model call of seconds
STEPS = tuple(range(2, LAST_TOUCH + 1))
STOPPED_LEAD_STATUSES = ("CONNECTED", "UNQUALIFIED", "BAD_TIMING")    # the SDR reached them, or moved the lead on
LABELS = {2: "follow-up", 3: "last email"}


def days(conn: sqlite3.Connection) -> list[int]:
    raw = get_setting(conn, "followup_days") or DEFAULT_DAYS
    out = [int(x) for x in raw.split(",") if x.strip()]
    return (out + [7] * len(STEPS))[: len(STEPS)]


def rows_for(conn: sqlite3.Connection, contact_id: str) -> list[dict]:
    """The lead's follow-ups, step 2 then 3: {step, due_at, status due|sent|stopped|skipped, sent_at, reason, subject}."""
    return [dict(r) for r in conn.execute("SELECT * FROM followups WHERE contact_id=? ORDER BY step", (contact_id,)).fetchall()]


def _stop(conn: sqlite3.Connection, contact_id: str, reason: str, steps: Optional[tuple] = None, status: str = "stopped") -> int:
    q = "UPDATE followups SET status=?, reason=?, updated_at=? WHERE contact_id=? AND status='due'"
    params: list = [status, reason, now_iso(), contact_id]
    if steps:
        q += f" AND step IN ({','.join('?' * len(steps))})"
        params += list(steps)
    n = conn.execute(q, params).rowcount
    conn.commit()
    return n


def stop(conn: sqlite3.Connection, contact_id: str, reason: str = "stopped by the SDR") -> int:
    """No more automatic emails for this lead. Returns how many were still to go."""
    return _stop(conn, contact_id, reason)


def schedule(hs: HubSpotClient, conn: sqlite3.Connection, now: Optional[datetime] = None) -> int:
    """Put the follow-up and the last email on the calendar for every lead whose first touch was an email seen
    since the switch went on and that has no rows yet. Returns how many leads were scheduled."""
    if not followups_auto(conn):
        return 0
    since = get_setting(conn, AUTO_SINCE) or now_iso()
    rows = conn.execute("SELECT t.contact_id, t.owner_id, t.touched_at FROM touches t LEFT JOIN followups f ON f.contact_id = t.contact_id "
                        "WHERE t.type='EMAIL' AND t.detected_at >= ? AND f.contact_id IS NULL ORDER BY t.touched_at", (since,)).fetchall()
    n = 0
    for r in rows:
        c = hs.get_contact(r["contact_id"])
        if c is None or not c.email or c.hubspot_owner_id != r["owner_id"]:
            continue
        touched = datetime.fromisoformat(r["touched_at"])
        for step, d in zip(STEPS, days(conn)):
            conn.execute("INSERT INTO followups(contact_id, owner_id, step, due_at, status, created_at, updated_at) VALUES(?,?,?,?,?,?,?)",
                         (c.id, r["owner_id"], step, (touched + timedelta(days=d)).isoformat(timespec="seconds"), "due", now_iso(), now_iso()))
        n += 1
    conn.commit()
    return n


def send_due(hs: HubSpotClient, conn: sqlite3.Connection, llm: LLM, now: Optional[datetime] = None, limit: int = DEFAULT_LIMIT,
             contact_id: Optional[str] = None) -> dict:
    """Draft and send every follow-up that has fallen due (`contact_id`: that lead's next one, due or not: the demo's
    "send it now"). A lead that replied, booked or was moved on is stopped instead; a step the SDR already sent
    themselves is skipped. Returns {sent, stopped, skipped, waiting}, counted in leads."""
    now = now or clock(conn)
    q = "SELECT * FROM followups WHERE status='due'" + (" AND contact_id=?" if contact_id else " AND due_at <= ?") + " ORDER BY due_at, step"
    due = conn.execute(q, (contact_id or now.isoformat(timespec="seconds"),)).fetchall()
    out = {"sent": 0, "stopped": 0, "skipped": 0, "waiting": 0}
    seen: set[str] = set()
    for r in due:
        if r["contact_id"] in seen:
            continue                      # one email a lead per run: the next step is drafted from this one once it is sent
        seen.add(r["contact_id"])
        if out["sent"] >= limit:
            out["waiting"] += 1
            continue
        c = hs.get_contact(r["contact_id"])
        if c is None or c.hubspot_owner_id != r["owner_id"] or c.lifecycle_stage not in SDR_LIFECYCLE_STAGES or c.hs_lead_status in STOPPED_LEAD_STATUSES:
            _stop(conn, r["contact_id"], "the lead moved on" if c else "the lead is gone")
            out["stopped"] += 1
            continue
        history = hs.engagements_for_contact(c.id)
        touch, why = next_touch(history)
        if touch is None:
            _stop(conn, c.id, "the lead replied" if why == "replied" else "all three emails sent")
            out["stopped"] += 1
            continue
        if touch > r["step"]:
            _stop(conn, c.id, "sent by the SDR", steps=(r["step"],), status="skipped")
            out["skipped"] += 1
            continue
        if touch < r["step"]:
            out["waiting"] += 1          # the earlier email has not gone yet (the first one was a call): nothing to send
            continue
        with usage.reference(c.id):
            draft, _ = draft_for(hs, conn, llm, c, history)
        if draft is None:
            out["waiting"] += 1
            continue
        hs.batch_update_contacts([(c.id, {"auxo_email_subject": draft.subject, "auxo_email_body": draft.body})])
        if hasattr(hs, "simulate_sequence_send"):            # the mock: log the send as HubSpot would, from the SDR
            hs.log_engagement(c.id, "EMAIL", "OUTGOING", now, c.hubspot_owner_id, subject=draft.subject)
        conn.execute("UPDATE followups SET status='sent', sent_at=?, subject=?, body=?, source=?, updated_at=? WHERE contact_id=? AND step=?",
                     (now.isoformat(timespec="seconds"), draft.subject, draft.body, draft.source, now_iso(), c.id, r["step"]))
        conn.commit()
        out["sent"] += 1
    return out


def run(hs: HubSpotClient, conn: sqlite3.Connection, llm: LLM, now: Optional[datetime] = None, limit: int = DEFAULT_LIMIT) -> dict:
    """The scheduled job: schedule new first-emailed leads, then send what is due. A no-op while the switch is off."""
    if not followups_auto(conn):
        return {"auto": False}
    out = {"auto": True, "scheduled": schedule(hs, conn, now)}
    out.update(send_due(hs, conn, llm, now, limit))
    return out
