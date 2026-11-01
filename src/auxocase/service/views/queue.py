"""An SDR's queue: the lead waiting for a first touch, the ones touched in HubSpot that still need a call logged,
and what they decided today.

The queue is drawn from the leads AuxoCase handed this SDR in the last QUEUE_DAYS (the routing log), not from
every contact they own: an SDR also owns months of leads worked before AuxoCase, and listing those buried the one
waiting lead. A lead an SDR has decided on (worked, skipped, junk) leaves the queue for "done today".
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from ...hubspot.models import SDR_LIFECYCLE_STAGES, WAITING_LEAD_STATUSES
from ...storage.db import clock, now_iso
from ...llm.replies import ANSWERED, WORDS as REPLY_WORDS
from ...llm.scheduling import slot_text
from .lead import first_day, lead_detail, when

QUEUE_DAYS = 30
DONE_SHOWN = 5


def queue_groups(st, owner_id: str) -> dict:
    """waiting: leads holding the SDR's slot (NEW/OPEN, no decision), best first. touched: leads whose first touch was
    seen in HubSpot and that have no decision yet, each with `seen_at`, `seen_when` (formatted) and `seen_type`
    (EMAIL|CALL). done: today's decisions (wall-clock day), newest first, at most DONE_SHOWN, with `done_total`; each
    overrides row carries `c` and `at` (its wall-clock time, HH:MM). Each lead entry is {c, co (its company), clock}."""
    since = (clock(st.conn) - timedelta(days=QUEUE_DAYS)).isoformat(timespec="seconds")
    ids = [r["contact_id"] for r in st.conn.execute(
        "SELECT contact_id FROM routing_log WHERE owner_id=? AND decision IN ('assigned','holdout_assigned') AND created_at >= ? "
        "UNION SELECT contact_id FROM touches WHERE owner_id=?", (owner_id, since, owner_id))]
    decided = {r["contact_id"] for r in st.conn.execute("SELECT DISTINCT contact_id FROM overrides WHERE action IN ('worked','junk','skipped')")}
    touches = {r["contact_id"]: dict(r) for r in st.conn.execute("SELECT contact_id, touched_at, type FROM touches WHERE owner_id=?", (owner_id,))}
    replied = {r["contact_id"]: r["replied_at"] for r in st.conn.execute("SELECT contact_id, replied_at FROM tier2 WHERE status='replied'")}
    triaged = {r["contact_id"]: dict(r) for r in st.conn.execute(
        "SELECT contact_id, kind, summary, replied_at, acted, sent_at FROM replies WHERE owner_id=? ORDER BY id", (owner_id,))}     # the latest wins
    booked = {r["contact_id"]: slot_text(datetime.fromisoformat(r["start_at"])) for r in st.conn.execute(
        "SELECT contact_id, start_at FROM bookings WHERE owner_id=? AND status='booked' ORDER BY id", (owner_id,))}
    now = clock(st.conn)
    waiting, touched = [], []
    for cid in ids:
        if cid in decided:
            continue
        c = st.hs.get_contact(cid)
        if c is None or c.hubspot_owner_id != owner_id:
            continue
        is_waiting = c.hs_lead_status in WAITING_LEAD_STATUSES and c.lifecycle_stage in SDR_LIFECYCLE_STAGES
        if cid not in touches and not is_waiting:
            continue
        entry = {"c": c, "co": st.hs.get_company(c.company_id) if c.company_id else None, "clock": first_day(c.createdate, now),
                 "replied": when(replied[cid]) if cid in replied else None,
                 "reply": {**triaged[cid], "when": when(triaged[cid]["replied_at"]), "word": REPLY_WORDS.get(triaged[cid]["kind"], triaged[cid]["kind"]),
                           "answer": triaged[cid]["kind"] in ANSWERED and not triaged[cid]["sent_at"], "sent": bool(triaged[cid]["sent_at"])} if cid in triaged else None,
                 "booked": booked.get(cid)}
        if cid in touches:
            t = touches[cid]
            touched.append({**entry, "seen_at": t["touched_at"], "seen_when": when(t["touched_at"]), "seen_type": t["type"] or ""})
        else:
            waiting.append(entry)
    # A lead that answered the automated sequence (phase 3) is warm and comes first, whatever its old score.
    waiting.sort(key=lambda e: (not e["replied"], -(e["c"].auxo_probability or 0)))
    touched.sort(key=lambda e: e["seen_at"], reverse=True)
    touched.sort(key=lambda e: not (e["reply"] and e["reply"]["answer"]))          # a reply waiting for its answer comes first
    today = now_iso()[:10]
    done_rows = [dict(r) for r in st.conn.execute(
        "SELECT * FROM overrides WHERE owner_id=? AND action IN ('worked','junk','skipped') AND created_at >= ? ORDER BY id DESC",
        (owner_id, today))]
    for r in done_rows[:DONE_SHOWN]:
        r["c"] = st.hs.get_contact(r["contact_id"])
        r["at"] = (r["created_at"] or "")[11:16]
    return {"waiting": waiting, "touched": touched, "done": done_rows[:DONE_SHOWN], "done_total": len(done_rows)}


TABS = ("untouched", "touched")


def queue_page(st, owner_id: str, lead: Optional[str] = None, draft: bool = False, call: bool = False, tab: str = "") -> Optional[dict]:
    """owner, the groups above, `tab` (the queue's two tabs: "untouched", the waiting group, and "touched", the leads
    waiting to hear back) and `open`: the lead_detail of the lead in view, or None when the tab is empty. `lead`
    picks the lead and its tab; otherwise `tab` picks the tab and its first lead, and no tab asked for means the
    untouched one while it has a lead, else the touched one. replies_waiting: touched leads whose reply has an answer
    drafted and not sent, for the untouched tab to point at. waiting_count/limit: the SDR's slot use."""
    owner = next((o for o in st.hs.list_owners() if o.id == owner_id), None)
    if not owner:
        return None
    g = queue_groups(st, owner_id)
    waiting_ids, touched_ids = [e["c"].id for e in g["waiting"]], [e["c"].id for e in g["touched"]]
    if lead in waiting_ids:
        tab, chosen = "untouched", lead
    elif lead in touched_ids:
        tab, chosen = "touched", lead
    else:
        if tab not in TABS:
            tab = "untouched" if waiting_ids or not touched_ids else "touched"
        ids = waiting_ids if tab == "untouched" else touched_ids
        chosen = ids[0] if ids else None
    replies_waiting = sum(1 for e in g["touched"] if e["reply"] and e["reply"].get("answer") and not e["reply"].get("sent"))
    return {"owner": owner, **g, "tab": tab, "open": lead_detail(st, chosen, draft, call) if chosen else None,
            "replies_waiting": replies_waiting, "waiting_count": len(g["waiting"]), "limit": owner.capacity_limit}
