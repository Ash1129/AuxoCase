"""Leadership: the shared number, demos per 1,000 leads, and where it came from.

On the mock every figure is simulated, so the page reads the stored evaluation (docs/evaluation.json, written by
`run.py eval` beside docs/evaluation.md) and the queue-limit experiment (outputs/queue_limits.json, written by
`run.py queue-limits`) rather than recomputing them per view, and says which file and when. The arithmetic the
page shows (per 1,000, averages a month, the cost of the exploration slice) is done here, not in the template.
Per 1,000 divides by the new leads the demo database recorded each month (contacts by month of arrival), and by
the brief's figure a month where it recorded none (a real portal, whose contacts are not in the database).
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ...data.generate import LATE_TOUCH_FACTOR
from ...hubspot.mock import MockHubSpot
from ...capacity import FOLLOWUPS, breakdown, touches_per_month
from ...routing import skips as skip_counts
from ...routing.engine import DEFAULT_LINE_HORIZON_DAYS
from ...scoring.model import DEMO_MATURITY_DAYS
from ...storage.db import clock, get_setting

REPORT = Path("docs/evaluation.json")
QUEUE_LIMITS = Path("outputs/queue_limits.json")
LEADS_PER_MONTH = 6000      # the brief: "about 6,000 new leads a month" (docs/blueprint/brief.txt), per 1,000 without a recorded count
WHOLE_MONTH = 0.6           # as evaluation.write_report: fewer first touches than this share of the median month is a partial month
BAR_W = 280                 # the gain charts: a bar's full width, in the SVG's units
COL_X0, COL_STEP, COL_H = 48, 36, 120   # the leads-a-month columns: where the first stands, the step between them, the plot's height
WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten",
         11: "eleven", 12: "twelve"}


def _load(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _written(path: Path) -> Optional[str]:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).strftime("%d %b %Y %H:%M UTC")
    except OSError:
        return None


def _when(iso: Optional[str]) -> Optional[str]:
    try:
        return datetime.fromisoformat(iso).strftime("%d %b %Y %H:%M UTC")
    except (TypeError, ValueError):
        return None


def word(n: int) -> str:
    return WORDS.get(n, f"{n:,}")


def month_label(m: str) -> str:
    try:
        return datetime.strptime(m, "%Y-%m").strftime("%b %Y")
    except (TypeError, ValueError):
        return str(m)


def span(first: str, last: str) -> str:
    """'2028-05', '2028-10' -> 'May to Oct 2028'; across years both get theirs."""
    a, b = month_label(first), month_label(last)
    return f"{a[:3]} to {b}" if a[-4:] == b[-4:] and a != b else (a if a == b else f"{a} to {b}")


def _pct(x) -> Optional[int]:
    return None if x is None else round(100 * x)


def _hours(x) -> Optional[int]:
    return None if x is None else round(x)


def _leads(n: int, what: str = "lead") -> str:
    return f"{word(n)} {what}{'' if n == 1 else 's'}"


def _share(f: float) -> str:
    """A late-touch factor as the share of its value a lead keeps: 0.5 -> 'half'."""
    return "half" if abs(f - 0.5) < 1e-9 else f"{f:g} of its value"


def _as_often(f: float) -> tuple[str, str]:
    """A late-touch factor both ways: (how much more often a fresh lead books, how often a late one does)."""
    if not f:
        return "[NOT RECORDED]", "[NOT RECORDED]"
    up = "twice" if abs(1 / f - 2) < 0.05 else f"{1 / f:.1f} times"
    return up + " as often", ("half" if abs(f - 0.5) < 1e-9 else f"{f:g} times") + " as often"


def _and(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _new_leads(st, months: list[str]) -> Optional[dict]:
    """New leads by month of arrival, as the demo database recorded them (the mock's contacts by createdate);
    None on a real portal, or when any of `months` has none recorded, and per 1,000 then uses the brief's figure."""
    if not months or not isinstance(st.hs, MockHubSpot):
        return None
    y, m = (int(x) for x in max(months).split("-"))
    end = f"{y + m // 12}-{m % 12 + 1:02d}"
    counts = {r[0]: r[1] for r in st.conn.execute(
        "SELECT substr(createdate, 1, 7) m, COUNT(*) n FROM hs_contacts WHERE createdate >= ? AND createdate < ? GROUP BY m",
        (min(months), end))}
    return {m: counts.get(m) or LEADS_PER_MONTH for m in months} if any(counts.get(m) for m in months) else None


def _counts_text(before: list[str], after: list[str], counts: dict) -> str:
    """'6,000 new leads in each history month, then 6,200, 6,000 and 5,800 in Aug, Sep and Oct 2028'."""
    b = [counts[m] for m in before]
    text = f"{b[0]:,} new leads in each history month" if len(set(b)) == 1 else f"{min(b):,} to {max(b):,} new leads a history month"
    a = [counts[m] for m in after]
    if len(set(a)) == 1:
        return text + f", then {a[0]:,} in each routed month"
    labels = [month_label(m) for m in after]
    if len({lb[-4:] for lb in labels}) == 1:
        labels = [lb[:3] for lb in labels[:-1]] + [labels[-1]]
    return text + f", then {_and([f'{n:,}' for n in a])} in {_and(labels)}"


def _side(ms: list[dict], counts: Optional[dict]) -> dict:
    demos, worked = sum(m["demos"] for m in ms), sum(m["worked"] for m in ms)
    leads = sum(counts[m["month"]] for m in ms) if counts else LEADS_PER_MONTH * len(ms)
    return {"months": len(ms), "months_word": word(len(ms)), "first": month_label(ms[0]["month"]), "last": month_label(ms[-1]["month"]),
            "demos": demos, "leads": leads, "per_1000_raw": 1000 * demos / leads, "per_1000": round(1000 * demos / leads, 1),
            "demos_a_month_raw": demos / len(ms), "demos_a_month": round(demos / len(ms)), "worked_a_month": round(worked / len(ms))}


def _months(report: dict, now: Optional[datetime] = None) -> dict:
    """Whole months (the rule evaluation.write_report uses), split into before and after routing. Routed months after
    the service clock's month are the simulator run ahead with the AI steps on (the SDRs' time saved, the model's
    picks): when there are any they are the After (`projected`), and the routed months up to the clock are `routed`,
    the middle group: ranking and first-day touches with the SDRs' time as before."""
    rows = report.get("success_by_month") or []
    full = sorted(m["worked"] for m in rows)
    whole = [m for m in rows if full and m["worked"] >= WHOLE_MONTH * full[len(full) // 2]]
    cut = now.strftime("%Y-%m") if now else None

    def row(m: dict) -> dict:
        return {"month": month_label(m["month"]), "worked": m["worked"], "demos": m["demos"],
                "rate": _pct(m["demos"] / m["worked"]) if m["worked"] else None, "median_hours": _hours(m.get("median_hours")),
                "within_24h": _pct(m.get("within_24h"))}
    before = [m for m in whole if m["routed_share"] < 0.5]
    ahead = [m for m in whole if m["routed_share"] >= 0.5 and cut and m["month"] > cut]
    mid = [m for m in whole if m["routed_share"] >= 0.5 and m not in ahead] if ahead else []
    after = ahead or [m for m in whole if m["routed_share"] >= 0.5]
    routed = sum(1 for m in rows if m["routed_share"] >= 0.5)
    return {"before_rows": [row(m) for m in before], "routed_rows": [row(m) for m in mid], "after_rows": [row(m) for m in after],
            "before": before, "routed": mid, "after": after, "projected": bool(ahead),
            "ahead": [month_label(m["month"]) for m in ahead], "routed_span": span(mid[0]["month"], mid[-1]["month"]) if mid else None,
            "left_out": [month_label(m["month"]) for m in rows if m not in whole],
            "routed_months": routed, "routed_word": word(routed),
            "span": span(whole[0]["month"], whole[-1]["month"]) if whole else None}


def _shared(st, report: dict, mo: dict) -> Optional[dict]:
    if not (mo["before"] and mo["after"]):
        return None
    before, after = [m["month"] for m in mo["before"]], [m["month"] for m in mo["after"]]
    counts = _new_leads(st, before + [m["month"] for m in mo["routed"]] + after)
    b, a = _side(mo["before"], counts), _side(mo["after"], counts)
    r = _side(mo["routed"], counts) if mo["routed"] else None
    sp = report.get("speed_to_lead") or {}
    base = sp.get("baseline") or {}
    return {"before": b, "after": a, "routed": r, "change": round(100 * (a["per_1000_raw"] / b["per_1000_raw"] - 1)) if b["per_1000_raw"] else None,
            "leads_recorded": bool(counts), "leads_text": _counts_text(before, after, counts) if counts else None,
            "more_a_month": a["demos_a_month"] - b["demos_a_month"],
            "same_capacity": abs(a["worked_a_month"] / b["worked_a_month"] - 1) < 0.1 if b["worked_a_month"] else False,
            "all_beat": min(m["demos"] for m in mo["after"]) > max(m["demos"] for m in mo["before"]),
            "hours_before": _hours(base.get("median_hours")), "hours_after": _hours(sp.get("median_hours")),
            "touched_before": base.get("n"), "touched_after": sp.get("n"),
            "within_before": _pct(base.get("within_24h")), "within_after": _pct(sp.get("within_24h")),
            "late_share": _pct(1 - sp["within_24h"]) if sp.get("within_24h") is not None else None}


def _setup_label(s: dict, prev: Optional[dict]) -> str:
    """Each routing setup said as the change from the one before it, as the experiment adds one change at a time."""
    if prev is None:
        return f"Ranking, {_leads(s['limit'], 'waiting lead')} a desk" + (", freshness discount on" if s["freshness"] == "on" else "")
    if s["freshness"] == "on" and prev["freshness"] != "on" and s["limit"] == prev["limit"]:
        return "Add the freshness discount"
    if s["freshness"] == prev["freshness"] and s["limit"] < prev["limit"]:
        return "Cut to one, refilled when touched" if s["limit"] == 1 else f"Cut queues to {_leads(s['limit'], 'waiting lead')}"
    return f"Freshness discount {s['freshness']}, {_leads(s['limit'], 'waiting lead')} a desk"


def _setups(ql: dict, history: Optional[float], in_use: tuple) -> Optional[dict]:
    summary = ql.get("summary") or []
    if not summary:
        return None
    top = max([s["demos_max"] for s in summary] + ([history] if history else []))
    scale = max(100, math.ceil(top / 100) * 100)                # bars run 0 to the next hundred
    rows, prev = [], None
    for s in summary:
        rows.append({"label": _setup_label(s, prev), "demos": round(s["demos_mean"]), "demos_lo": s["demos_min"], "demos_hi": s["demos_max"],
                     "within": _pct(s["within_24h_mean"]), "within_lo": _pct(s["within_24h_min"]), "within_hi": _pct(s["within_24h_max"]),
                     "bar": round(200 * s["demos_mean"] / scale, 1),
                     "in_use": bool(in_use) and abs(s["stale_lead_factor"] - in_use[0]) < 1e-9 and s["limit"] == in_use[1]})
        prev = s
    fresh = [s["stale_lead_factor"] for s in summary if s["freshness"] == "on"]
    first, last = summary[0], summary[-1]
    seeds = len(first.get("seeds") or [])
    return {"rows": rows, "scale": scale, "line": round(200 * history / scale, 1) if history else None, "seeds": seeds,
            "seeds_word": word(seeds), "fresh_words": _share(fresh[0]) if fresh else None,
            "first_desk": f"{_leads(first['limit'])} waiting at every desk", "first_off": first["freshness"] == "off",
            "first_demos": round(first["demos_mean"]), "speed_adds": last["demos_mean"] > first["demos_mean"]}


CHANNEL_WORDS = {"paid_ads": "paid ads", "website": "website", "webinar": "webinar", "referral": "referral", "events": "events", "email": "email"}
PIE_R, PIE_R0, PIE_C = 56.0, 34.0, 70.0            # the donut: outer and inner radius, centre, in a 140 x 140 box


def _arc(a0: float, a1: float) -> str:
    """An SVG path for one donut slice from angle a0 to a1 (degrees, clockwise from the top)."""
    def pt(r: float, a: float) -> str:
        rad = math.radians(a - 90)
        return f"{PIE_C + r * math.cos(rad):.1f} {PIE_C + r * math.sin(rad):.1f}"
    big = 1 if a1 - a0 > 180 else 0
    return (f"M {pt(PIE_R, a0)} A {PIE_R} {PIE_R} 0 {big} 1 {pt(PIE_R, a1)} L {pt(PIE_R0, a1)} "
            f"A {PIE_R0} {PIE_R0} 0 {big} 0 {pt(PIE_R0, a0)} Z")


def _channels(st, months: list[str]) -> Optional[dict]:
    """The leads worked in `months` by the channel they came in on, largest first, as donut slices:
    {total, slices: [{channel, label, n, share %, path, i}]}; None when the database has none of them."""
    if not months:
        return None
    rows = st.conn.execute(
        "SELECT COALESCE(NULLIF(c.channel, ''), 'unknown') ch, COUNT(*) n FROM outcomes o JOIN hs_contacts c ON c.id = o.contact_id "
        f"WHERE o.worked = 1 AND substr(o.first_touch_at, 1, 7) IN ({','.join('?' * len(months))}) GROUP BY ch ORDER BY n DESC, ch", months).fetchall()
    total = sum(r["n"] for r in rows)
    if not total:
        return None
    slices, a = [], 0.0
    for i, r in enumerate(rows):
        sweep = 360.0 * r["n"] / total
        slices.append({"channel": r["ch"], "label": CHANNEL_WORDS.get(r["ch"], r["ch"].replace("_", " ")), "n": r["n"],
                       "share": round(100 * r["n"] / total), "path": _arc(a, min(a + sweep, 360.0) - (0.0 if len(rows) == 1 else 0.01)), "i": i + 1})
        a += sweep
    return {"total": total, "slices": slices}


def _gain(setups: Optional[dict], shared: Optional[dict], mo: Optional[dict], st) -> Optional[dict]:
    """The gain, metric by metric, as charts: `steps` (history, each routing setup, and the months simulated ahead
    with the AI steps on) with demos a month and the share touched within 24 h, each with its bar width out of
    BAR_W; `leads` (leads worked a month, month by month, with the group each is in and its column); `channels`
    (the After's worked leads by channel, a donut). None when the report has no before and after."""
    if not shared or not mo:
        return None
    b, a = shared["before"], shared["after"]
    steps = [{"label": "History", "demos": b["demos_a_month"], "within": shared["within_before"], "kind": "history", "range": None}]
    if setups:
        steps += [{"label": r["label"], "demos": r["demos"], "within": r["within"], "kind": "setup", "in_use": r["in_use"],
                   "range": f"{r['demos_lo']}–{r['demos_hi']} across seeds, touched within 24 h {r['within_lo']}–{r['within_hi']}%"} for r in setups["rows"]]
    if mo["projected"]:
        steps.append({"label": "Add the time the AI steps save", "demos": a["demos_a_month"], "within": shared["within_after"], "kind": "after", "range": None})
    top = max(s["demos"] for s in steps) or 1
    for s in steps:
        s["bar"] = round(BAR_W * s["demos"] / top, 1)
        s["bar_within"] = round(BAR_W * (s["within"] or 0) / 100, 1)
    rows = [{**r, "group": "before"} for r in mo["before_rows"]] + [{**r, "group": "after"} for r in mo["after_rows"]]
    peak = max((r["worked"] for r in rows), default=0) or 1
    tick = 500 if peak <= 1500 else 1000
    axis_top = math.ceil(peak / tick) * tick
    for i, r in enumerate(rows):
        r["x"] = COL_X0 + i * COL_STEP
        r["h"] = round(COL_H * r["worked"] / axis_top, 1)
        r["show"] = i == 0 or i == len(rows) - 1 or r["month"][:3] == "Jan" or (i > 0 and rows[i - 1]["group"] != r["group"])
    ticks = [{"v": v, "y": round(COL_H - COL_H * v / axis_top, 1), "text": f"{v / 1000:g}K" if v >= 1000 else f"{v:,}"}
             for v in range(0, axis_top + 1, tick)]
    return {"steps": steps, "projected": mo["projected"], "leads": {"rows": rows, "ticks": ticks, "width": COL_X0 + len(rows) * COL_STEP,
            "before_avg": b["worked_a_month"], "after_avg": a["worked_a_month"]},
            "channels": _channels(st, [m["month"] for m in mo["after"]]), "after_span": span(mo["after"][0]["month"], mo["after"][-1]["month"])}


def _ranking(report: dict) -> Optional[dict]:
    t = report.get("capacity_head_to_head")
    if not t:
        return None
    mn = t["mean"]
    scale = max(mn.values()) or 1

    def row(label: str, arm: str, kind: str, live: bool = False) -> dict:
        return {"label": label, "v": round(mn[arm]), "bar": round(199 * mn[arm] / scale, 1), "kind": kind, "live": live}

    bg = report.get("below_gate") or {}
    return {"capacity": t["capacity"], "months": len(t["months"]), "months_word": word(len(t["months"])),
            "span": span(t["months"][0], t["months"][-1]), "scale": round(scale), "version": report.get("live"),
            "model": round(mn["model"]), "hubspot": round(mn["hubspot"]), "points": round(mn["points"]),
            "rows": [row("Random MQLs", "random", "out"), row("HubSpot's current points", "hubspot", "out"),
                     row("AuxoCase cold-start points table", "points", "solid"),
                     row(f"AuxoCase model, {report.get('live') or '[NOT RECORDED]'}", "model", "solid", live=True),
                     row("Best possible among the MQLs", "best_mql", "out")],
            "phase2": [row("AuxoCase model, ranking every lead", "model_all", "dash-accent"),
                       row("Best possible among every lead", "best_all", "dash")],
            "phase2_on": bool(bg.get("ranks_below_gate")), "phase2_ready": bool(bg.get("ready")),
            "vs_hubspot": {k: round(v) for k, v in t["model_vs_hubspot"].items()},
            "vs_points": round(t["model_vs_points"]["mean"]),
            "all_vs_mql": {k: round(v) for k, v in t["all_vs_mql"].items()},
            "below_gate_picked": round(t.get("below_gate_picked") or 0),
            "below_gate_by_channel": [(c.replace("_", " "), n) for c, n in (t.get("below_gate_by_channel") or {}).items()],
            "ece": (report.get("metrics") or {}).get("ece")}


def _explore(report: dict, routed_months: int) -> Optional[dict]:
    """The ranked slice against the random one, and what the random one cost at the ranked rate."""
    sl = report.get("slices") or {}
    ranked, held = sl.get("assigned"), sl.get("holdout_assigned")
    if not (ranked and held and ranked["worked"]):
        return None
    expected = held["worked"] * ranked["booked"] / ranked["worked"]
    cost = max(0.0, expected - held["booked"])
    rows = [{"label": "Ranked by AuxoCase", **ranked}, {"label": "Picked at random", **held}]
    for key, label in (("assigned (below HubSpot's gate)", "Ranked, below HubSpot's gate"),
                       ("holdout_assigned (below HubSpot's gate)", "Picked at random below HubSpot's gate")):
        if sl.get(key):
            rows.append({"label": label, **sl[key]})
    for r in rows:
        r["rate"] = _pct(r["booked"] / r["worked"]) if r["worked"] else None
    slots = sum(r["assigned"] for r in rows)
    random_slots = sum(r["assigned"] for r in rows if r["label"].startswith("Picked at random"))
    return {"rows": rows, "share": _pct(random_slots / slots) if slots else None, "random_worked": held["worked"],
            "random_booked": held["booked"], "expected": round(expected), "cost": round(cost), "months": routed_months,
            "months_word": word(routed_months), "cost_a_month": round(cost / routed_months) if routed_months else None}


def _precision(report: dict) -> Optional[dict]:
    h = report.get("head_to_head")
    if not h:
        return None
    model, points = h["model_precision_at_k"], h["points_table_precision_at_k"]
    return {"k": h["k"], "model": model, "points": points, "points_ahead": points > model, "hair": abs(points - model) < 0.01,
            "level": f"{points:.3f}" == f"{model:.3f}"}


def _speed_effect(report: dict) -> Optional[dict]:
    """The outcomes' own late-touch factor, with the two booking rates as whole percents."""
    se = report.get("speed_effect") or {}
    if se.get("factor") is None:
        return None
    return {"factor": se["factor"], "fast": _pct(se.get("rate_fast")), "slow": _pct(se.get("rate_slow")),
            "n_fast": se.get("n_fast"), "n_slow": se.get("n_slow")}


def _tier2(report: dict) -> Optional[dict]:
    """Phase 3's experiment as the page shows it: the two arms side by side, with rates as percentages."""
    t = report.get("tier2")
    if not t:
        return None
    em, co = t["arms"]["emailed"], t["arms"]["control"]
    if not (em["leads"] or co["leads"] or t["explore_share"] or t["band"]):
        return None
    rows = []
    for label, a in (("Emailed by the sequence", em), ("Left to the ordinary process", co)):
        rows.append({"label": label, **a, "rate": _pct(a["observed_rate"]) if a["observed_rate"] is not None else None,
                     "predicted": _pct(a["predicted_rate"]) if a["predicted_rate"] is not None else None,
                     "reply_rate": _pct(a["replied"] / a["leads"]) if a["leads"] else None})
    return {"rows": rows, "on": bool(t["band"]), "state": "on" if t["band"] else ("exploring" if t["explore_share"] else "off"),
            "explore": _pct(t["explore_share"]), "send": _pct(t["send_share"]),
            "holdback": _pct(t["holdback"]), "daily_limit": t["daily_limit"], "sequence": t.get("sequence"),
            "lift": round(100 * t["lift"], 1) if t.get("lift") is not None else None, "ready": bool(t["ready"]),
            "needed": t["needed"], "matured": em["matured"], "refused": t["refused"]}


def _costs(report: dict) -> Optional[dict]:
    """The cost table: a demo's cost by the way the lead was reached, and the service's own AI spend."""
    c = report.get("costs")
    if not c or not (c["sdr"]["worked"] or c["automated"]["emailed"]):
        return None
    sd, au, ct, u, ai = c["sdr"], c["automated"], c["control"], c["units"], c.get("ai") or {}
    rows = [{"label": "An SDR's touch on a ranked lead", "reached": sd["worked"], "demos": sd["demos"], "rate": _pct(sd["demo_rate"]),
             "hours": sd["sdr_hours"], "cost": sd["cost"], "per_demo": sd["cost_per_demo"], "per_hour": sd["demos_per_sdr_hour"], "value": sd["value"]}]
    if au["emailed"]:
        rows.append({"label": "The automated sequence", "reached": au["emailed"], "demos": au["demos"], "rate": _pct(au["demo_rate"]),
                     "hours": au["sdr_hours"], "cost": au["cost"], "per_demo": au["cost_per_demo"], "per_hour": au["demos_per_sdr_hour"], "value": au["value"]})
    return {"rows": rows, "units": u, "value_per_demo": c["value_per_demo"], "measured_close_rate": _pct(c["measured_close_rate"]) if c.get("measured_close_rate") is not None else None,
            "closed_demos": c.get("closed_demos"), "automated": au, "control": ct, "reply_rate": _pct(au["reply_rate"]) if au.get("reply_rate") is not None else None,
            "ai": ai, "ai_metered": bool(ai.get("priced")), "draft_cost_metered": bool(au.get("draft_cost_metered")), "since": c.get("since"),
            "months": c.get("months") or []}


def _in_use(st) -> tuple:
    """(stale_lead_factor, capacity_limit) the router runs with now, to mark that row of the experiment; () when the
    SDRs' limits differ."""
    limits = {o.capacity_limit for o in st.hs.list_owners() if o.team == "sdr"}
    if len(limits) != 1:
        return ()
    return (float(get_setting(st.conn, "stale_lead_factor") or 1.0), limits.pop())


def _capacity(st, sdrs: int) -> dict:
    """The capacity figures: breakdown() plus line_place (the capacity line's place in the pool: a week's work) and
    the week in words."""
    b = breakdown(st.conn, sdrs)
    days = float(get_setting(st.conn, "capacity_line_days") or DEFAULT_LINE_HORIZON_DAYS)
    b["line_place"] = max(1, int(round(b["per_month"] * days / 30.4)))
    b["line_days"] = round(days)
    b["hours_text"] = f"{b['hours']:.1f}".rstrip("0").rstrip(".")
    later = 0.0 if b["auto"] else FOLLOWUPS * b["minutes_followup"]
    b["at"] = [{"minutes": m, "per_month": round(sdrs * b["hours"] * 60 / (m + later))} for m in (20, 15, 12, 10) if abs(m - b["minutes_touch"]) > 0.5]
    b["switched"] = round(sdrs * b["hours"] * 60 / (b["minutes_touch"] - b["call_log_saving"] + (FOLLOWUPS * b["minutes_followup"] if b["auto"] else 0.0)))
    saving = b["call_share"] * max(0.0, b["minutes_call_log"] - b["minutes_call_log_auto"])
    b["notes_switched"] = round(sdrs * b["hours"] * 60 / (b["minutes_touch"] + (saving if b["call_notes_auto"] else -saving) - b["call_log_saving"] - b["skips"]["saving"] + later))
    model = skip_counts.stored(st.conn) or {}
    rep = model.get("replay") or {}
    would_save = b["skips"]["share"] * b["skips"]["caught"] * b["minutes_skip"]
    b["skips_switched"] = round(sdrs * b["hours"] * 60 / (b["minutes_lead"] + (b["skips"]["saving"] if b["skips_auto"] else -would_save)))
    booking = b["demo_share"] * max(0.0, b["minutes_booking"] - b["minutes_booking_auto"])
    b["booking_switched"] = round(sdrs * b["hours"] * 60 / (b["minutes_lead"] + (b["booking_saving"] if b["scheduling_auto"] else -booking)))
    b["skip_model"] = {"replay": rep, "threshold": model.get("threshold"),
                       "traits": sorted(((skip_counts.word(t), n[0], n[1]) for t, n in (model.get("traits") or {}).items()
                                         if n[1] >= skip_counts.MIN_DECISIONS and skip_counts.rate(n) >= max(model.get("threshold") or 0.5, (model.get("base") or 0.0) + skip_counts.MARGIN)),
                                        key=lambda x: -x[1] / x[2])}
    return b


def leadership_page(st) -> dict:
    """report: the stored evaluation dict (see evaluation.evaluate) or None; report_path; report_written (its
    generated_at, as text); queue_limits: the experiment's {runs, summary} or None; queue_limits_path;
    queue_limits_written (the file's time, as text). sdrs (active SDRs now); capacity (first touches a month the
    router plans for); desk (the SDRs' queue limit now in words, None when it differs by SDR); leads_per_month (the
    brief's 6,000, the per-1,000 denominator when the database has no lead count for the months); maturity_days;
    generator_factor (the generator's built-in late-touch factor), fresh_as_often ('twice as often') and
    late_as_often ('half as often').
    From the report, each None without it or without the part it needs:
      months: whole months, {before_rows, routed_rows, after_rows: [{month, worked, demos, rate %, median_hours,
        within_24h %}], before, routed, after (the report's rows), projected (the After is the months simulated ahead
        of the service clock, the AI steps on; the routed months up to the clock are then the middle group), ahead
        (those months by label), routed_span, left_out (partial months by label), routed_months, routed_word,
        span ('Jul 2027 to Oct 2028')}; clock_day ('31 Oct 2028');
      shared: {before, after, routed (the middle group, else None): {months, months_word, first, last, demos, leads,
        per_1000, demos_a_month, worked_a_month}, change %, leads_recorded (leads are the demo database's new leads by month of arrival,
        else the brief's figure a month), leads_text ('6,000 new leads in each history month, then 6,200, 6,000 and
        5,800 in Aug, Sep and Oct 2028', None unless recorded), more_a_month, same_capacity, all_beat,
        hours_before/after, touched_before/after, within_before/after %, late_share % (routed first touches after the
        first day)};
      ranking: the capacity head to head, mock only: {capacity, months, months_word, span, scale, version,
        model, hubspot, points, rows and phase2: [{label, v, bar, kind, live}], phase2_on, phase2_ready, vs_hubspot
        {mean, lo, hi}, vs_points, all_vs_mql {mean, lo, hi}, below_gate_picked, below_gate_by_channel [(channel, n)],
        ece};
      explore: {rows: [{label, assigned, worked, booked, rate %}], share % of slots picked at random, random_worked,
        random_booked, expected, cost, months, months_word, cost_a_month};
      precision: worked test leads, {k, model, points, points_ahead, hair, level}; gate: report["below_gate"];
      speed_effect: {factor, fast %, slow %, n_fast, n_slow};
      tier2: phase 3's experiment, {rows: [{label, leads, replied, reply_rate %, worked_by_sdr, matured, booked, rate %,
        predicted %}], on, explore %, send %, holdback %, daily_limit, sequence, lift (points), ready, needed, matured, refused};
      costs: {rows: [{label, reached, demos, rate %, hours, cost, per_demo, per_hour, value}], units, value_per_demo,
        measured_close_rate %, closed_demos, automated, control, reply_rate %, ai {calls, cost_usd, by_task}, ai_metered,
        draft_cost_metered}.
    setups: from queue_limits, {rows: [{label, demos, demos_lo, demos_hi, within, within_lo, within_hi, bar, in_use}],
      scale, line (the history's demos a month on the bars' scale, or None), seeds, seeds_word, fresh_words,
      first_desk, first_off, first_demos, speed_adds}.
    gain: the charts (see _gain): steps, projected, leads {rows, ticks, width, before_avg, after_avg}, channels
      {total, slices}, after_span."""
    report, ql, in_use = _load(REPORT), _load(QUEUE_LIMITS), _in_use(st)
    sdrs = sum(1 for o in st.hs.list_owners(active_only=True) if o.team == "sdr")
    fresh_as_often, late_as_often = _as_often(LATE_TOUCH_FACTOR)
    out = {"report": report, "report_path": str(REPORT), "report_written": _when((report or {}).get("generated_at")),
           "queue_limits": ql, "queue_limits_path": str(QUEUE_LIMITS), "queue_limits_written": _written(QUEUE_LIMITS) if ql else None,
           "sdrs": sdrs,
           "capacity": round(touches_per_month(st.conn, sdrs)), "cap": _capacity(st, sdrs),
           "desk": f"{_leads(in_use[1], 'waiting lead')} a desk" if in_use else None, "leads_per_month": LEADS_PER_MONTH,
           "maturity_days": DEMO_MATURITY_DAYS, "generator_factor": LATE_TOUCH_FACTOR, "fresh_as_often": fresh_as_often,
           "late_as_often": late_as_often, "months": None, "shared": None, "ranking": None, "explore": None, "precision": None,
           "gate": None, "speed_effect": None, "setups": None, "tier2": None, "costs": None}
    if report:
        now = clock(st.conn)
        mo = _months(report, now)
        out.update(clock_day=f"{now.day} {now:%b %Y}", months=mo if mo["before_rows"] or mo["after_rows"] else None, shared=_shared(st, report, mo), ranking=_ranking(report),
                   explore=_explore(report, mo["routed_months"]), precision=_precision(report), gate=report.get("below_gate"),
                   speed_effect=_speed_effect(report), tier2=_tier2(report), costs=_costs(report))
    if ql:
        out["setups"] = _setups(ql, out["shared"]["before"]["demos_a_month_raw"] if out["shared"] else None, in_use)
    out["gain"] = _gain(out["setups"], out["shared"], out["months"], st) if report else None
    return out
