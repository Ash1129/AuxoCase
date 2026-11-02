"""The team's capacity, in leads worked a month, from SDR minutes.

The brief fixes the team at 12 SDRs working about 500 MQLs a month. That 500 is SDR time: each SDR spends so many
hours a month working leads, and a lead takes so many minutes: a cold first touch, then the follow-up and the last
email when the lead does not answer. Holding the hours and dividing by the minutes gives the capacity, so anything
that makes a lead quicker (a draft ready to send, a call brief, the follow-ups sent without the SDR) shows up here
as more leads worked by the same team. Routing's capacity line, the Overview, the Leadership page, the simulator
and the cost report all read this one number.

The inputs are settings with defaults, not facts: `sdr_minutes_touch` (20, a cold first touch: read the reason
and the notes, write or call), `sdr_minutes_followup` (5, each of the two later emails when the SDR writes and
sends them), `sdr_minutes_reply` (10, answering a lead who replied to the sequence) and `sdr_touch_hours_month`
(the hours each SDR gives leads a month, set so the defaults reproduce the brief's 500). `followups_auto` ('1')
has the service send the follow-up and the last email itself (routing/followups.py), so their minutes drop out.
`replies_auto` ('1') has the service read each reply, draft the answer and close the ones that are over
(routing/replies.py); a reply then takes `sdr_minutes_reply_triaged` (4) instead. `call_notes_auto` ('1') has
the service write up the SDR's rough call notes (routing/callnotes.py): logging a call then takes
`sdr_minutes_call_log_auto` (1) of the cold touch instead of `sdr_minutes_call_log` (4), for the share of first
touches that are calls. The cold touch's minutes include the leads opened and skipped on the way to one worked;
`skips_auto` ('1') has routing hold the leads the SDRs would skip (routing/skips.py), so the `sdr_minutes_skip` (3)
each of those cost come off the touch. So do the minutes of booking a demo, `sdr_minutes_booking` (8, the back and
forth to fix a time and create the meeting), for the share of worked leads that book: `scheduling_auto` ('1') has
the service offer times, book the pick and confirm it (routing/scheduling.py), and the SDR spends
`sdr_minutes_booking_auto` (1) checking the invite instead. `team_capacity_per_month`, when set, overrides the
arithmetic with a plain number.

Two of the inputs are read from the SDRs' own work once there is enough of it (`measured`): the minutes a cold
touch is the median gap between one first touch and the next in a working session (the touches table, which the
first-touch poll fills from HubSpot's engagements, real or mock), and the hours an SDR gives leads a month is
the time in those sessions, scaled to a month. Each replaces its default when the evidence is there (MIN_GAPS
gaps across MIN_SDRS SDRs; MIN_ACTIVE_DAYS days with a touch for MIN_SDRS SDRs) and a typed figure wins over
both; typing the measured figure back drops the typed one.
"""
from __future__ import annotations

import sqlite3
from typing import Optional

from datetime import datetime, timedelta
from statistics import median

from .storage.db import get_setting, now_iso, set_setting

DEFAULT_SDRS = 12                                   # the brief's team
BRIEF_TOUCHES_PER_MONTH = 500                       # the brief: about 500 MQLs worked a month
FOLLOWUPS = 2                                       # the sequence's later emails: the follow-up and the last email
DEFAULTS = {
    "sdr_minutes_touch": 20.0,
    "sdr_minutes_followup": 5.0,
    "sdr_minutes_reply": 10.0,
    "sdr_minutes_reply_triaged": 4.0,
    "sdr_minutes_call_log": 4.0,            # of the cold touch, when it is a call: typing the notes, the fields, the task
    "sdr_minutes_call_log_auto": 1.0,       # the same with the notes written up for the SDR: a few fragments typed
    "sdr_minutes_skip": 3.0,                # opening a lead and skipping it: part of the cold touch's minutes
    "sdr_minutes_booking": 8.0,             # fixing a demo time with a lead who wants one and creating the meeting: part of the cold touch's minutes
    "sdr_minutes_booking_auto": 1.0,        # the same when the service offers the times, books the pick and confirms it
    "sdr_touch_hours_month": BRIEF_TOUCHES_PER_MONTH * (20.0 + FOLLOWUPS * 5.0) / 60 / DEFAULT_SDRS,   # 20.8 hours: the brief's 500 at 30 minutes a lead
}
LIMITS = {"sdr_minutes_touch": (1.0, 240.0), "sdr_minutes_followup": (0.0, 240.0), "sdr_minutes_reply": (1.0, 240.0),
          "sdr_minutes_reply_triaged": (0.0, 240.0), "sdr_minutes_call_log": (0.0, 240.0), "sdr_minutes_call_log_auto": (0.0, 240.0),
          "sdr_minutes_skip": (0.0, 240.0), "sdr_minutes_booking": (0.0, 240.0), "sdr_minutes_booking_auto": (0.0, 240.0),
          "sdr_touch_hours_month": (0.5, 200.0)}
