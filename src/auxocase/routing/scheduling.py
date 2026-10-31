"""Demos booked for the SDR: the times offered, the pick booked in HubSpot, the emails sent.

With `scheduling_auto` on (the Leadership page's capacity section), a lead who replies interested, or with a
question, is answered by the service with three free times from the SDR's calendar, sent for the SDR; a reply that
names a time books it: a meeting is created in HubSpot on the SDR, which outcome sync counts as the demo booked, the
follow-ups stop, and the confirmation goes out. A time already taken gets new times. The SDR sees it on the lead
page and the queue ("demo booked Tue 4 Nov, 14:00 ET") and does nothing. The minutes the back and forth took come
off the cold touch for the share of worked leads that book (capacity.py).

Every email sent is recorded on the reply it answers (replies.sent_at) and in `bookings`, with the times offered,
so the next reply's "the second one" means something. Replies the service does not understand stay the SDR's.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from typing import Optional

from ..capacity import scheduling_auto
from ..hubspot.client import HubSpotClient
from ..hubspot.models import Engagement
from ..llm.emails import sender_first_name
from ..llm.replies import Triage
from ..llm.scheduling import OFFER, SLOT_MINUTES, confirm_body, free, offer_body, picked_time, re_subject, slot_text, slots
from ..storage.db import dumps, loads, now_iso

LOOKBACK_DAYS = 30


def rows_for(conn: sqlite3.Connection, contact_id: str) -> list[dict]:
    """The lead's bookings, oldest first, `offered` parsed: {engagement_id, owner_id, meeting_id, start_at, offered, status, created_at}."""
    return [{**dict(r), "offered": loads(r["offered"]) or []} for r in conn.execute("SELECT * FROM bookings WHERE contact_id=? ORDER BY id", (contact_id,)).fetchall()]


def latest(conn: sqlite3.Connection, contact_id: str) -> Optional[dict]:
    rows = rows_for(conn, contact_id)
    return rows[-1] if rows else None


def booked(conn: sqlite3.Connection, contact_id: str) -> Optional[dict]:
    rows = [r for r in rows_for(conn, contact_id) if r["status"] == "booked"]
    return rows[-1] if rows else None


def busy_times(hs: HubSpotClient, owner_id: str, now: datetime) -> list[datetime]:
    return [m.start_time for m in hs.meetings_for_owner(owner_id, now - timedelta(days=1)) if m.outcome in ("SCHEDULED", "RESCHEDULED")]


def _send(hs: HubSpotClient, c, owner_id: Optional[str], subject: str, body: str, now: datetime) -> None:
    hs.batch_update_contacts([(c.id, {"auxo_email_subject": subject, "auxo_email_body": body})])
    if hasattr(hs, "simulate_sequence_send"):            # the mock: log the send as HubSpot would, from the SDR
        hs.log_engagement(c.id, "EMAIL", "OUTGOING", now, owner_id, subject=subject, body=body)


def _record(conn: sqlite3.Connection, c, e: Engagement, owner_id: Optional[str], meeting_id: Optional[str], start: Optional[datetime],
            offered: list[datetime], status: str) -> None:
    conn.execute("INSERT INTO bookings(contact_id, engagement_id, owner_id, meeting_id, start_at, offered, status, created_at) VALUES(?,?,?,?,?,?,?,?)",
                 (c.id, e.id, owner_id, meeting_id, start.isoformat(timespec="seconds") if start else None,
                  dumps([t.isoformat(timespec="seconds") for t in offered]), status, now_iso()))


def act(hs: HubSpotClient, conn: sqlite3.Connection, c, owner, e: Engagement, t: Triage, now: datetime) -> Optional[str]:
    """Handle the booking side of an interested reply (or a question): book the time it names, or offer times. Sets
    the reply's answer to what was sent and returns what was done, or None when scheduling is off or the reply is
    not one to book from."""
    if not scheduling_auto(conn) or owner is None or t.kind not in ("interested", "question") or not c.email:
        return None
    if booked(conn, c.id):
        return None                                   # a demo is on the calendar already: the reply is the SDR's to read
    last = latest(conn, c.id)
    offered = [datetime.fromisoformat(s) for s in last["offered"]] if last else []
    busy = busy_times(hs, owner.id, now)
    sender = sender_first_name(owner.name)
    pick = datetime.fromisoformat(t.when) if t.when else picked_time(e.body, now, offered)
    if pick and pick > now and free(pick, busy):
        meeting = hs.create_meeting(owner.id, c.id, pick, pick + timedelta(minutes=SLOT_MINUTES),
                                    f"BrightPath demo: {c.first_name} {c.last_name}".strip(), created_at=now)
        t.subject, t.body, t.source = re_subject(e.subject), confirm_body(c.first_name, sender, pick), "scheduling"
        t.summary = f"Picked {slot_text(pick)}."
        _send(hs, c, owner.id, t.subject, t.body, now)
        _record(conn, c, e, owner.id, meeting.id, pick, [], "booked")
        return f"demo booked for {slot_text(pick)}; confirmation sent"
    times = slots(busy, now, OFFER)
    if not times:
        return None
    clash = bool(pick)
    t.subject, t.body, t.source = re_subject(e.subject), offer_body(c.first_name, sender, times, owner.meetings_link, after_clash=clash), "scheduling"
    _send(hs, c, owner.id, t.subject, t.body, now)
    _record(conn, c, e, owner.id, None, pick if clash else None, times, "clash" if clash else "offered")
    listed = ", ".join(slot_text(x) for x in times)
    return (f"{slot_text(pick)} is taken; " if clash else "") + f"times offered: {listed}; sent"
