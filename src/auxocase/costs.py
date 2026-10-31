"""Cost against outcome: what a demo costs by the way it was reached.

The brief fixes the team at 12 SDRs, so SDR time is the scarce input and the
question for any change is demos per SDR hour, and dollars per demo. This
module prices two ways of reaching a lead from the service's own records:

- an SDR's touch on a ranked lead (routing assigned it, the SDR worked it);
- the automated sequence (phase 3): a drafted email sent by HubSpot, and an
  SDR's time only for the leads that reply.

The unit costs are settings with defaults, not facts: BrightPath's answers
give the contract value (24,000 dollars) and the close rate (about 20% of
demos), nothing on SDR cost or minutes a touch. Change them with
`set_setting(conn, "cost_sdr_hour", "52")` and the report follows. The
mock's outcomes are simulated, so on the mock every figure here carries the
report's dagger; on a real portal the counts are HubSpot's and only the unit
costs are assumptions.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from typing import Optional

import json
import os
import urllib.parse
import urllib.request

from . import capacity
from .llm import usage
from .routing.engine import REPLY_REASON
from .storage.db import clock, get_setting

DEFAULTS = {
    "cost_sdr_hour": 45.0,         # loaded hourly cost of an SDR (salary, benefits, tools)
    "sdr_minutes_touch": capacity.DEFAULTS["sdr_minutes_touch"],   # a cold first touch: read the reason, the notes, write or call
    "sdr_minutes_reply": capacity.DEFAULTS["sdr_minutes_reply"],   # answering a lead who replied to the sequence
    "cost_draft": 0.02,            # one Claude draft when none has been metered yet (about two thousand tokens on Opus)
    "acv": 24000.0,                # the brief: average annual contract value
    "close_rate": 0.20,            # the brief: of the demos booked, about 20% close
}
REVENIUM_ANALYTICS = "https://app.revenium.ai/api/v2/analytics"


def unit(conn: sqlite3.Connection, key: str) -> float:
    v = get_setting(conn, key)
    return float(v) if v else DEFAULTS[key]


def units(conn: sqlite3.Connection) -> dict:
    return {k: unit(conn, k) for k in DEFAULTS}


def _per(a: float, b: float, nd: int = 2) -> Optional[float]:
    return round(a / b, nd) if b else None


def cost_report(conn: sqlite3.Connection, now: Optional[datetime] = None, since: Optional[datetime] = None) -> dict:
    """Demos, SDR hours, dollars and value by route. Both routes are counted from `since`: by default the day the
    automated sequence first ran, so the SDR route covers the same months, or everything recorded when it never
    has. `value_per_demo` is the contract value times the close rate from the brief; `measured_close_rate` is what
    the outcomes show for demos old enough to have closed, when there are at least 50 of them."""
    now = now or clock(conn)
    u = units(conn)
    value_per_demo = u["acv"] * u["close_rate"]
    if since is None:
        first = conn.execute("SELECT MIN(created_at) s FROM tier2").fetchone()["s"]
        since = datetime.fromisoformat(first) if first else None

    # SDR touches on ranked leads: assignments by routing (the exploration slice included), not the replies below.
    sdr = conn.execute(
        "SELECT COUNT(*) worked, COALESCE(SUM(o.demo_booked), 0) demos FROM outcomes o WHERE o.worked = 1 AND o.contact_id IN "
        "(SELECT contact_id FROM routing_log WHERE decision IN ('assigned', 'holdout_assigned') AND COALESCE(reason, '') != ? AND created_at >= ?) "
        "AND o.contact_id NOT IN (SELECT contact_id FROM tier2 WHERE arm = 'emailed')",
        (REPLY_REASON, since.isoformat(timespec="seconds") if since else "")).fetchone()
    sdr_hours = sdr["worked"] * u["sdr_minutes_touch"] / 60
    sdr_cost = sdr_hours * u["cost_sdr_hour"]

    # The automated sequence: drafts sent, replies answered, demos among the emailed arm.
    t2 = conn.execute(
        "SELECT COUNT(*) emailed, COALESCE(SUM(t.source = 'llm'), 0) llm_drafts, COALESCE(SUM(t.replied_at IS NOT NULL), 0) replied, "
        "COALESCE(SUM(o.demo_booked), 0) demos FROM tier2 t LEFT JOIN outcomes o ON o.contact_id = t.contact_id "
        "WHERE t.arm = 'emailed' AND t.status != 'refused'").fetchone()
    ctl = conn.execute(
        "SELECT COUNT(*) leads, COALESCE(SUM(o.worked), 0) worked, COALESCE(SUM(o.demo_booked), 0) demos FROM tier2 t "
        "LEFT JOIN outcomes o ON o.contact_id = t.contact_id WHERE t.arm = 'control'").fetchone()
    reply_hours = t2["replied"] * u["sdr_minutes_reply"] / 60
    # Drafting: each draft the Claude CLI metered at its own cost (the figure Revenium receives); the rest at the metered
    # mean when any call has run, else at the assumed unit cost. The mock's template drafts stand in for Claude's, so
    # they are priced too: the question is what the route costs on a real portal.
    emailed_ids = {r["contact_id"] for r in conn.execute("SELECT contact_id FROM tier2 WHERE arm = 'emailed' AND status != 'refused'")}
    metered = usage.draft_costs(conn, emailed_ids)
    draft_unit = metered["mean_cost"] if metered["mean_cost"] is not None else u["cost_draft"]
    draft_cost = (metered["total"] or 0.0) + max(t2["emailed"] - metered["metered"], 0) * draft_unit
    draft_pricing = ("metered" if metered["metered"] >= t2["emailed"] else "metered mean") if metered["mean_cost"] is not None else "assumed"
    t2_cost = draft_cost + reply_hours * u["cost_sdr_hour"]
    # What the control arm would have booked at the emailed arm's size: the demos the sequence added, not just found.
    ctl_rate = _per(ctl["demos"], ctl["leads"], 4)
    added = round(t2["demos"] - (ctl_rate or 0.0) * t2["emailed"], 1) if ctl["leads"] else None

    # What the replies cost the ranked route: the SDR time they took would have gone to ranked leads at the ranked
    # route's demo rate. Net is what the sequence added over the control arm, less that. Break-even is the reply time
    # at which the two cancel: above it the sequence costs demos, below it adds them.
    touch_share = u["sdr_minutes_reply"] / u["sdr_minutes_touch"] if u["sdr_minutes_touch"] else 0.0
    forgone = round((sdr["demos"] / sdr["worked"] if sdr["worked"] else 0.0) * t2["replied"] * touch_share, 1)
    net = round(added - forgone, 1) if added is not None else None
    break_even = round((added * u["sdr_minutes_touch"]) / (sdr["demos"] / sdr["worked"] * t2["replied"]), 1) \
        if added is not None and sdr["worked"] and sdr["demos"] and t2["replied"] else None
    months = demos_by_month(conn, since, now)

    closed = conn.execute(
        "SELECT COUNT(*) demos, COALESCE(SUM(closed_won), 0) won FROM outcomes WHERE demo_booked = 1 AND demo_booked_at <= ?",
        ((now - timedelta(days=60)).isoformat(),)).fetchone()
    measured = _per(closed["won"], closed["demos"], 3) if closed["demos"] >= 50 else None

    ai = usage.summary(conn)
    return {
        "units": u, "since": since.isoformat(timespec="seconds") if since else None,
        "value_per_demo": round(value_per_demo), "measured_close_rate": measured, "closed_demos": closed["demos"],
        "ai": {**ai, "draft_mean_cost": metered["mean_cost"], "drafts_priced": metered["drafts_priced"],
               "source": "metered per call by the Claude CLI, the same figure it reports to Revenium" if ai["priced"] else "assumed (no priced calls yet)"},
        "sdr": {"worked": sdr["worked"], "demos": sdr["demos"], "demo_rate": _per(sdr["demos"], sdr["worked"], 3),
                "sdr_hours": round(sdr_hours, 1), "cost": round(sdr_cost), "cost_per_demo": _per(sdr_cost, sdr["demos"], 0),
                "demos_per_sdr_hour": _per(sdr["demos"], sdr_hours, 2), "value": round(sdr["demos"] * value_per_demo)},
        "automated": {"emailed": t2["emailed"], "llm_drafts": t2["llm_drafts"], "replied": t2["replied"],
                      "reply_rate": _per(t2["replied"], t2["emailed"], 3), "demos": t2["demos"], "demo_rate": _per(t2["demos"], t2["emailed"], 3),
                      "sdr_hours": round(reply_hours, 1), "draft_cost": round(draft_cost, 2), "draft_unit_cost": round(draft_unit, 5),
                      "drafts_metered": metered["metered"], "draft_pricing": draft_pricing, "draft_cost_metered": metered["mean_cost"] is not None,
                      "cost": round(t2_cost),
                      "cost_per_demo": _per(t2_cost, t2["demos"], 0), "demos_per_sdr_hour": _per(t2["demos"], reply_hours, 2),
                      "demos_per_100_emails": _per(100 * t2["demos"], t2["emailed"], 1), "value": round(t2["demos"] * value_per_demo),
                      "demos_added": added, "value_added": round(added * value_per_demo) if added is not None else None,
                      "demos_forgone": forgone, "demos_net": net, "value_net": round(net * value_per_demo) if net is not None else None,
                      "break_even_reply_minutes": break_even},
        "months": months,
        "control": {"leads": ctl["leads"], "worked_by_sdr": ctl["worked"], "demos": ctl["demos"], "demo_rate": ctl_rate},
    }


def demos_by_month(conn: sqlite3.Connection, since: Optional[datetime], now: datetime, before: int = 3) -> list[dict]:
    """Demos booked a month, and how many came through the sequence: the `before` months before the sequence's first
    month, that month, and every month since up to the service clock's (whole months, so the clock's own month is
    partial until it ends). The plain test of whether the sequence added demos or only moved SDR time: the 12 SDRs
    are fixed, so total demos a month is what has to rise."""
    rows = conn.execute(
        "SELECT substr(demo_booked_at, 1, 7) m, COUNT(*) demos, "
        "COALESCE(SUM(contact_id IN (SELECT contact_id FROM tier2 WHERE arm = 'emailed')), 0) via_sequence "
        "FROM outcomes WHERE demo_booked = 1 AND demo_booked_at IS NOT NULL AND substr(demo_booked_at, 1, 7) <= ? GROUP BY 1 ORDER BY 1",
        (now.strftime("%Y-%m"),)).fetchall()
    if since is None:
        return [{"month": r["m"], "demos": r["demos"], "via_sequence": r["via_sequence"], "period": "before"} for r in rows]
    first = since.strftime("%Y-%m")
    out = []
    for r in rows:
        if r["m"] < first and sum(1 for x in rows if x["m"] < first and x["m"] > r["m"]) >= before:
            continue
        out.append({"month": r["m"], "demos": r["demos"], "via_sequence": r["via_sequence"],
                    "period": "before" if r["m"] < first else ("first" if r["m"] == first else "with")})
    return out


def revenium_cost(start: datetime, end: datetime, product: str = usage.PRODUCT, api_key: Optional[str] = None,
                  timeout_s: float = 20.0) -> dict:
    """The product's metered cost by task over [start, end] from Revenium's analytics API: the cross-check for the
    local `llm_usage` total, and the one place the owner's own Claude Code sessions and the service's calls sit
    side by side. Needs a read key (`rev_rk_*`) or write key (`rev_sk_*`) in `REVENIUM_API_KEY`; the metering key
    the CLI installs can only send. Returns {"error": ...} rather than raising, so a report never fails on it."""
    key = api_key or os.environ.get("REVENIUM_API_KEY") or ""
    if not key:
        return {"error": "REVENIUM_API_KEY is not set (a rev_rk_ read key or rev_sk_ write key; the CLI's rev_mk_ key only sends)"}
    if key.startswith("rev_mk_"):
        return {"error": "REVENIUM_API_KEY is a metering key (rev_mk_), which can only send; use a rev_rk_ read key"}
    q = urllib.parse.urlencode({"startDate": start.strftime("%Y-%m-%dT%H:%M:%SZ"), "endDate": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                "metricType": "sum", "size": 100})
    out: dict = {"product": product, "start": start.isoformat(timespec="seconds"), "end": end.isoformat(timespec="seconds")}
    for name in ("cost-by-product-aggregated", "cost-by-task-aggregated"):
        req = urllib.request.Request(f"{REVENIUM_ANALYTICS}/{name}?{q}", headers={"Authorization": f"Bearer {key}", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                data = json.loads(resp.read().decode())
        except Exception as e:      # the network, the key, the tenant: reported, never raised
            return {**out, "error": f"{name}: {type(e).__name__}: {str(e)[:200]}"}
        items = ((data.get("_embedded") or {}).get("items")) or []
        groups = {}
        for it in items:
            cost = next((m.get("metricResult") for m in it.get("metrics", []) if m.get("metricType") != "runs"), None)
            runs = next((m.get("metricResult") for m in it.get("metrics", []) if m.get("metricType") == "runs"), None)
            groups[it.get("groupName")] = {"cost_usd": cost, "runs": runs}
        out["by_" + name.split("-")[2]] = groups
    out["product_cost_usd"] = (out["by_product"].get(product) or {}).get("cost_usd")
    return out