LABELS = {"sdr_minutes_touch": "minutes a cold touch", "sdr_minutes_followup": "minutes a follow-up",
          "sdr_minutes_reply": "minutes a reply", "sdr_minutes_reply_triaged": "minutes a triaged reply",
          "sdr_minutes_call_log": "minutes logging a call", "sdr_minutes_call_log_auto": "minutes logging a call written up",
          "sdr_minutes_skip": "minutes a skip", "sdr_minutes_booking": "minutes booking a demo",
          "sdr_minutes_booking_auto": "minutes booking a demo booked for the SDR",
          "sdr_touch_hours_month": "hours an SDR gives leads a month"}
AUTO = "followups_auto"
REPLIES_AUTO = "replies_auto"
CALL_NOTES_AUTO = "call_notes_auto"
SKIPS_AUTO = "skips_auto"
SCHEDULING_AUTO = "scheduling_auto"
DEFAULT_DEMO_SHARE = 0.35               # of worked leads, booking a demo, until the outcomes say
DEFAULT_SKIP_SHARE = 0.2                # skips per worked lead, until the SDRs' decisions say
DEFAULT_CALL_SHARE = 0.5                # of first touches, when none has been seen yet
SESSION_GAP_MINUTES = 45                # a longer gap between two first touches ends a working session
MEASURE_WINDOW_DAYS = 30                # the touches read: the month up to the latest one
MIN_GAPS = 30                           # gaps between first touches, across MIN_SDRS SDRs, before the measured minutes count
MIN_SDRS = 2
MIN_ACTIVE_DAYS = 10                    # days with a touch, for each SDR whose hours count
MEASURED = ("sdr_minutes_touch", "sdr_touch_hours_month")
AUTO_SINCE = "followups_since"      # when the switch went on: only leads first emailed after it get automatic follow-ups


def unit(conn: sqlite3.Connection, key: str) -> float:
    """The figure in force: typed, else measured from the SDRs' touches when there is enough, else the default."""
    v = get_setting(conn, key)
    if v:
        return float(v)
    if key in MEASURED:
        m = measured(conn)
        if key == "sdr_minutes_touch" and m["minutes_in_force"]:
            return m["minutes_touch"]
        if key == "sdr_touch_hours_month" and m["hours_in_force"]:
            return m["hours"]
    return DEFAULTS[key]


def source(conn: sqlite3.Connection, key: str) -> str:
    """Where the figure in force comes from: 'typed', 'measured' or 'assumed'."""
    if get_setting(conn, key):
        return "typed"
    if key in MEASURED:
        m = measured(conn)
        if (key == "sdr_minutes_touch" and m["minutes_in_force"]) or (key == "sdr_touch_hours_month" and m["hours_in_force"]):
            return "measured"
    return "assumed"


