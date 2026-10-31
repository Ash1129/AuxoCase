"""One lead in full: the queue's detail pane and the lead page share it."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from ...data.generate import LATE_TOUCH_FACTOR
from ...feedback.loop import speed_effect, speed_to_lead
from ...hubspot.mock import MockHubSpot
from ...hubspot.models import CALL_DISPOSITIONS
from ...llm.emails import LAST_TOUCH, Draft, next_touch
from ...llm import usage
from ...capacity import followups_auto
from ...routing.followups import rows_for as followup_rows
from ...routing.outreach import call_brief_for, draft_for, stored_draft
from ...scoring.explain import driver_rows
from ...storage.db import clock, get_setting, loads
from ..security import lead_token

FIRST_DAY_HOURS = 24
WARN_AFTER_HOURS = 18          # the clock turns to a warning in the last six hours of the first day
SMALL_BAND = 30                # a calibration band with fewer leads than this is "too few to say"
ASSIGNED = ("assigned", "holdout_assigned")
DECISIONS = ("worked", "junk", "skipped")     # an SDR's call that takes the lead out of their queue
EMAIL_LABELS = {1: "First email", 2: "Follow-up", 3: "Last email"}
ACTION_WORDS = {"worked": "marked it worked", "junk": "marked it junk", "skipped": "skipped it",
                "promote": "moved it up", "demote": "moved it down"}
TOUCHES_READ = "scheduler.last_run.touches"   # when the scheduler last read HubSpot for first touches


def _span(hours: float) -> str:
    """'23h 23m', '2d 4h'."""
    if hours < 0:
        hours = 0
    if hours >= 48:
        return f"{int(hours // 24)}d {int(hours % 24)}h"
    return f"{int(hours)}h {int(round((hours % 1) * 60)):02d}m"


def _join(words: list[str]) -> str:
    """"a", "a and b", "a, b and c"."""
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]


def when(v) -> str:
    """'30 Oct 2028 00:00' from a datetime or an ISO timestamp; '' when there is none."""
    if isinstance(v, str):
        try:
            v = datetime.fromisoformat(v)
        except ValueError:
            return v
    return v.strftime("%d %b %Y %H:%M") if v else ""


def months_text(months) -> str:
    """['2028-05', '2028-06'] -> 'May and June 2028'; months in different years keep their own year."""
    ds = [datetime.strptime(m, "%Y-%m") for m in months or []]
    if not ds:
        return ""
    if len({d.year for d in ds}) == 1:
        return f"{_join([d.strftime('%B') for d in ds])} {ds[0].year}"
    return _join([d.strftime("%B %Y") for d in ds])


def first_day(created, now) -> dict:
    """The first-day clock: how long since the lead arrived and when the day runs out. Counts from arrival, which is
    what the freshness discount and the speed report use."""
    age_h = (now - created).total_seconds() / 3600
    touch_by = created + timedelta(hours=FIRST_DAY_HOURS)
    state = "late" if age_h >= FIRST_DAY_HOURS else ("warn" if age_h >= WARN_AFTER_HOURS else "fresh")
    return {"age": _span(age_h), "age_hours": round(age_h, 1), "touch_by": touch_by.strftime("%d %b %H:%M"),
            "state": state, "used": min(1.0, max(0.0, age_h / FIRST_DAY_HOURS))}


def calibration_band(metrics: Optional[dict], p: Optional[float]) -> Optional[dict]:
    """The live model's calibration bin that holds `p`: how often leads scored like this booked, and how many.
    `predicted` is the bin's mean score; `months` names the test months the bin was counted on."""
    if not metrics or p is None:
        return None
    for b in metrics.get("calibration_bins") or []:
        if b["lo"] <= p < b["hi"] or (b["hi"] >= 1 and p <= b["hi"]):
            return {"lo": round(100 * b["lo"]), "hi": round(100 * b["hi"]), "n": b["n"], "observed": b["observed"],
                    "too_few": b["n"] < SMALL_BAND, "predicted": b.get("predicted"),
                    "months": months_text(metrics.get("validation_months"))}
    return None


