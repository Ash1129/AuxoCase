"""Flagged leads: marketing's second look (flags.py).

The open flags, each with the record's own words and the lead's fields beside it, to resolve one by one; the counts
by kind and by channel, which are the form and campaign conversation; and, once leads have matured, whether flagged
leads booked differently from unflagged ones at the same odds, which is the only test of whether the flags are worth
anything.
"""
from __future__ import annotations

from ... import flags
from ...llm.flags import FLAG_KINDS
from ..scheduler import jobs_from_env


def flagged_page(st) -> dict:
    s = flags.summary(st.conn)
    every = next((j.every_min for j in jobs_from_env() if j.name == "flags"), 10)
    return {
        "summary": s,
        "open": flags.open_flags(st.hs, st.conn),
        "resolved": flags.resolved_flags(st.conn),
        "comparison": flags.comparison(st.conn),
        "kinds": [{"kind": k, "label": v[0], "meaning": v[1]} for k, v in FLAG_KINDS.items()],
        "resolutions": flags.RESOLUTIONS,
        "every": every,
        "reader": st.llm.name,
    }