def measured(conn: sqlite3.Connection) -> dict:
    """What the SDRs' first touches say, over the month up to the latest one: touches, sdrs (with a touch), days (with
    a touch), since (the window's first day, 'YYYY-MM-DD'), gaps (between consecutive touches in a session),
    minutes_touch (the median gap, None without one), minutes_in_force (MIN_GAPS gaps across MIN_SDRS SDRs), hours
    (the mean over the SDRs with MIN_ACTIVE_DAYS active days of their session time scaled to a month, None without
    one), hours_sdrs (how many), hours_in_force (MIN_SDRS of them). A session is a run of touches less than
    SESSION_GAP_MINUTES apart; its time is first to last plus one touch (the median gap, or the default until then).
    Touches with the same timestamp (a day with no time) give no gap but count as a day."""
    out = {"touches": 0, "sdrs": 0, "days": 0, "since": None, "gaps": 0, "minutes_touch": None, "minutes_in_force": False,
           "hours": None, "hours_sdrs": 0, "hours_in_force": False}
    rows = conn.execute("SELECT owner_id, touched_at FROM touches WHERE owner_id IS NOT NULL AND touched_at IS NOT NULL "
                        "ORDER BY owner_id, touched_at").fetchall()
    if not rows:
        return out
    stamps = [(r["owner_id"], datetime.fromisoformat(str(r["touched_at"])[:26].replace("Z", ""))) for r in rows]
    latest = max(t for _, t in stamps)
    start = latest - timedelta(days=MEASURE_WINDOW_DAYS)
    stamps = [(o, t) for o, t in stamps if t >= start]
    by_owner: dict[str, list[datetime]] = {}
    for o, t in stamps:
        by_owner.setdefault(o, []).append(t)
    gaps: list[float] = []
    sessions: dict[str, list[tuple[datetime, datetime]]] = {}
    for o, ts in by_owner.items():
        ts.sort()
        first = prev = ts[0]
        for t in ts[1:]:
            gap = (t - prev).total_seconds() / 60
            if gap > SESSION_GAP_MINUTES:
                sessions.setdefault(o, []).append((first, prev))
                first = t
            elif gap > 0:
                gaps.append(gap)
            prev = t
        sessions.setdefault(o, []).append((first, prev))
    out.update(touches=len(stamps), sdrs=len(by_owner), days=len({t.date() for _, t in stamps}), since=start.date().isoformat(), gaps=len(gaps))
    if gaps:
        out["minutes_touch"] = round(median(gaps), 1)
        out["minutes_in_force"] = len(gaps) >= MIN_GAPS and len(by_owner) >= MIN_SDRS
    one_touch = out["minutes_touch"] if out["minutes_in_force"] else DEFAULTS["sdr_minutes_touch"]
    hours = []
    for o, ts in by_owner.items():
        if len({t.date() for t in ts}) < MIN_ACTIVE_DAYS:
            continue
        minutes = sum((b - a).total_seconds() / 60 + one_touch for a, b in sessions[o])
        hours.append(minutes / 60 * 30.4 / MEASURE_WINDOW_DAYS)
    if hours:
        out["hours"] = round(sum(hours) / len(hours), 1)
        out["hours_sdrs"] = len(hours)
        out["hours_in_force"] = len(hours) >= MIN_SDRS
    return out


def followups_auto(conn: sqlite3.Connection) -> bool:
    return get_setting(conn, AUTO) == "1"


def replies_auto(conn: sqlite3.Connection) -> bool:
    return get_setting(conn, REPLIES_AUTO) == "1"


def minutes_per_reply(conn: sqlite3.Connection) -> float:
    """SDR minutes a reply takes: read from the inbox, or read for them with the answer drafted and the closed ones closed."""
    return unit(conn, "sdr_minutes_reply_triaged" if replies_auto(conn) else "sdr_minutes_reply")


def call_notes_auto(conn: sqlite3.Connection) -> bool:
    return get_setting(conn, CALL_NOTES_AUTO) == "1"


def call_share(conn: sqlite3.Connection) -> float:
    """The share of first touches that were calls, from what HubSpot has shown; a guess until it has shown any."""
    r = conn.execute("SELECT COUNT(*) n, COALESCE(SUM(type='CALL'), 0) calls FROM touches").fetchone()
    return r["calls"] / r["n"] if r and r["n"] else DEFAULT_CALL_SHARE


def call_log_saving(conn: sqlite3.Connection) -> float:
    """Minutes a lead saved on logging calls when the notes are written up: the share of touches that are calls times
    the minutes typing saves. Zero while the switch is off."""
    if not call_notes_auto(conn):
        return 0.0
    return call_share(conn) * max(0.0, unit(conn, "sdr_minutes_call_log") - unit(conn, "sdr_minutes_call_log_auto"))


def skips_auto(conn: sqlite3.Connection) -> bool:
    return get_setting(conn, SKIPS_AUTO) == "1"