def _call_steps(activity, first_touch) -> list[dict]:
    """The SDR's calls so far, oldest first, each with HubSpot's outcome: connected calls count as a conversation."""
    calls = sorted((e for e in activity if e.type == "CALL" and e.direction == "OUTGOING"), key=lambda e: e.timestamp)
    return [{"when": when(e.timestamp), "state": "sent" if e.connected else "later",
             "note": (CALL_DISPOSITIONS.get(e.disposition) or ("connected" if e.connected else "no answer")).lower(),
             "first": bool(first_touch and first_touch["engagement_id"] == e.id)} for e in calls]


def _email_steps(activity, touch: Optional[int], no_draft: str, email: Optional[Draft], followups: Optional[dict] = None) -> list[dict]:
    """The three emails of the sequence and where each stands: sent (seen in HubSpot; by the service when it sent
    it), next, due (the service will send it that day), later, or stopped."""
    sent = sorted((e for e in activity if e.type == "EMAIL" and e.direction == "OUTGOING"), key=lambda e: e.timestamp)
    followups = followups or {}
    steps = []
    for n in range(1, LAST_TOUCH + 1):
        f = followups.get(n)
        if n <= len(sent):
            state = "sent"
            note = ("sent for you " if f and f["status"] == "sent" else "sent, seen in HubSpot ") + when(sent[n - 1].timestamp)
        elif f and f["status"] == "due":
            state, note = "due", "sent for you on " + when(f["due_at"]) + " unless they answer"
        elif f and f["status"] == "stopped":
            state, note = "stopped", "not sent: " + f["reason"]
        elif n == touch:
            state, note = "next", "next, drafted below" if email and email.touch == n else "next, not drafted yet"
        elif no_draft == "replied":
            state, note = "stopped", "not needed: the lead replied"
        else:
            state, note = "later", "not yet"
        steps.append({"n": n, "label": EMAIL_LABELS[n], "state": state, "note": note})
    return steps


def _timeline(c, score, routing, stored, first_touch, history, owners, followups=()) -> list[dict]:
    """What happened to the lead, in the order it happens: created, scored, routed, drafted, touched, decided.
    `clock` says which clock stamped it: HubSpot and routing run on the service clock ("demo" on the mock),
    AuxoCase's own logs on wall time ("wall")."""
    name = lambda oid: owners[oid].name if oid in owners else (f"owner {oid}" if oid else "nobody")
    out = [{"when": when(c.createdate), "clock": "demo", "what": "Created in HubSpot"}]
    if score:
        place = f", #{score['rank']} of {score['pool_size']:,}" if score.get("rank") and score.get("pool_size") else ""
        out.append({"when": when(score["created_at"]), "clock": "wall",
                    "what": f"Scored by {score['model_version']}: {round(100 * (score['probability'] or 0))}%{place}"})
    for r in routing:
        if r["decision"] == "assigned":
            what = f"Assigned to {name(r['owner_id'])}" + (f", {r['reason']}" if r["reason"] else "")
        elif r["decision"] == "holdout_assigned":
            what = f"Assigned to {name(r['owner_id'])} for the exploration slice"
        else:
            what = r["decision"].replace("_", " ").capitalize() + (f": {r['reason']}" if r["reason"] else "")
        out.append({"when": when(r["created_at"]), "clock": "demo", "what": what})
    if stored:
        out.append({"when": when(stored["created_at"]), "clock": "wall",
                    "what": "First email drafted" + ("; enrolled in the sequence" if stored["status"] == "enrolled" else "")})
    if first_touch:
        out.append({"when": when(first_touch["touched_at"]), "clock": "demo",
                    "what": f"First touch: {(first_touch['type'] or 'activity').lower()} seen in HubSpot"})
    for h in history:
        out.append({"when": when(h["created_at"]), "clock": "wall",
                    "what": f"{name(h['owner_id'])} {ACTION_WORDS.get(h['action'], h['action'])}" + (f" ({h['note']})" if h["note"] else "")})
    for f in followups:
        if f["status"] == "sent":
            out.append({"when": when(f["sent_at"]), "clock": "demo", "what": f"{EMAIL_LABELS[f['step']]} sent for {name(f['owner_id'])}"})
        elif f["status"] == "stopped":
            out.append({"when": when(f["updated_at"]), "clock": "wall", "what": f"{EMAIL_LABELS[f['step']]} not sent: {f['reason']}"})
    return out


