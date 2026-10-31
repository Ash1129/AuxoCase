"""Outreach on assignment: draft the first email and put it in the SDR's HubSpot sequence.

When routing gives a lead to an SDR, this job drafts the first email (Claude
when it is on, checked by code, else the template), adds the SDR's booking
link, writes the draft to the lead's `auxo_email_subject` and
`auxo_email_body` properties, and enrols the lead in the sequence set by
`AUXO_SEQUENCE_ID` (or the `sequence_id` setting), sending from the SDR's
inbox. The sequence's first step is a manual email task whose subject and
body are those two property tokens, so the draft waits in the SDR's HubSpot
task queue to be read, edited and sent; nothing goes out unapproved. Later
steps are the sequence's own; HubSpot unenrolls the lead when they reply or
book a meeting. With no sequence set, the draft is still written to the
lead and shown on the lead page.

The job walks the routing log forward from where it stopped, so each
assignment is looked at once. A lead already touched, or no longer the same
SDR's, is passed over. Drafting with Claude takes seconds a lead, so at most
`limit` leads are drafted per run; the rest wait for the next.

`draft_for` is the one way a draft is made for a lead, here and on the lead
page: it reads the lead's HubSpot activity to pick the next email.
`call_brief_for` is its counterpart for the phone: the brief the SDR reads
before dialling, from the same record plus the score's drivers and the
team's loss reasons for companies of this size.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from typing import Optional

from ..hubspot.client import HubSpotClient
from ..hubspot.models import CALL_DISPOSITIONS, WAITING_LEAD_STATUSES, Contact, Engagement
from ..llm import usage
from ..llm.calls import CallBrief, draft_call
from ..llm.client import LLM
from ..llm.emails import Draft, draft_email, next_touch, sender_first_name
from ..storage.db import clock, get_setting, loads, now_iso, set_setting

CURSOR = "outreach_cursor"
DEFAULT_LIMIT = 10
SCAN = 500


def sequence_id(conn: sqlite3.Connection) -> str:
    return os.environ.get("AUXO_SEQUENCE_ID") or get_setting(conn, "sequence_id") or ""


def latest_signals(conn: sqlite3.Connection, contact_id: str) -> list[dict]:
    r = conn.execute("SELECT signals FROM scores WHERE contact_id=? ORDER BY id DESC LIMIT 1", (contact_id,)).fetchone()
    return ((loads(r["signals"]) or {}).get("signals", []) if r else []) or []


def draft_for(hs: HubSpotClient, conn: sqlite3.Connection, llm: LLM, c: Contact,
              history: Optional[list[Engagement]] = None) -> tuple[Optional[Draft], str]:
    """The next email for this lead, or (None, why): 'replied', 'finished' or 'no email address'."""
    if not c.email:
        return None, "no email address"
    history = hs.engagements_for_contact(c.id) if history is None else history
    touch, why = next_touch(history)
    if touch is None:
        return None, why
    co = hs.get_company(c.company_id) if c.company_id else None
    owner = next((o for o in hs.list_owners(active_only=False) if o.id == c.hubspot_owner_id), None)
    return draft_email(llm, first_name=c.first_name, job_title=c.job_title, company=co.name if co else "", channel=c.channel,
                       referral_partner=c.referral_partner, reason=c.auxo_reason or "", signals=latest_signals(conn, c.id),
                       notes=c.notes, sender=sender_first_name(owner.name if owner else None), touch=touch, history=history,
                       booking_link=owner.meetings_link if owner else "", now=clock(conn)), ""


def call_brief_for(hs: HubSpotClient, conn: sqlite3.Connection, llm: LLM, c: Contact,
                   history: Optional[list[Engagement]] = None) -> CallBrief:
    """The call brief for this lead: opener, questions from the score's drivers, the likely objection, the ask with
    the SDR's booking link, and a voicemail."""
    from ..feedback.loop import loss_report
    from ..scoring.features import size_band
    history = hs.engagements_for_contact(c.id) if history is None else history
    co = hs.get_company(c.company_id) if c.company_id else None
    owner = next((o for o in hs.list_owners(active_only=False) if o.id == c.hubspot_owner_id), None)
    r = conn.execute("SELECT contributions FROM scores WHERE contact_id=? ORDER BY id DESC LIMIT 1", (c.id,)).fetchone()
    drivers = [(str(k), float(v)) for k, v in (loads(r["contributions"]) or [])] if r else []
    return draft_call(llm, first_name=c.first_name, job_title=c.job_title, company=co.name if co else "", channel=c.channel,
                      referral_partner=c.referral_partner, reason=c.auxo_reason or "", signals=latest_signals(conn, c.id),
                      notes=c.notes, sender=sender_first_name(owner.name if owner else None), drivers=drivers,
                      size_band=size_band(co.num_employees if co else None), loss_mix=loss_report(hs, conn), history=history,
                      booking_link=owner.meetings_link if owner else "", now=clock(conn))


