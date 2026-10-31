"""Replies read and acted on before the SDR opens them.

Every reply used to be the SDR's to read: HubSpot logs it, the lead page says "replied: answer from HubSpot", and the
SDR works out what it is. With `replies_auto` on (the Leadership page's capacity section), this job reads each new
reply from a lead an SDR owns (llm/replies.py says what it is) and does the plain part:

- interested, a question, not now: the answer is drafted, with the SDR's booking link, and waits on the lead page
  and the queue, where the lead comes first; not now records when to come back;
- not interested, wrong person, unsubscribe: the lead is closed (UNQUALIFIED in HubSpot) so no SDR time goes on it;
  an unsubscribe still needs the subscription set in HubSpot, which the page says;
- out of office: not an answer, so it does not end the sequence: the follow-ups still to go are moved past the
  day they are back, and the draft after it does not count it as a reply.

Anything else is `other`: handed to the SDR as before. Replies to the automated sequence (phase 3) are read too,
once detect_replies has handed the lead to an SDR. The minutes a triaged reply takes the SDR are a setting of their
own (capacity.py), read by the cost report and the simulator.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from typing import Optional, Sequence

from ..capacity import replies_auto
from ..hubspot.client import HubSpotClient
from ..hubspot.models import Engagement, SDR_LIFECYCLE_STAGES
from ..llm import usage
from ..llm.client import LLM
from ..llm.emails import sender_first_name
from ..llm.replies import CLOSES, Triage, triage
from ..storage.db import clock, get_setting, now_iso, set_setting

CURSOR = "reply_triage_cursor"
DEFAULT_LIMIT = 10
OOO_PUSH_DAYS = 7           # an out-of-office with no date named: the follow-ups wait a week


def rows_for(conn: sqlite3.Connection, contact_id: str) -> list[dict]:
    """The lead's triaged replies, oldest first: {engagement_id, kind, summary, until, subject, body, source, acted, created_at}."""
    return [dict(r) for r in conn.execute("SELECT * FROM replies WHERE contact_id=? ORDER BY id", (contact_id,)).fetchall()]


def latest(conn: sqlite3.Connection, contact_id: str) -> Optional[dict]:
    r = conn.execute("SELECT * FROM replies WHERE contact_id=? ORDER BY id DESC LIMIT 1", (contact_id,)).fetchone()
    return dict(r) if r else None


def without_auto_replies(conn: sqlite3.Connection, history: Sequence[Engagement]) -> list[Engagement]:
    """The lead's activity with out-of-office auto-replies left out: they are not the lead answering."""
    ooo = {r["engagement_id"] for r in conn.execute("SELECT engagement_id FROM replies WHERE kind='out_of_office'")}
    return [e for e in history if e.id not in ooo]


def _act(hs: HubSpotClient, conn: sqlite3.Connection, c, owner, e: Engagement, t: Triage, now: datetime) -> str:
    """Do the plain part and say what was done."""
    from .followups import stop
    from . import scheduling
    if t.kind in CLOSES:
        hs.batch_update_contacts([(c.id, {"hs_lead_status": "UNQUALIFIED"})])
        conn.execute("INSERT INTO outcomes(contact_id, worked, first_touch_at, updated_at) VALUES(?,?,?,?) ON CONFLICT(contact_id) DO UPDATE SET "
                     "worked=1, updated_at=excluded.updated_at", (c.id, 1, now_iso(), now_iso()))
        stop(conn, c.id, "the lead replied")
        return "closed in HubSpot" + ("; set their email subscription there too" if t.kind == "unsubscribe" else "")
    if t.kind == "out_of_office":
        back = datetime.fromisoformat(t.until) if t.until else e.timestamp + timedelta(days=OOO_PUSH_DAYS)
        back = max(back, e.timestamp + timedelta(days=1))
        n = conn.execute("UPDATE followups SET due_at=?, updated_at=? WHERE contact_id=? AND status='due' AND due_at < ?",
                         (back.isoformat(timespec="seconds"), now_iso(), c.id, back.isoformat(timespec="seconds"))).rowcount
        return f"follow-ups moved to {back.date().isoformat()}" if n else "nothing to move"
    stop(conn, c.id, "the lead replied")
    if t.kind == "not_now":
        return f"come back {t.until}" if t.until else "come back later"
    booked = scheduling.act(hs, conn, c, owner, e, t, now)
    if booked:
        return booked
    return "answer drafted" if t.answered else "handed to the SDR"


def triage_new(hs: HubSpotClient, conn: sqlite3.Connection, llm: LLM, now: Optional[datetime] = None, limit: int = DEFAULT_LIMIT) -> dict:
    """Read every reply HubSpot logged since the last run on a lead an SDR owns, say what it is, act, and record it.
    Returns {read, triaged, by kind..., waiting}. A no-op while the switch is off."""
    if not replies_auto(conn):
        return {"auto": False}
    now = now or clock(conn)
    cursor = get_setting(conn, CURSOR)
    since = datetime.fromisoformat(cursor) - timedelta(minutes=5) if cursor else datetime.utcnow() - timedelta(days=1)
    engagements = hs.engagements_logged_since(since)
    out: dict = {"auto": True, "read": len(engagements), "triaged": 0, "waiting": 0}
    owners = {o.id: o for o in hs.list_owners(active_only=False)}
    for e in sorted(engagements, key=lambda x: (x.timestamp, x.logged_at)):
        if e.direction != "INCOMING" or e.type != "EMAIL":
            continue
        if conn.execute("SELECT 1 FROM replies WHERE engagement_id=?", (e.id,)).fetchone():
            continue
        if out["triaged"] >= limit:
            out["waiting"] += 1
            continue
        c = hs.get_contact(e.contact_id)
        if c is None or not c.hubspot_owner_id or c.lifecycle_stage not in SDR_LIFECYCLE_STAGES:
            continue
        owner = owners.get(c.hubspot_owner_id)
        history = [h for h in hs.engagements_for_contact(c.id) if h.id != e.id and h.timestamp <= e.timestamp]
        co = hs.get_company(c.company_id) if c.company_id else None
        with usage.reference(c.id):
            t = triage(llm, text=e.body, subject=e.subject, first_name=c.first_name, sender=sender_first_name(owner.name if owner else None),
                       company=co.name if co else "", notes=c.notes, history=without_auto_replies(conn, history),
                       booking_link=owner.meetings_link if owner else "", now=now)
        acted = _act(hs, conn, c, owner, e, t, now)
        conn.execute("INSERT INTO replies(contact_id, engagement_id, owner_id, kind, summary, until, subject, body, source, rejected, acted, replied_at, created_at, sent_at) "
                     "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (c.id, e.id, c.hubspot_owner_id, t.kind, t.summary, t.until, t.subject, t.body, t.source,
                                                             t.rejected, acted, e.timestamp.isoformat(timespec="seconds"), now_iso(),
                                                             now.isoformat(timespec="seconds") if t.source == "scheduling" else None))
        conn.commit()
        out["triaged"] += 1
        out[t.kind] = out.get(t.kind, 0) + 1
    if engagements:
        set_setting(conn, CURSOR, max(e.logged_at for e in engagements).isoformat(timespec="seconds"))
    elif not cursor:
        set_setting(conn, CURSOR, since.isoformat(timespec="seconds"))
    return out