def lead_detail(st, contact_id: str, draft: bool = False, call: bool = False) -> Optional[dict]:
    """Everything known about one lead, or None if HubSpot has no such contact.

    c, co: the contact and its company. score: the latest scores row, with `contributions` and `signals` parsed and
    `when` (its time, formatted); drivers: the score breakdown rows (label, better, word, effect, bar); band: the
    calibration bin for its probability under the live model (lo, hi, n, observed, too_few, predicted, months);
    clock: the first-day clock (age, touch_by, state fresh|warn|late, used 0..1).
    history: SDR actions (overrides), routing: routing log rows, each with `when`. activity: emails and calls seen in
    HubSpot; activity_rows: the same as {e, when, first (it was the first touch), after (time since assignment)}.
    first_touch: the touches row, if the first touch was seen, with `when`; touched_in: {span, inside (the first
    day), at} from arrival to that touch. touch: which email is next (1, 2, 3, or None) and no_draft why not;
    emails: the sequence's three steps {n, label, state sent|next|later|stopped, note}; email: the draft shown (the
    stored first email, or one drafted on request); call: the call brief (llm/calls.py CallBrief), prepared when
    `call` is asked for, else None; stored: the outreach row (status enrolled|drafted|refused,
    sequence_id) with `when`. mock: the demo stand-in for HubSpot is on, with enrollments listed. seen: the version
    token lead forms must carry. owner: the owning SDR, if any; owners: every owner by id.
    assigned: the latest assignment's routing row; decision: the SDR's latest worked/junk/skipped call (overrides
    row), if any; slot_next: {c, when} the next lead assigned to the same SDR after the first touch freed the slot.
    speed: first-day effect measured from outcomes (feedback.loop.speed_effect: n_fast, n_slow, and rate_fast,
    rate_slow, factor once both sides have 100 leads); late_factor: on the mock, the generator's built-in factor for
    a lead touched after its first day, else None. pace: hours from arrival to first touch (speed_to_lead: routed
    median_hours, and `baseline` for the history). watch: the channel monitor's latest month for the lead's channel
    (period, n, demos, demo_rate, predicted_rate, lcl, ucl, flag ok|above|below|held, month). enrichment: rows from
    the enrichment log with `filled` parsed. timeline: _timeline's entries {when, clock demo|wall, what}.
    last_read: when the scheduler last read HubSpot for first touches, or ''.
    """
    c = st.hs.get_contact(contact_id)
    if not c:
        return None
    co = st.hs.get_company(c.company_id) if c.company_id else None
    score = st.conn.execute("SELECT * FROM scores WHERE contact_id=? ORDER BY id DESC LIMIT 1", (contact_id,)).fetchone()
    score = dict(score) if score else None
    if score:
        score["contributions"] = loads(score["contributions"]) or []
        score["signals"] = loads(score["signals"]) or {}
        score["when"] = when(score["created_at"])
    live = st.conn.execute("SELECT metrics FROM model_registry WHERE status='live' ORDER BY promoted_at DESC LIMIT 1").fetchone()
    history = [{**dict(r), "when": when(r["created_at"])} for r in st.conn.execute("SELECT * FROM overrides WHERE contact_id=? ORDER BY id", (contact_id,)).fetchall()]
    routing = [{**dict(r), "when": when(r["created_at"])} for r in st.conn.execute("SELECT * FROM routing_log WHERE contact_id=? ORDER BY id", (contact_id,)).fetchall()]
    activity = st.hs.engagements_for_contact(contact_id)
    touch, no_draft = next_touch(activity) if c.email else (None, "no email address")
    stored = stored_draft(st.conn, contact_id)
    if stored:
        stored["when"] = when(stored["created_at"])
    email = None
    if draft and touch:
        # Drafted on request only: with Claude on, a draft is a model call, too slow for every page view.
        email, no_draft = draft_for(st.hs, st.conn, st.llm, c, activity)
    elif touch == 1 and stored:
        email = Draft(stored["subject"], stored["body"], stored["source"], stored["rejected"] or "", st.llm.name)
    brief = None
    if call:
        with usage.reference(contact_id):
            brief = call_brief_for(st.hs, st.conn, st.llm, c, activity)
    first_touch = st.conn.execute("SELECT * FROM touches WHERE contact_id=?", (contact_id,)).fetchone()
    first_touch = {**dict(first_touch), "when": when(first_touch["touched_at"])} if first_touch else None
    mock = isinstance(st.hs, MockHubSpot)
    owners = {o.id: o for o in st.hs.list_owners(active_only=False)}
    owner = owners.get(c.hubspot_owner_id)

    assigned = next((r for r in reversed(routing) if r["decision"] in ASSIGNED), None)
    assigned_at = datetime.fromisoformat(assigned["created_at"]) if assigned else None
    activity_rows = [{"e": e, "when": when(e.timestamp), "first": bool(first_touch and first_touch["engagement_id"] == e.id),
                      "after": _span((e.timestamp - assigned_at).total_seconds() / 3600) if assigned_at and e.timestamp >= assigned_at else ""}
                     for e in activity]
    touched_in, slot_next = None, None
    if first_touch:
        hours = (datetime.fromisoformat(first_touch["touched_at"]) - c.createdate).total_seconds() / 3600
        touched_in = {"span": _span(hours), "inside": hours <= FIRST_DAY_HOURS, "at": first_touch["when"]}
        nxt = st.conn.execute(
            "SELECT contact_id, created_at FROM routing_log WHERE owner_id=? AND decision IN ('assigned','holdout_assigned') "
            "AND created_at >= ? AND contact_id != ? ORDER BY created_at, id LIMIT 1",
            (first_touch["owner_id"], first_touch["touched_at"], contact_id)).fetchone()
        nc = st.hs.get_contact(nxt["contact_id"]) if nxt else None
        slot_next = {"c": nc, "when": when(nxt["created_at"])} if nc else None
    watch = st.conn.execute("SELECT * FROM channel_monitor WHERE channel=? ORDER BY period DESC, id DESC LIMIT 1", (c.channel,)).fetchone()
    watch = {**dict(watch), "month": months_text([watch["period"]])} if watch else None
    enrichment = [{**dict(r), "filled": loads(r["filled"]) or {}, "when": when(r["created_at"])}
                  for r in st.conn.execute("SELECT * FROM enrichment_log WHERE contact_id=? ORDER BY id", (contact_id,)).fetchall()]
    fups = [{**f, "when": when(f["due_at"])} for f in followup_rows(st.conn, contact_id)]
    by_step = {f["step"]: f for f in fups}
    t2 = st.conn.execute("SELECT * FROM tier2 WHERE contact_id=?", (contact_id,)).fetchone()
    t2 = {**dict(t2), "when": when(t2["created_at"]), "replied_when": when(t2["replied_at"]) if t2["replied_at"] else None} if t2 else None
    return {"c": c, "co": co, "score": score, "drivers": driver_rows((score or {}).get("contributions")), "tier2": t2,
            "band": calibration_band(loads(live["metrics"]) if live else None, c.auxo_probability),
            "clock": first_day(c.createdate, clock(st.conn)), "history": history, "routing": routing,
            "activity": activity, "first_touch": first_touch, "touch": touch,
            "no_draft": no_draft, "email": email, "call": brief, "stored": stored, "mock": mock, "dispositions": CALL_DISPOSITIONS,
            "enrollments": st.hs.sequence_enrollments(contact_id) if mock else [], "seen": lead_token(st.conn, c),
            "owner": owner, "owners": owners, "assigned": assigned,
            "decision": next((h for h in reversed(history) if h["action"] in DECISIONS), None),
            "activity_rows": activity_rows, "touched_in": touched_in, "slot_next": slot_next,
            "emails": _email_steps(activity, touch, no_draft, email, by_step), "calls": _call_steps(activity, first_touch),
            "followups": {"auto": followups_auto(st.conn), "rows": fups, "due": [f for f in fups if f["status"] == "due"],
                          "next": next((f for f in fups if f["status"] == "due"), None)},
            "speed": speed_effect(st.conn),
            "late_factor": LATE_TOUCH_FACTOR if mock else None, "pace": speed_to_lead(st.conn), "watch": watch,
            "enrichment": enrichment, "timeline": _timeline(c, score, routing, stored, first_touch, history, owners, fups),
            "last_read": when(get_setting(st.conn, TOUCHES_READ))}
