"""Next phases: the two experiments that reach the leads routing leaves out, each with its switches on the page.

Phase 2 ranks below HubSpot's MQL gate (pool.py, feedback/loop.switch_below_gate); phase 3 emails the leads below
the capacity line (routing/tier2.py). Both have the same shape: an explore switch that buys unbiased outcomes, a
readiness bar that counts matured labels toward the number the second switch waits for, and the second switch
itself. The counts here are read live from the database, as `run.py below-gate` and `run.py tier2` print them;
the estimate of what ranking below the gate would add and the gate's loss by channel come from the stored
evaluation, as the Leadership page reads it. A switch thrown here is recorded with a name and a note, so turning
a phase on is a decision someone made, not a terminal command nobody can find later.
"""
from __future__ import annotations

import sqlite3
from typing import Optional

from ...feedback import loop as fb
from ...pool import BELOW_GATE_READY_WORKED
from ...routing import tier2
from ...capacity import touches_per_month
from ...routing.engine import DEFAULT_EXPLORATION_SHARE, DEFAULT_LINE_HORIZON_DAYS
from ...storage.db import clock, get_setting, now_iso
from . import leadership
from .leadership import _load, _pct, _ranking, _when

PHASES = ("gate", "line")
GATE_REPORT_MONTHS = 3                       # the months fb.mql_gate_report counts (its default)
GATE_SHARES = (0.0, 0.25, 0.5, 1.0)          # of the exploration slice, below the gate
LINE_SHARES = (0.0, 0.1, 0.2, 0.5, 1.0)      # of the leads below the line, in the sequence


def _share_word(x: float) -> str:
    words = {0.0: "none", 0.1: "one in ten", 0.2: "a fifth", 0.25: "a quarter", 0.5: "half", 1.0: "all"}
    return words.get(round(x, 3), f"{round(100 * x)}%")


def decision_label(phase: str, switch: str, value: str, forced: bool) -> str:
    """One line for a recorded switch: what was set, in words."""
    if switch == "explore":
        share = float(value)
        if phase == "gate":
            return ("Exploration below the gate switched off" if not share
                    else f"{_share_word(share).capitalize()} of the exploration slice sent below the gate")
        return ("The sequence switched off" if not share
                else f"{_share_word(share).capitalize()} of the leads below the line put in the sequence")
    on = value == "on"
    if phase == "gate":
        text = "Ranking below the gate switched " + ("on" if on else "off")
    else:
        text = "Email to every lead below the line switched " + ("on, but the share left alone" if on else "off")
    return text + (", ahead of the outcomes" if on and forced else "")


def _decisions(conn: sqlite3.Connection, phase: str) -> list[dict]:
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM phase_decisions WHERE phase=? ORDER BY created_at DESC, id DESC", (phase,)).fetchall()]
    for r in rows:
        r["label"] = decision_label(r["phase"], r["switch"], r["value"], bool(r["forced"]))
    return rows


def throw_switches(conn: sqlite3.Connection, phase: str, explore: Optional[float], on: Optional[bool], force: bool,
                   who: str, note: str) -> list[dict]:
    """The page's action: set what changed, record each change with who threw it, and return those records. Raises
    ValueError as the CLI does when the second switch is refused or a share is out of range; nothing is then set."""
    if phase not in PHASES:
        raise ValueError("unknown phase")
    if not who:
        raise ValueError("a switch needs the name of who threw it")
    before = _state(conn, phase)
    if phase == "gate":
        fb.switch_below_gate(conn, explore=explore, rank=on, force=force)
    else:
        tier2.switch(conn, explore=explore, band=on, force=force)
    after = _state(conn, phase)
    changed = []
    for switch in ("explore", "on"):
        if before[switch] != after[switch]:
            value = after[switch] if switch == "explore" else ("on" if after[switch] else "off")
            forced = switch == "on" and after[switch] and force and not before["ready"]
            conn.execute("INSERT INTO phase_decisions(phase, switch, value, forced, who, note, created_at) VALUES(?,?,?,?,?,?,?)",
                         (phase, switch, str(value), int(forced), who, note, now_iso()))
            changed.append({"switch": switch, "value": value, "forced": forced})
    return changed


def _state(conn: sqlite3.Connection, phase: str) -> dict:
    if phase == "gate":
        s = fb.below_gate_readiness(conn)
        return {"explore": s["explore_below_gate_share"], "on": s["ranks_below_gate"], "ready": s["ready"]}
    s = tier2.readiness(conn)
    return {"explore": s["explore_share"], "on": s["band"], "ready": s["ready"]}