def skip_figures(conn: sqlite3.Connection) -> dict:
    """The skips opened on the way to a worked lead, from the counts routing keeps of the SDRs' own decisions:
    {share (skips per worked lead; a guess until they have decided any), caught (the share of them routing would
    hold), saving (minutes a worked lead saved while the switch is on)}."""
    from .storage.db import loads
    model = loads(get_setting(conn, "skip_model")) or {}
    share = model.get("skip_share")
    share = DEFAULT_SKIP_SHARE if share is None else share
    caught = model.get("caught_share") or 0.0
    saving = share * caught * unit(conn, "sdr_minutes_skip") if skips_auto(conn) else 0.0
    return {"share": share, "caught": caught, "saving": round(saving, 1)}


def scheduling_auto(conn: sqlite3.Connection) -> bool:
    return get_setting(conn, SCHEDULING_AUTO) == "1"


def demo_share(conn: sqlite3.Connection) -> float:
    """The share of worked leads that booked a demo, from the outcomes synced; a guess until there are any."""
    r = conn.execute("SELECT COUNT(*) n, COALESCE(SUM(demo_booked), 0) booked FROM outcomes WHERE worked=1").fetchone()
    return r["booked"] / r["n"] if r and r["n"] else DEFAULT_DEMO_SHARE


def booking_saving(conn: sqlite3.Connection) -> float:
    """Minutes a lead saved on booking demos while the service does the back and forth: the share of worked leads
    that book times the minutes it saves on each. Zero while the switch is off."""
    if not scheduling_auto(conn):
        return 0.0
    return demo_share(conn) * max(0.0, unit(conn, "sdr_minutes_booking") - unit(conn, "sdr_minutes_booking_auto"))


def skip_saving(conn: sqlite3.Connection) -> float:
    """Minutes a lead saved on skips while routing holds the likely ones. Zero while the switch is off."""
    return skip_figures(conn)["saving"]


def minutes_per_lead(conn: sqlite3.Connection) -> float:
    """SDR minutes a lead takes: the cold touch (less what writing up the call notes saves, less the skips routing
    holds, less the demos booked for the SDR), plus the two later emails when the SDR sends them."""
    return (unit(conn, "sdr_minutes_touch") - call_log_saving(conn) - skip_saving(conn) - booking_saving(conn)
            + (0.0 if followups_auto(conn) else FOLLOWUPS * unit(conn, "sdr_minutes_followup")))


def touches_per_month(conn: sqlite3.Connection, sdrs: Optional[int] = None) -> float:
    """Leads the team can work a month: SDRs × hours × 60 ÷ minutes a lead, or the override."""
    override = get_setting(conn, "team_capacity_per_month")
    if override:
        return float(override)
    n = DEFAULT_SDRS if sdrs is None else sdrs
    return n * unit(conn, "sdr_touch_hours_month") * 60 / minutes_per_lead(conn)


def breakdown(conn: sqlite3.Connection, sdrs: Optional[int] = None) -> dict:
    """What the capacity is made of, for a page: {sdrs, minutes_touch, minutes_followup, minutes_reply, auto,
    minutes_lead, hours, per_month, per_sdr, override (the plain number when one is set, else None), brief}."""
    n = DEFAULT_SDRS if sdrs is None else sdrs
    per_month = touches_per_month(conn, n)
    override = get_setting(conn, "team_capacity_per_month")
    return {"sdrs": n, "minutes_touch": unit(conn, "sdr_minutes_touch"), "minutes_followup": unit(conn, "sdr_minutes_followup"),
            "minutes_reply": unit(conn, "sdr_minutes_reply"), "minutes_reply_triaged": unit(conn, "sdr_minutes_reply_triaged"),
            "replies_auto": replies_auto(conn), "minutes_reply_effective": minutes_per_reply(conn),
            "call_notes_auto": call_notes_auto(conn), "minutes_call_log": unit(conn, "sdr_minutes_call_log"),
            "minutes_call_log_auto": unit(conn, "sdr_minutes_call_log_auto"), "call_share": round(call_share(conn), 2),
            "call_log_saving": round(call_log_saving(conn), 1),
            "skips_auto": skips_auto(conn), "minutes_skip": unit(conn, "sdr_minutes_skip"), "skips": skip_figures(conn),
            "scheduling_auto": scheduling_auto(conn), "minutes_booking": unit(conn, "sdr_minutes_booking"),
            "minutes_booking_auto": unit(conn, "sdr_minutes_booking_auto"), "demo_share": round(demo_share(conn), 2),
            "booking_saving": round(booking_saving(conn), 1),
            "auto": followups_auto(conn), "minutes_lead": minutes_per_lead(conn),
            "hours": unit(conn, "sdr_touch_hours_month"), "per_month": round(per_month), "per_sdr": round(per_month / n, 1) if n else 0.0,
            "override": float(override) if override else None, "brief": BRIEF_TOUCHES_PER_MONTH,
            "measured": measured(conn), "minutes_touch_source": source(conn, "sdr_minutes_touch"),
            "hours_source": source(conn, "sdr_touch_hours_month"), "min_gaps": MIN_GAPS, "min_sdrs": MIN_SDRS, "min_days": MIN_ACTIVE_DAYS}


