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
(routing/replies.py); a reply then takes `sdr_minutes_reply_triaged` (4) instead. `team_capacity_per_month`, when
set, overrides the arithmetic with a plain number.
"""
from __future__ import annotations

import sqlite3
from typing import Optional

from .storage.db import get_setting, now_iso, set_setting

DEFAULT_SDRS = 12                                   # the brief's team
BRIEF_TOUCHES_PER_MONTH = 500                       # the brief: about 500 MQLs worked a month
FOLLOWUPS = 2                                       # the sequence's later emails: the follow-up and the last email
DEFAULTS = {
    "sdr_minutes_touch": 20.0,
    "sdr_minutes_followup": 5.0,
    "sdr_minutes_reply": 10.0,
    "sdr_minutes_reply_triaged": 4.0,
    "sdr_touch_hours_month": BRIEF_TOUCHES_PER_MONTH * (20.0 + FOLLOWUPS * 5.0) / 60 / DEFAULT_SDRS,   # 20.8 hours: the brief's 500 at 30 minutes a lead
}
LIMITS = {"sdr_minutes_touch": (1.0, 240.0), "sdr_minutes_followup": (0.0, 240.0), "sdr_minutes_reply": (1.0, 240.0),
          "sdr_minutes_reply_triaged": (0.0, 240.0), "sdr_touch_hours_month": (0.5, 200.0)}
LABELS = {"sdr_minutes_touch": "minutes a cold touch", "sdr_minutes_followup": "minutes a follow-up",
          "sdr_minutes_reply": "minutes a reply", "sdr_minutes_reply_triaged": "minutes a triaged reply",
          "sdr_touch_hours_month": "hours an SDR gives leads a month"}
AUTO = "followups_auto"
REPLIES_AUTO = "replies_auto"
AUTO_SINCE = "followups_since"      # when the switch went on: only leads first emailed after it get automatic follow-ups


def unit(conn: sqlite3.Connection, key: str) -> float:
    v = get_setting(conn, key)
    return float(v) if v else DEFAULTS[key]


def followups_auto(conn: sqlite3.Connection) -> bool:
    return get_setting(conn, AUTO) == "1"


def replies_auto(conn: sqlite3.Connection) -> bool:
    return get_setting(conn, REPLIES_AUTO) == "1"


def minutes_per_reply(conn: sqlite3.Connection) -> float:
    """SDR minutes a reply takes: read from the inbox, or read for them with the answer drafted and the closed ones closed."""
    return unit(conn, "sdr_minutes_reply_triaged" if replies_auto(conn) else "sdr_minutes_reply")


def minutes_per_lead(conn: sqlite3.Connection) -> float:
    """SDR minutes a lead takes: the cold touch, plus the two later emails when the SDR sends them."""
    return unit(conn, "sdr_minutes_touch") + (0.0 if followups_auto(conn) else FOLLOWUPS * unit(conn, "sdr_minutes_followup"))


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
            "auto": followups_auto(conn), "minutes_lead": minutes_per_lead(conn),
            "hours": unit(conn, "sdr_touch_hours_month"), "per_month": round(per_month), "per_sdr": round(per_month / n, 1) if n else 0.0,
            "override": float(override) if override else None, "brief": BRIEF_TOUCHES_PER_MONTH}


def set_assumptions(conn: sqlite3.Connection, minutes_touch: Optional[float] = None, minutes_followup: Optional[float] = None,
                    minutes_reply: Optional[float] = None, hours: Optional[float] = None, auto: Optional[bool] = None,
                    minutes_reply_triaged: Optional[float] = None, replies: Optional[bool] = None) -> list[str]:
    """Set the inputs given (None leaves one as it is). Raises ValueError, with nothing set, when one is out of its
    range. Returns the keys that changed (a value equal to the current one at one decimal is unchanged). Setting any
    of them drops a plain-number override, so the arithmetic rules. Turning the follow-ups automatic stamps when, so
    only leads first emailed from then on get them."""
    wanted = {"sdr_minutes_touch": minutes_touch, "sdr_minutes_followup": minutes_followup, "sdr_minutes_reply": minutes_reply,
              "sdr_minutes_reply_triaged": minutes_reply_triaged, "sdr_touch_hours_month": hours}
    for key, v in wanted.items():
        if v is None:
            continue
        lo, hi = LIMITS[key]
        if not (lo <= v <= hi):
            raise ValueError(f"{LABELS[key]} must be between {lo:g} and {hi:g}, not {v:g}")
    changed = []
    for key, v in wanted.items():
        if v is not None and round(v, 1) != round(unit(conn, key), 1):     # the page shows one decimal: 20.8 is 20.833...
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
    if changed and get_setting(conn, "team_capacity_per_month"):
        conn.execute("DELETE FROM settings WHERE key = 'team_capacity_per_month'")
        conn.commit()
    return changed