def _gate_table(report: Optional[dict]) -> Optional[list[dict]]:
    rows = (report or {}).get("mql_gate")
    if not rows:
        return None
    return [{"channel": g["channel"].replace("_", " "), "leads": g["leads"], "mqls": g["mqls"], "through": _pct(g["mql_share"]),
             "no_title": _pct(g["no_title"]), "no_size": _pct(g["no_size"])} for g in rows]


def _gate_stands(g: dict, gate: dict, table: Optional[list[dict]], free: int, maturity_days: int) -> list[str]:
    """Phase 2 in plain sentences: what it is, what is on now, how far the count has got and why, what comes next."""
    out = []
    if table:
        leads = sum(t["leads"] for t in table)              # the report's gate table covers GATE_REPORT_MONTHS
        kept = sum(t["leads"] - t["mqls"] for t in table)
        out.append(f"HubSpot's points decide which leads become MQLs; a lead with no title or company size scores zero. About {round(kept / GATE_REPORT_MONTHS):,} of "
                   f"{round(leads / GATE_REPORT_MONTHS):,} leads a month stay below the gate and never reach the pool, most of them referrals and webinar sign-ups.")
    else:
        out.append("HubSpot's points decide which leads become MQLs; a lead with no title or company size scores zero and never reaches the pool.")
    if gate["on"]:
        out.append("On: the model ranks these leads with the MQLs.")
    elif g["explore_below_gate_share"]:
        out.append(f"Trying a few: {gate['share']}% of the exploration slice, the hand-outs drawn at random, comes from below the gate ({gate['slots']}% of all hand-outs), so SDRs work some of "
                   "these leads and their outcomes say whether ranking there would pay.")
    else:
        out.append("Off: nothing is drawn from below the gate.")
    count = f"{gate['matured']} of the {gate['needed']} outcomes needed are in"
    if gate["assigned"] == 0:
        why = ": none handed out yet" + (", every SDR's slot is full and the slice draws only when one frees" if free == 0 and g["explore_below_gate_share"] else "")
    elif gate["matured"] < gate["assigned"]:
        why = f": {gate['assigned']} handed out, and an outcome counts {maturity_days} days after the lead arrived"
    else:
        why = ""
    out.append(count + why + ".")
    if gate["on"]:
        pass
    elif gate["ready"]:
        out.append("Enough to judge: ranking below the gate can be switched on.")
    else:
        out.append(f"At {gate['needed']}, ranking below the gate can be switched on; switching it on earlier is allowed, and recorded as such.")
    return out


def _line_stands(t: dict, seq: dict, below: Optional[int], place: Optional[int], pool_size: Optional[int]) -> list[str]:
    """Phase 3 in plain sentences."""
    out = []
    if below is not None and pool_size:
        out.append(f"Routing hands out the top of the pool; the capacity line sits at place {place:,} of {pool_size:,} now, so {below:,} MQLs are below it and no SDR will contact them.")
    else:
        out.append("Routing hands out the top of the pool. The MQLs below the capacity line are never contacted.")
    if seq["on"]:
        out.append(f"On: every eligible lead below the line gets the email sequence from an SDR's inbox, but the {seq['holdback']}% left alone to keep the comparison honest.")
    elif t["explore_share"]:
        out.append(f"Trying a few: {seq['share']}% of the eligible leads get the email sequence from an SDR's inbox, at most {seq['daily_limit']} a day; the rest are left "
                   "alone, so the two can be compared. A reply goes to the SDR it came from.")
    else:
        out.append("Off: no automated email goes out.")
    count = f"{seq['emailed']:,} emailed and {seq['control']:,} left alone so far; {seq['emailed_matured']} of the {seq['needed']} emailed outcomes needed are in"
    if seq["emailed"] == 0 and t["explore_share"] and not seq["on"]:
        count += ": none drawn yet, the sequence draws on routing's next pass"
    out.append(count + ".")
    if seq["lift"] is not None:
        em, ct = seq["rows"][0], seq["rows"][1]
        out.append(f"Emailed leads booked {em['rate']}% against {ct['rate']}% left alone.")
    if seq["on"]:
        pass
    elif seq["ready"]:
        out.append("Enough to judge: the sequence can go to every lead below the line.")
    else:
        out.append(f"At {seq['needed']}, the sequence can go to every lead below the line; switching it on earlier is allowed, and recorded as such.")
    return out


def _shares(options: tuple, current: float) -> list[dict]:
    values = sorted(set(options) | {round(current, 3)})
    return [{"value": f"{v:g}", "label": _share_word(v), "current": abs(v - current) < 1e-9} for v in values]


