"""First touch from HubSpot activity: the SDR works in HubSpot, the slot refills here.

SDRs email and call from HubSpot, which logs each one on the contact. This job
reads what HubSpot logged since its last run, and for an assigned lead still
waiting (NEW/OPEN) takes the first email or call the SDR made as the first
touch:

- the lead's status moves on (ATTEMPTED_TO_CONTACT, or CONNECTED when a call
  reached them), so it stops counting against the SDR's capacity;
- the outcome row gets the touch time, which is what speed-to-lead and the
  reminder SLA read, so no reminder goes out for a lead already contacted;
- routing runs at once, so the freed slot takes the next best lead.

Only outgoing activity counts: a lead emailing in is not the SDR's touch. A
lead an SDR has already moved on (clicked Worked, changed the status in
HubSpot) is left alone. The job pages by when HubSpot recorded the activity,
not when it happened, so a call logged an hour late is still seen; re-reading
the overlap is harmless because a lead is touched once.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from typing import Optional

from ..hubspot.client import HubSpotClient
from ..hubspot.models import WAITING_LEAD_STATUSES, Engagement
from ..storage.db import get_setting, now_iso, set_setting
from .engine import Router

CURSOR = "touch_cursor"
FIRST_LOOKBACK = timedelta(days=1)       # the first run reads a day back
OVERLAP = timedelta(minutes=5)           # re-read a little each run: HubSpot's search index lags writes


def is_sdr_touch(e: Engagement) -> bool:
    return e.direction == "OUTGOING" and e.type in ("EMAIL", "CALL")


def status_after(e: Engagement) -> str:
    return "CONNECTED" if e.type == "CALL" and e.connected else "ATTEMPTED_TO_CONTACT"


def detect_first_touches(hs: HubSpotClient, conn: sqlite3.Connection, now: Optional[datetime] = None,
                         refill: bool = True) -> dict:
    now = now or datetime.utcnow()
    cursor = get_setting(conn, CURSOR)
    since = datetime.fromisoformat(cursor) - OVERLAP if cursor else now - FIRST_LOOKBACK
    engagements = hs.engagements_logged_since(since)
    touched: list[str] = []
    for e in sorted(engagements, key=lambda x: (x.timestamp, x.logged_at)):
        if not is_sdr_touch(e) or e.contact_id in touched:
            continue
        if conn.execute("SELECT 1 FROM touches WHERE contact_id=?", (e.contact_id,)).fetchone():
            continue
        c = hs.get_contact(e.contact_id)
        if c is None or not c.hubspot_owner_id or c.hs_lead_status not in WAITING_LEAD_STATUSES:
            continue
        status = status_after(e)
        if not hs.batch_update_contacts([(c.id, {"hs_lead_status": status})]).all_ok:
            continue          # not recorded, so the next run tries again
        at = e.timestamp.isoformat(timespec="seconds")
        conn.execute("INSERT INTO outcomes(contact_id, worked, first_touch_at, updated_at) VALUES(?,?,?,?) "
                     "ON CONFLICT(contact_id) DO UPDATE SET worked=1, first_touch_at=COALESCE(outcomes.first_touch_at, excluded.first_touch_at), "
                     "updated_at=excluded.updated_at", (c.id, 1, at, now_iso()))
        conn.execute("INSERT INTO touches(contact_id, owner_id, engagement_id, type, touched_at, status_set, detected_at) VALUES(?,?,?,?,?,?,?)",
                     (c.id, c.hubspot_owner_id, e.id, e.type, at, status, now_iso()))
        touched.append(c.id)
    if engagements:
        set_setting(conn, CURSOR, max(e.logged_at for e in engagements).isoformat(timespec="seconds"))
    elif not cursor:
        set_setting(conn, CURSOR, since.isoformat(timespec="seconds"))
    out: dict = {"read": len(engagements), "first_touches": len(touched)}
    if touched and refill:
        d = Router(hs, conn).run()
        out["assigned"] = sum(1 for x in d if x.decision in ("assigned", "holdout_assigned"))
    return out
