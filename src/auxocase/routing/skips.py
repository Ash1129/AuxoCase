"""Likely skips, learned from the SDRs' own skips, held before they cost an SDR's minutes.

Some leads an SDR opens and skips: wrong fit, a student, no way to reach them. Each one costs the minutes of
reading it and deciding. The SDRs' skip and junk decisions (the overrides log) say which kind: this module counts,
for every trait a lead has (seniority, company size, channel, country, a student title, no notes), how often leads
with that trait were skipped against worked, and calls a lead a likely skip when one of its traits was skipped
often enough, by enough decisions, to be more than chance. With `skips_auto` on, routing holds such a lead in the
manager's queue with the reason, where it can still be released; the SDR never spends the minutes.

It is a count, not a model: a trait needs `MIN_DECISIONS` decisions to count, the rate is smoothed, the
threshold is a setting, and a trait only counts when its rate is clearly above the SDRs' overall skip rate (a trait
every lead has says nothing). Replaying it over the same decisions says what it would have caught and what it would have
held wrongly; that is what the Leadership page shows and what the capacity arithmetic discounts the skip minutes
by. The SDRs keep skipping what they skip, so the counts keep learning.
"""
from __future__ import annotations

import sqlite3
from typing import Optional

from ..hubspot.client import HubSpotClient
from ..hubspot.models import Company, Contact
from ..scoring.features import country_group, is_student_or_intern, size_band
from ..storage.db import dumps, get_setting, loads, set_setting

SKIP_ACTIONS = ("skipped", "junk")
DECIDED = ("worked", "skipped", "junk")
MIN_DECISIONS = 20                    # a trait is judged on at least this many decisions: nine on "outside the US" held hundreds
HOLD_PREFIX = "likely skipped"        # how a hold for this reads in held_queue.reason, which routing tells its own holds by
PRIOR = 1.0                           # smoothing: one skip and one worked lead assumed per trait
DEFAULT_THRESHOLD = 0.5
MARGIN = 0.15                         # a trait's skip rate must beat the overall skip rate by this much to mean anything
MODEL = "skip_model"                  # the setting the counts and the replay are kept in, written by routing
WORDS = {"seniority": {"individual": "an individual contributor", "manager": "a manager", "director": "a director", "vp": "a VP",
                       "c_level": "C-level", "owner": "an owner", "unknown": "no title"},
         "size": {"unknown": "company size unknown", "under_50": "under 50 employees", "50_149": "50 to 149 employees",
                  "150_299": "150 to 299 employees", "300_500": "300 to 500 employees", "over_500": "over 500 employees"},
         "channel": {}, "country": {"us": "in the US", "anglophone": "outside the US", "other": "outside the US", "unknown": "country unknown"},
         "title": {"student": "a student or intern"}, "notes": {"none": "no notes"}}


def traits(c: Contact, co: Optional[Company]) -> list[str]:
    out = [f"seniority:{c.seniority or 'unknown'}", f"size:{size_band(co.num_employees if co else None)}", f"channel:{c.channel}",
           f"country:{country_group(c.country or (co.country if co else ''))}"]
    if is_student_or_intern(c.job_title):
        out.append("title:student")
    if not (c.notes or "").strip():
        out.append("notes:none")
    return out


def word(trait: str) -> str:
    kind, value = trait.split(":", 1)
    return WORDS.get(kind, {}).get(value) or (f"from {value.replace('_', ' ')}" if kind == "channel" else value.replace("_", " "))


def threshold(conn: sqlite3.Connection) -> float:
    return float(get_setting(conn, "skip_threshold") or DEFAULT_THRESHOLD)


def _decisions(conn: sqlite3.Connection) -> dict[str, bool]:
    """Each lead's last decision: True for a skip, False for worked."""
    out: dict[str, bool] = {}
    for r in conn.execute("SELECT contact_id, action FROM overrides WHERE action IN ('worked','skipped','junk') ORDER BY id"):
        out[r["contact_id"]] = r["action"] in SKIP_ACTIONS
    return out


def counts(hs: HubSpotClient, conn: sqlite3.Connection) -> tuple[dict[str, list[int]], dict[str, bool]]:
    """Per trait [skipped, decided], and the decisions they came from."""
    decided = _decisions(conn)
    tally: dict[str, list[int]] = {}
    for cid, skipped in decided.items():
        c = hs.get_contact(cid)
        if c is None:
            continue
        co = hs.get_company(c.company_id) if c.company_id else None
        for t in traits(c, co):
            n = tally.setdefault(t, [0, 0])
            n[0] += int(skipped)
            n[1] += 1
    return tally, decided


def rate(n: list[int]) -> float:
    return (n[0] + PRIOR) / (n[1] + 2 * PRIOR)


def base_rate(decided: dict[str, bool]) -> float:
    return rate([sum(decided.values()), len(decided)])


def likelihood(tally: dict[str, list[int]], c: Contact, co: Optional[Company], base: float = 0.0) -> Optional[dict]:
    """The lead's riskiest trait with enough decisions behind it and a skip rate clearly above the overall one
    (`base`): {trait, rate, skipped, decided}, or None."""
    best = None
    for t in traits(c, co):
        n = tally.get(t)
        if not n or n[1] < MIN_DECISIONS or rate(n) < base + MARGIN:
            continue
        if best is None or rate(n) > best["rate"]:
            best = {"trait": t, "rate": round(rate(n), 3), "skipped": n[0], "decided": n[1]}
    return best


def reason(found: dict) -> str:
    return f"{HOLD_PREFIX}: {word(found['trait'])}, {found['skipped']} of {found['decided']} skipped"


def replay(hs: HubSpotClient, conn: sqlite3.Connection, tally: dict[str, list[int]], decided: dict[str, bool], cut: float) -> dict:
    """What the counts would have done to the decisions they were learned from: {skips, worked, caught, held_worked}."""
    out = {"skips": 0, "worked": 0, "caught": 0, "held_worked": 0}
    base = base_rate(decided)
    for cid, skipped in decided.items():
        c = hs.get_contact(cid)
        if c is None:
            continue
        co = hs.get_company(c.company_id) if c.company_id else None
        found = likelihood(tally, c, co, base)
        flagged = bool(found and found["rate"] >= cut)
        out["skips" if skipped else "worked"] += 1
        if flagged:
            out["caught" if skipped else "held_worked"] += 1
    return out


def refresh(hs: HubSpotClient, conn: sqlite3.Connection) -> dict:
    """Count, replay, keep the result in the `skip_model` setting (read by the pages and the capacity arithmetic), and
    return it: {threshold, base (the overall skip rate), traits: {trait: [skipped, decided]}, replay, skip_share,
    caught_share}."""
    tally, decided = counts(hs, conn)
    cut = threshold(conn)
    rep = replay(hs, conn, tally, decided, cut)
    worked = rep["worked"]
    model = {"threshold": cut, "base": round(base_rate(decided), 3), "traits": tally, "replay": rep,
             "skip_share": round(rep["skips"] / worked, 3) if worked else None,
             "caught_share": round(rep["caught"] / rep["skips"], 3) if rep["skips"] else None}
    set_setting(conn, MODEL, dumps(model))
    return model


def stored(conn: sqlite3.Connection) -> Optional[dict]:
    return loads(get_setting(conn, MODEL))


def likely(model: dict, c: Contact, co: Optional[Company]) -> Optional[str]:
    """The reason to hold this lead under these counts (a `refresh` result), or None."""
    found = likelihood(model["traits"], c, co, model.get("base") or 0.0)
    return reason(found) if found and found["rate"] >= model["threshold"] else None