def phases_page(st) -> dict:
    """gate: phase 2 live, {state ('off', 'exploring', 'ranking'), share % of the exploration slice, slots % of all
    slots, assigned, worked, matured, booked, observed %, predicted %, by_rank, needed, ready, shares (the form's
    options), decisions}; estimate: from the stored evaluation's capacity head to head or None, {mean, lo, hi,
    capacity, picked, by_channel [(channel, n)], months_word, span}; gate_table: [{channel, leads, mqls, through %,
    no_title %, no_size %}] or None, with report_path and report_written.
    seq: phase 3 live, {state ('off', 'exploring', 'on'), share %, send %, holdback %, daily_limit, sequence, rows
    [{label, leads, replied, reply_rate %, worked_by_sdr, matured, booked, rate %, predicted %}], lift (points),
    emailed_matured, needed, ready, refused, shares, decisions}. exploration: the slice's share of slots, %."""
    conn = st.conn
    report = _load(leadership.REPORT)
    g = fb.below_gate_readiness(conn)
    gate = {"state": "ranking" if g["ranks_below_gate"] else ("exploring" if g["explore_below_gate_share"] else "off"),
            "share": _pct(g["explore_below_gate_share"]), "slots": round(100 * DEFAULT_EXPLORATION_SHARE * g["explore_below_gate_share"], 1),
            "assigned": g["assigned"], "worked": g["worked"], "matured": g["matured"], "booked": g["booked"],
            "observed": _pct(g["observed_rate"]), "predicted": _pct(g["predicted_rate"]), "by_rank": g["assigned_by_rank"],
            "needed": g["needed"], "ready": g["ready"], "on": g["ranks_below_gate"],
            "shares": _shares(GATE_SHARES, g["explore_below_gate_share"]), "decisions": _decisions(conn, "gate")}
    ranking = _ranking(report) if report else None
    estimate = None
    if ranking:
        estimate = {**ranking["all_vs_mql"], "capacity": ranking["capacity"], "picked": ranking["below_gate_picked"],
                    "by_channel": ranking["below_gate_by_channel"], "months_word": ranking["months_word"], "span": ranking["span"]}
    t = tier2.readiness(conn)
    rows = []
    for label, arm in (("Emailed by the sequence", t["arms"]["emailed"]), ("Left alone", t["arms"]["control"])):
        rows.append({"label": label, **arm, "rate": _pct(arm["observed_rate"]), "predicted": _pct(arm["predicted_rate"]),
                     "reply_rate": _pct(arm["replied"] / arm["leads"]) if arm["leads"] else None})
    seq = {"state": "on" if t["band"] else ("exploring" if t["explore_share"] else "off"), "share": _pct(t["explore_share"]),
            "send": _pct(t["send_share"]), "holdback": _pct(t["holdback"]), "daily_limit": t["daily_limit"], "sequence": t["sequence"],
            "rows": rows, "lift": round(100 * t["lift"], 1) if t["lift"] is not None else None,
            "emailed": t["arms"]["emailed"]["leads"], "control": t["arms"]["control"]["leads"],
            "emailed_matured": t["arms"]["emailed"]["matured"], "needed": t["needed"], "ready": t["ready"], "refused": t["refused"],
            "on": t["band"], "shares": _shares(LINE_SHARES, t["explore_share"]), "decisions": _decisions(conn, "line")}
    # the pool as routing sees it: how many sit below the line, and whether any slot is free
    router = st.router()
    now = clock(conn)
    pool = router.pool(now)
    counts, sdrs, free = router.free_slots()
    days = float(get_setting(conn, "capacity_line_days") or DEFAULT_LINE_HORIZON_DAYS)
    k = max(1, int(round(touches_per_month(conn, len(sdrs)) * days / 30.4)))
    place = min(k, len(pool)) if pool else None
    table = _gate_table(report)
    gate["stands"] = _gate_stands(g, gate, table, free, leadership.DEMO_MATURITY_DAYS)
    seq["stands"] = _line_stands(t, seq, len(pool) - place if pool else None, place, len(pool))
    return {"gate": gate, "estimate": estimate, "gate_table": table, "seq": seq,
            "report_path": str(leadership.REPORT), "report_written": _when((report or {}).get("generated_at")),
            "exploration": round(100 * DEFAULT_EXPLORATION_SHARE), "gate_needed": BELOW_GATE_READY_WORKED,
            "maturity_days": leadership.DEMO_MATURITY_DAYS}
