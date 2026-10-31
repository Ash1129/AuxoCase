"""The team's capacity, in first touches a month, from SDR minutes.

The brief fixes the team at 12 SDRs working about 500 MQLs a month. That 500 is SDR time: each SDR spends so many
hours a month on first touches, and a cold touch takes so many minutes. Holding the hours and dividing by the
minutes gives the capacity, so anything that makes a touch quicker (a draft ready to send, a call brief, the
logging done) shows up here as more leads worked by the same team. Routing's capacity line, the Overview, the
Leadership page, the simulator and the cost report all read this one number.

The three inputs are settings with defaults, not facts: `sdr_minutes_touch` (20, a cold first touch: read the
reason and the notes, write or call), `sdr_minutes_reply` (10, answering a lead who replied to the sequence) and
`sdr_touch_hours_month` (the hours each SDR gives first touches a month, set so the defaults reproduce the brief's
500). `team_capacity_per_month`, when set, overrides the arithmetic with a plain number.
"""
from __future__ import annotations

import sqlite3
from typing import Optional

from .storage.db import get_setting, set_setting

DEFAULT_SDRS = 12                                   # the brief's team
BRIEF_TOUCHES_PER_MONTH = 500                       # the brief: about 500 MQLs worked a month
DEFAULTS = {
    "sdr_minutes_touch": 20.0,
    "sdr_minutes_reply": 10.0,
    "sdr_touch_hours_month": BRIEF_TOUCHES_PER_MONTH * 20.0 / 60 / DEFAULT_SDRS,   # 13.9 hours: the brief's 500 at 20 minutes
}
LIMITS = {"sdr_minutes_touch": (1.0, 240.0), "sdr_minutes_reply": (1.0, 240.0), "sdr_touch_hours_month": (0.5, 200.0)}
LABELS = {"sdr_minutes_touch": "minutes a cold touch", "sdr_minutes_reply": "minutes a reply",
          "sdr_touch_hours_month": "hours an SDR gives first touches a month"}


def unit(conn: sqlite3.Connection, key: str) -> float:
    v = get_setting(conn, key)
    return float(v) if v else DEFAULTS[key]


def touches_per_month(conn: sqlite3.Connection, sdrs: Optional[int] = None) -> float:
    """First touches a month the team can make: SDRs × hours × 60 ÷ minutes a touch, or the override."""
    override = get_setting(conn, "team_capacity_per_month")
    if override:
        return float(override)
    n = DEFAULT_SDRS if sdrs is None else sdrs
    return n * unit(conn, "sdr_touch_hours_month") * 60 / unit(conn, "sdr_minutes_touch")


def breakdown(conn: sqlite3.Connection, sdrs: Optional[int] = None) -> dict:
    """What the capacity is made of, for a page: {sdrs, minutes_touch, minutes_reply, hours, per_month, per_sdr,
    override (the plain number when one is set, else None), brief (the brief's 500)}."""
    n = DEFAULT_SDRS if sdrs is None else sdrs
    per_month = touches_per_month(conn, n)
    override = get_setting(conn, "team_capacity_per_month")
    return {"sdrs": n, "minutes_touch": unit(conn, "sdr_minutes_touch"), "minutes_reply": unit(conn, "sdr_minutes_reply"),
            "hours": unit(conn, "sdr_touch_hours_month"), "per_month": round(per_month), "per_sdr": round(per_month / n, 1) if n else 0.0,
            "override": float(override) if override else None, "brief": BRIEF_TOUCHES_PER_MONTH}


def set_assumptions(conn: sqlite3.Connection, minutes_touch: Optional[float] = None, minutes_reply: Optional[float] = None,
                    hours: Optional[float] = None) -> list[str]:
    """Set the inputs given (None leaves one as it is). Raises ValueError, with nothing set, when one is out of its
    range. Returns the keys that changed (a value equal to the current one at one decimal is unchanged). Setting any of
    them drops a plain-number override, so the arithmetic rules."""
    wanted = {"sdr_minutes_touch": minutes_touch, "sdr_minutes_reply": minutes_reply, "sdr_touch_hours_month": hours}
    for key, v in wanted.items():
        if v is None:
            continue
        lo, hi = LIMITS[key]
        if not (lo <= v <= hi):
            raise ValueError(f"{LABELS[key]} must be between {lo:g} and {hi:g}, not {v:g}")
    changed = []
    for key, v in wanted.items():
        if v is not None and round(v, 1) != round(unit(conn, key), 1):     # the page shows one decimal: 13.9 is 13.888...
            set_setting(conn, key, f"{v:g}")
            changed.append(key)
    if changed and get_setting(conn, "team_capacity_per_month"):
        conn.execute("DELETE FROM settings WHERE key = 'team_capacity_per_month'")
        conn.commit()
    return changed