def set_assumptions(conn: sqlite3.Connection, minutes_touch: Optional[float] = None, minutes_followup: Optional[float] = None,
                    minutes_reply: Optional[float] = None, hours: Optional[float] = None, auto: Optional[bool] = None,
                    minutes_reply_triaged: Optional[float] = None, replies: Optional[bool] = None,
                    minutes_call_log: Optional[float] = None, minutes_call_log_auto: Optional[float] = None,
                    call_notes: Optional[bool] = None, minutes_skip: Optional[float] = None, skips: Optional[bool] = None,
                    minutes_booking: Optional[float] = None, minutes_booking_auto: Optional[float] = None,
                    scheduling: Optional[bool] = None) -> list[str]:
    """Set the inputs given (None leaves one as it is). Raises ValueError, with nothing set, when one is out of its
    range. Returns the keys that changed (a value equal to the current one at one decimal is unchanged). Setting any
    of them drops a plain-number override, so the arithmetic rules. Turning the follow-ups automatic stamps when, so
    only leads first emailed from then on get them."""
    wanted = {"sdr_minutes_touch": minutes_touch, "sdr_minutes_followup": minutes_followup, "sdr_minutes_reply": minutes_reply,
              "sdr_minutes_reply_triaged": minutes_reply_triaged, "sdr_minutes_call_log": minutes_call_log,
              "sdr_minutes_call_log_auto": minutes_call_log_auto, "sdr_minutes_skip": minutes_skip, "sdr_minutes_booking": minutes_booking,
              "sdr_minutes_booking_auto": minutes_booking_auto, "sdr_touch_hours_month": hours}
    for key, v in wanted.items():
        if v is None:
            continue
        lo, hi = LIMITS[key]
        if not (lo <= v <= hi):
            raise ValueError(f"{LABELS[key]} must be between {lo:g} and {hi:g}, not {v:g}")
    changed = []
    m = measured(conn)
    for key, v in wanted.items():
        if v is None:
            continue
        in_force = m["minutes_touch"] if key == "sdr_minutes_touch" and m["minutes_in_force"] else (
            m["hours"] if key == "sdr_touch_hours_month" and m["hours_in_force"] else None)
        if in_force is not None and round(v, 1) == round(in_force, 1) and get_setting(conn, key):
            conn.execute("DELETE FROM settings WHERE key = ?", (key,))      # back to the measured figure
            conn.commit()
            changed.append(key)
        elif round(v, 1) != round(unit(conn, key), 1):                     # the page shows one decimal: 20.8 is 20.833...
            set_setting(conn, key, f"{v:g}")
            changed.append(key)
    if auto is not None and auto != followups_auto(conn):
        set_setting(conn, AUTO, "1" if auto else "0")
        if auto:
            set_setting(conn, AUTO_SINCE, now_iso())
        changed.append(AUTO)
    if replies is not None and replies != replies_auto(conn):
        set_setting(conn, REPLIES_AUTO, "1" if replies else "0")
        changed.append(REPLIES_AUTO)
    if call_notes is not None and call_notes != call_notes_auto(conn):
        set_setting(conn, CALL_NOTES_AUTO, "1" if call_notes else "0")
        changed.append(CALL_NOTES_AUTO)
    if skips is not None and skips != skips_auto(conn):
        set_setting(conn, SKIPS_AUTO, "1" if skips else "0")
        changed.append(SKIPS_AUTO)
    if scheduling is not None and scheduling != scheduling_auto(conn):
        set_setting(conn, SCHEDULING_AUTO, "1" if scheduling else "0")
        changed.append(SCHEDULING_AUTO)
    if changed and get_setting(conn, "team_capacity_per_month"):
        conn.execute("DELETE FROM settings WHERE key = 'team_capacity_per_month'")
        conn.commit()
    return changed
