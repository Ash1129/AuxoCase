"""The SDR's rough call notes written up and put where HubSpot keeps them.

The SDR logs the call in HubSpot with a few fragments in its notes box. With `call_notes_auto` on (the Leadership
page's capacity section), this job reads each new call that has notes (llm/callnotes.py writes them up) and does
what the SDR would have done with ten more minutes:

- the summary and what was learned go onto the lead's notes, which the next score reads (a stated timeline or a
  competitor's name is a signal the model uses);
- a company size learned goes onto the company;
- the next step becomes a task for the SDR on its day (call back, send the information, book the demo);
  "not interested" closes the lead;
- the follow-up email is drafted from the call, so it is ready to send.

HubSpot's own outcome on the call (connected, voicemail, ...) stands; the write-up only fills it when the call
has none. The minutes the SDR no longer spends typing come back as capacity (capacity.py).
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from typing import Optional

from ..capacity import call_notes_auto
from ..hubspot.client import HubSpotClient
from ..hubspot.models import SDR_LIFECYCLE_STAGES
from ..llm import usage
from ..llm.callnotes import WriteUp, write_up
from ..llm.client import LLM
from ..storage.db import clock, dumps, get_setting, loads, now_iso, set_setting
from .outreach import draft_for

CURSOR = "call_notes_cursor"
DEFAULT_LIMIT = 10
TASK_DAYS = {"demo": 1, "send_info": 1, "call_back": 3}     # when the notes name no date


def rows_for(conn: sqlite3.Connection, contact_id: str) -> list[dict]:
    """The lead's written-up calls, oldest first, `facts` parsed."""
    return [{**dict(r), "facts": loads(r["facts"]) or {}} for r in conn.execute("SELECT * FROM call_notes WHERE contact_id=? ORDER BY id", (contact_id,)).fetchall()]


def latest(conn: sqlite3.Connection, contact_id: str) -> Optional[dict]:
    rows = rows_for(conn, contact_id)
    return rows[-1] if rows else None


def _act(hs: HubSpotClient, conn: sqlite3.Connection, c, co, owner, e, w: WriteUp, now: datetime) -> str:
    done = []
    line = w.notes_line(e.timestamp)
    if hs.batch_update_contacts([(c.id, {"notes": (c.notes + "\n" if c.notes else "") + line})]).all_ok:
        done.append("notes")
    n = w.facts.get("employees")
    if n and co is not None and co.num_employees != n and hs.update_company(co.id, {"num_employees": n}):
        done.append(f"company size {n}")
    if w.next == "not_interested":
        hs.batch_update_contacts([(c.id, {"hs_lead_status": "UNQUALIFIED"})])
        done.append("closed in HubSpot")
    elif w.next in TASK_DAYS and owner is not None:
        due = datetime.fromisoformat(w.when) if w.when else now + timedelta(days=TASK_DAYS[w.next])
        due = max(due, now + timedelta(hours=1))
        hs.create_task(owner.id, c.id, f"{w.next_word[:1].upper()}{w.next_word[1:]}: {c.first_name} {c.last_name}".strip(), w.summary, due_at=due)
        done.append(f"task to {w.next_word} on {due.date().isoformat()}")
    if w.outcome and not e.disposition:
        done.append(f"outcome {w.outcome.replace('_', ' ')}")
    return ", ".join(done) if done else "nothing to write"


def write_up_new(hs: HubSpotClient, conn: sqlite3.Connection, llm: LLM, now: Optional[datetime] = None, limit: int = DEFAULT_LIMIT) -> dict:
    """Write up every call with notes HubSpot logged since the last run, on a lead an SDR owns, and put the write-up
    where it belongs. Returns {read, written, waiting}. A no-op while the switch is off."""
    if not call_notes_auto(conn):
        return {"auto": False}
    now = now or clock(conn)
    cursor = get_setting(conn, CURSOR)
    since = datetime.fromisoformat(cursor) - timedelta(minutes=5) if cursor else datetime.utcnow() - timedelta(days=1)
    engagements = hs.engagements_logged_since(since)
    out: dict = {"auto": True, "read": len(engagements), "written": 0, "waiting": 0}
    owners = {o.id: o for o in hs.list_owners(active_only=False)}
    for e in sorted(engagements, key=lambda x: (x.timestamp, x.logged_at)):
        if e.type != "CALL" or e.direction != "OUTGOING" or not (e.body or "").strip():
            continue
        if conn.execute("SELECT 1 FROM call_notes WHERE engagement_id=?", (e.id,)).fetchone():
            continue
        if out["written"] >= limit:
            out["waiting"] += 1
            continue
        c = hs.get_contact(e.contact_id)
        if c is None or not c.hubspot_owner_id or c.lifecycle_stage not in SDR_LIFECYCLE_STAGES:
            continue
        co = hs.get_company(c.company_id) if c.company_id else None
        owner = owners.get(e.owner_id or c.hubspot_owner_id)
        with usage.reference(c.id):
            w = write_up(llm, e.body, now=now, disposition=e.disposition)
            acted = _act(hs, conn, c, co, owner, e, w, now)
            draft, _ = draft_for(hs, conn, llm, hs.get_contact(c.id)) if c.email and w.next != "not_interested" else (None, "")
        conn.execute("INSERT INTO call_notes(contact_id, engagement_id, owner_id, summary, outcome, facts, next_step, next_when, subject, body, "
                     "source, rejected, acted, called_at, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (c.id, e.id, c.hubspot_owner_id, w.summary, w.outcome, dumps(w.facts), w.next, w.when, draft.subject if draft else "",
                      draft.body if draft else "", w.source, w.rejected, acted, e.timestamp.isoformat(timespec="seconds"), now_iso()))
        conn.commit()
        out["written"] += 1
    if engagements:
        set_setting(conn, CURSOR, max(e.logged_at for e in engagements).isoformat(timespec="seconds"))
    elif not cursor:
        set_setting(conn, CURSOR, since.isoformat(timespec="seconds"))
    return out