def prepare_outreach(hs: HubSpotClient, conn: sqlite3.Connection, llm: LLM, limit: int = DEFAULT_LIMIT,
                     sequence: Optional[str] = None) -> dict:
    sequence = sequence_id(conn) if sequence is None else sequence
    cursor = int(get_setting(conn, CURSOR) or 0)
    rows = conn.execute("SELECT id, contact_id, owner_id FROM routing_log WHERE id > ? AND decision IN ('assigned','holdout_assigned') "
                        "ORDER BY id LIMIT ?", (cursor, SCAN)).fetchall()
    owners = {o.id: o for o in hs.list_owners(active_only=False)}
    counts = {"drafted": 0, "enrolled": 0, "refused": 0, "passed_over": 0}
    ensured = False
    for r in rows:
        if counts["drafted"] >= limit:
            break
        cursor = r["id"]
        if conn.execute("SELECT 1 FROM outreach WHERE contact_id=?", (r["contact_id"],)).fetchone():
            continue
        c = hs.get_contact(r["contact_id"])
        owner = owners.get(r["owner_id"])
        if c is None or owner is None or c.hubspot_owner_id != owner.id or c.hs_lead_status not in WAITING_LEAD_STATUSES:
            counts["passed_over"] += 1
            continue
        with usage.reference(c.id):
            draft, _ = draft_for(hs, conn, llm, c)
        if draft is None or draft.touch != 1:
            counts["passed_over"] += 1          # already emailed, or no address: nothing for step one
            continue
        if not ensured:
            hs.ensure_properties()
            ensured = True
        hs.batch_update_contacts([(c.id, {"auxo_email_subject": draft.subject, "auxo_email_body": draft.body})])
        enrollment = hs.enroll_in_sequence(c.id, sequence, owner.email) if sequence else None
        status = "drafted" if not sequence else ("enrolled" if enrollment else "refused")
        conn.execute("INSERT INTO outreach(contact_id, owner_id, subject, body, source, rejected, sequence_id, enrollment_id, status, created_at) "
                     "VALUES(?,?,?,?,?,?,?,?,?,?)", (c.id, owner.id, draft.subject, draft.body, draft.source, draft.rejected,
                                                     sequence or None, enrollment.id if enrollment else None, status, now_iso()))
        counts["drafted"] += 1
        if status != "drafted":
            counts[status] += 1
    set_setting(conn, CURSOR, str(cursor))
    counts["sequence"] = sequence or "none set"
    return counts


def stored_draft(conn: sqlite3.Connection, contact_id: str) -> Optional[dict]:
    r = conn.execute("SELECT * FROM outreach WHERE contact_id=?", (contact_id,)).fetchone()
    return dict(r) if r else None


def log_demo_activity(hs, conn: sqlite3.Connection, contact_id: str, kind: str, subject: str = "", disposition: str = "", body: str = "") -> None:
    """Mock only: stand in for the SDR (or the lead) acting in HubSpot, at the service clock's time. A call carries
    HubSpot's outcome (`disposition`, a CALL_DISPOSITIONS key); the older kinds name the two common ones."""
    c = hs.get_contact(contact_id)
    if c is None:
        raise KeyError(contact_id)
    at: datetime = clock(conn)
    if kind == "email":
        hs.log_engagement(contact_id, "EMAIL", "OUTGOING", at, c.hubspot_owner_id, subject=subject)
    elif kind in ("call", "call_no_answer", "call_connected"):
        disposition = {"call_no_answer": "no_answer", "call_connected": "connected"}.get(kind, disposition)
        if disposition not in CALL_DISPOSITIONS:
            raise ValueError(disposition)
        hs.log_engagement(contact_id, "CALL", "OUTGOING", at, c.hubspot_owner_id, disposition=disposition)
    elif kind == "reply":
        hs.log_engagement(contact_id, "EMAIL", "INCOMING", at, None, subject=f"Re: {subject}" if subject else "", body=body)
    else:
        raise ValueError(kind)
