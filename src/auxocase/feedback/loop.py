"""The learning loop: outcomes in, a reviewed model out.

Blueprint job 3 as designed, with the model in place of a weights table:

1. Pull outcomes from HubSpot (meetings and deal stages, H07) into the
   `outcomes` table with three label states per lead: booked, worked-no-demo,
   not-worked (L05). Record first-touch time for the speed-to-lead report.
2. Monitor by channel: a p-chart of the observed demo rate against the
   model's predicted rate per month, with control limits from the predicted
   rate; a channel outside its limits is flagged; fewer than 30 worked leads
   in a period is held, not judged (Blueprint job 3 step 5).
3. Retrain on a schedule or when the monitor fires (L09), on a sliding
   window, excluding immature leads. The candidate goes to the registry as
   `candidate` with its metrics beside the live model's, and nothing changes
   in routing until one named approver from marketing and one from sales
   approve it, one version at a time (Blueprint job 3 step 9). A model that
   does not beat the live one on precision@500 can still be approved (for
   example to recalibrate), but the page says so.
4. Promotion is a set-not-add write: the previous model is kept as `shadow`
   for one cycle and its scores are compared on the next month's labels.
"""
from __future__ import annotations

import math
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

from ..hubspot.client import HubSpotClient
from ..hubspot.models import OPEN_LEAD_STATUSES, TOUCHED_STATUSES
from ..scoring.features import feature_row
from ..scoring.model import DemoModel, Metrics, TrainingTable, train
from ..scoring.points import points_score
from ..storage.db import clock, dumps, loads, now_iso

MIN_GROUP = 30
def model_dir(conn: sqlite3.Connection) -> Path:
    """Where model pickles live: AUXO_MODEL_DIR, else a `models/` folder beside the database file, so two
    databases (the demo's and a test's) never share or overwrite each other's artifacts."""
    env = os.environ.get("AUXO_MODEL_DIR")
    if env:
        return Path(env)
    row = conn.execute("PRAGMA database_list").fetchone()
    db_file = row["file"] if row and row["file"] else ""
    return (Path(db_file).parent if db_file else Path("data")) / "models"


# ---------------------------------------------------------------- outcomes
def sync_outcomes(hs: HubSpotClient, conn: sqlite3.Connection, since: Optional[datetime] = None) -> dict:
    """Join HubSpot's view of what happened to every MQL into one row per lead. Leads older than `since` (default:
    the training window plus maturity, ~13 months) keep the rows earlier syncs wrote; their outcomes are settled."""
    since = since or (clock(conn) - timedelta(days=400))
    contacts = [c for c in hs.list_contacts(created_since=since) if c.lifecycle_stage != "lead"]
    n_booked = n_won = n_worked = 0
    for c in contacts:
        meetings = hs.meetings_for_contact(c.id)
        deals = hs.deals_for_contact(c.id)
        # Assigned is not worked: the owner alone, with the status still NEW/OPEN, is a lead waiting in a queue (L05, item 34).
        worked = int(bool(c.hubspot_owner_id) and (c.hs_lead_status in TOUCHED_STATUSES or bool(meetings)))
        booked = [m for m in meetings if m.outcome in ("SCHEDULED", "COMPLETED", "RESCHEDULED")]
        demo_at = min((m.start_time for m in booked), default=None)
        won = [d for d in deals if d.dealstage == "closedwon" or d.hs_is_closed_won]
        lost = [d for d in deals if d.dealstage == "closedlost"]
        amount = max((d.amount for d in deals), default=None)
        first_touch = None
        if worked:
            # The first change of hs_lead_status is the first SDR touch; HubSpot exposes it through property history (H02).
            first_touch = _first_status_change(conn, c.id)
        conn.execute(
            "INSERT INTO outcomes(contact_id, worked, first_touch_at, demo_booked, demo_booked_at, closed_won, closed_won_at, closed_lost, closed_lost_at, amount, label_as_of, updated_at, lead_created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(contact_id) DO UPDATE SET worked=excluded.worked, first_touch_at=COALESCE(excluded.first_touch_at, outcomes.first_touch_at), "
            "lead_created_at=excluded.lead_created_at, "
            "demo_booked=excluded.demo_booked, demo_booked_at=excluded.demo_booked_at, closed_won=excluded.closed_won, closed_won_at=excluded.closed_won_at, "
            "closed_lost=excluded.closed_lost, closed_lost_at=excluded.closed_lost_at, amount=excluded.amount, label_as_of=excluded.label_as_of, updated_at=excluded.updated_at",
            (c.id, worked, first_touch, int(bool(booked)), demo_at.isoformat() if demo_at else None, int(bool(won)),
             won[0].closedate.isoformat() if won and won[0].closedate else None, int(bool(lost)),
             lost[0].closedate.isoformat() if lost and lost[0].closedate else None, amount, now_iso(), now_iso(), c.createdate.isoformat()))
        n_worked += worked
        n_booked += int(bool(booked))
        n_won += int(bool(won))
    conn.execute("INSERT INTO run_log(job, started_at, finished_at, status, detail) VALUES(?,?,?,?,?)",
                 ("feedback.sync_outcomes", now_iso(), now_iso(), "ok", dumps({"mqls": len(contacts), "worked": n_worked, "booked": n_booked, "won": n_won})))
    return {"mqls": len(contacts), "worked": n_worked, "booked": n_booked, "won": n_won}


def _first_status_change(conn: sqlite3.Connection, contact_id: str) -> Optional[str]:
    try:
        r = conn.execute("SELECT MIN(changed_at) t FROM hs_property_history WHERE object_id=? AND property='hs_lead_status' AND value NOT IN ('', 'NEW', 'OPEN')", (contact_id,)).fetchone()
        return r["t"] if r else None
    except sqlite3.OperationalError:   # no mock tables: a real portal would be asked through the property-history API
        return None


# ---------------------------------------------------------------- training table
def build_training_table(hs: HubSpotClient, conn: sqlite3.Connection, window_months: int = 12,
                         as_of: Optional[datetime] = None) -> TrainingTable:
    as_of = as_of or clock(conn)
    start = as_of - timedelta(days=30 * window_months)
    outcomes = {r["contact_id"]: dict(r) for r in conn.execute("SELECT * FROM outcomes").fetchall()}
    holdouts = {r["contact_id"] for r in conn.execute("SELECT DISTINCT contact_id FROM routing_log WHERE decision='holdout_assigned'").fetchall()}
    rows, states, created, points, hub, closed, ids, hold = [], [], [], [], [], [], [], []
    companies = {}
    for c in hs.list_contacts(created_since=start):
        if c.lifecycle_stage == "lead" or c.createdate < start or c.createdate > as_of:
            continue
        if c.company_id not in companies:
            companies[c.company_id] = hs.get_company(c.company_id) if c.company_id else None
        co = companies[c.company_id]
        o = outcomes.get(c.id)
        if o and o["demo_booked"]:
            state = "booked"
        elif o and o["worked"]:
            state = "worked_no_demo"
        else:
            state = "not_worked"
        rows.append(feature_row(c, co))
        states.append(state)
        created.append(c.createdate)
        points.append(points_score(c, co))
        hub.append(float(c.hubspot_score or 0))
        closed.append((1 if o["closed_won"] else (0 if o["closed_lost"] else None)) if (o and o["demo_booked"]) else None)
        ids.append(c.id)
        hold.append(1 if c.id in holdouts or c.auxo_holdout else 0)
    from ..scoring.features import frame
    return TrainingTable(X=frame(rows), label_state=pd.Series(states), created=pd.Series(pd.to_datetime(created)),
                         points=pd.Series(points), hubspot_score=pd.Series(hub), closed=pd.Series(closed, dtype="float"), contact_ids=pd.Series(ids),
                         holdout=pd.Series(hold))


# ---------------------------------------------------------------- monitor
@dataclass
class ChannelPoint:
    period: str
    channel: str
    n: int
    demos: int
    demo_rate: Optional[float]
    predicted_rate: Optional[float]
    lcl: Optional[float]
    ucl: Optional[float]
    flag: str            # ok | above | below | held


def channel_monitor(hs: HubSpotClient, conn: sqlite3.Connection, months: int = 3, as_of: Optional[datetime] = None) -> list[ChannelPoint]:
    """A p-chart per channel: observed demo rate among worked leads against the model's predicted rate (L09, Blueprint job 3 step 5)."""
    as_of = as_of or clock(conn)
    outcomes = {r["contact_id"]: dict(r) for r in conn.execute("SELECT * FROM outcomes WHERE worked=1").fetchall()}
    last_scores = {r["contact_id"]: r["probability"] for r in conn.execute(
        "SELECT contact_id, probability FROM scores WHERE id IN (SELECT MAX(id) FROM scores GROUP BY contact_id)").fetchall()}
    groups: dict[tuple[str, str], list[tuple[int, float]]] = {}
    for c in hs.list_contacts(created_since=as_of - timedelta(days=31 * months)):
        o = outcomes.get(c.id)
        if not o or c.createdate < as_of - timedelta(days=31 * months):
            continue
        period = c.createdate.strftime("%Y-%m")
        p = last_scores.get(c.id, c.auxo_probability)
        groups.setdefault((period, c.channel), []).append((int(o["demo_booked"]), p if p is not None else float("nan")))
    out = []
    for (period, ch), items in sorted(groups.items()):
        n = len(items)
        demos = sum(d for d, _ in items)
        preds = [p for _, p in items if not (isinstance(p, float) and math.isnan(p))]
        pred = sum(preds) / len(preds) if preds else None
        if n < MIN_GROUP or pred is None:
            pt = ChannelPoint(period, ch, n, demos, demos / n if n else None, pred, None, None, "held")
        else:
            se = math.sqrt(pred * (1 - pred) / n)
            lcl, ucl = max(0.0, pred - 3 * se), min(1.0, pred + 3 * se)
            rate = demos / n
            flag = "above" if rate > ucl else ("below" if rate < lcl else "ok")
            pt = ChannelPoint(period, ch, n, demos, rate, pred, lcl, ucl, flag)
        out.append(pt)
        conn.execute("INSERT INTO channel_monitor(period, channel, n, demos, demo_rate, predicted_rate, lcl, ucl, flag, created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                     (pt.period, pt.channel, pt.n, pt.demos, pt.demo_rate, pt.predicted_rate, pt.lcl, pt.ucl, pt.flag, now_iso()))
    return out


# ---------------------------------------------------------------- registry and approvals
def next_version(conn: sqlite3.Connection) -> str:
    n = conn.execute("SELECT COUNT(*) AS n FROM model_registry").fetchone()["n"]
    return f"demo-v{n + 1}"


def train_candidate(hs: HubSpotClient, conn: sqlite3.Connection, as_of: Optional[datetime] = None,
                    window_months: int = 12, notes: str = "", prefer: Optional[str] = None) -> tuple[DemoModel, Metrics]:
    as_of = as_of or clock(conn)
    table = build_training_table(hs, conn, window_months, as_of)
    version = next_version(conn)
    model = train(table, as_of, version, prefer=prefer)
    path = model_dir(conn) / f"{version}.pkl"
    path.parent.mkdir(parents=True, exist_ok=True)
    model.save(path)
    conn.execute(
        "INSERT INTO model_registry(version, family, trained_at, train_rows, positives, metrics, feature_names, artifact_path, status, notes) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (version, model.family, as_of.isoformat(), model.metrics.rows, model.metrics.positives, dumps(model.metrics.to_dict()),
         dumps(model.feature_names), str(path), "candidate", notes))
    conn.execute("INSERT INTO run_log(job, started_at, finished_at, status, detail) VALUES(?,?,?,?,?)",
                 ("feedback.train_candidate", now_iso(), now_iso(), "ok", dumps({"version": version, "precision_at_k": model.metrics.precision_at_k, "lift": model.metrics.lift_at_k})))
    return model, model.metrics


def live_version(conn: sqlite3.Connection) -> Optional[dict]:
    r = conn.execute("SELECT * FROM model_registry WHERE status='live' ORDER BY promoted_at DESC LIMIT 1").fetchone()
    return dict(r) if r else None


def approve(conn: sqlite3.Connection, version: str, team: str, approver: str, decision: str, note: str = "") -> dict:
    """One approval row per team per version. 'approve' from both marketing and sales promotes; 'send_back' retires the candidate."""
    if team not in ("marketing", "sales"):
        raise ValueError("team must be marketing or sales")
    row = conn.execute("SELECT status FROM model_registry WHERE version=?", (version,)).fetchone()
    if not row or row["status"] != "candidate":
        raise ValueError(f"{version} is not a candidate")
    conn.execute("INSERT INTO approvals(version, team, approver, decision, note, created_at) VALUES(?,?,?,?,?,?)",
                 (version, team, approver, decision, note, now_iso()))
    if decision == "send_back":
        conn.execute("UPDATE model_registry SET status='sent_back', notes=COALESCE(notes,'') || ? WHERE version=?", (f" | sent back by {team}: {note}", version))
        return {"status": "sent_back"}
    teams = {r["team"] for r in conn.execute("SELECT team FROM approvals WHERE version=? AND decision='approve'", (version,)).fetchall()}
    if teams >= {"marketing", "sales"}:
        promote(conn, version)
        return {"status": "live"}
    return {"status": "candidate", "approved_by": sorted(teams)}


def promote(conn: sqlite3.Connection, version: str) -> None:
    conn.execute("UPDATE model_registry SET status='retired' WHERE status='shadow'")
    conn.execute("UPDATE model_registry SET status='shadow' WHERE status='live'")
    conn.execute("UPDATE model_registry SET status='live', promoted_at=? WHERE version=?", (now_iso(), version))


def shadow_comparison(hs: HubSpotClient, conn: sqlite3.Connection, k: int = 500) -> Optional[dict]:
    """Score last month's labelled leads with the live and shadow models and compare precision@k (L09: keep the previous model as a shadow)."""
    live, shadow = live_version(conn), conn.execute("SELECT * FROM model_registry WHERE status='shadow'").fetchone()
    if not live or not shadow:
        return None
    from ..scoring.model import precision_at_k, score_frame
    import numpy as np
    table = build_training_table(hs, conn, window_months=2)
    m = table.label_state.isin(["booked", "worked_no_demo"]).to_numpy()
    if m.sum() < 50:
        return None
    y = (table.label_state[m] == "booked").astype(int).to_numpy()
    rows = table.X[m].to_dict("records")
    out = {}
    for label, reg in (("live", live), ("shadow", dict(shadow))):
        model = DemoModel.load(Path(reg["artifact_path"]))
        p, _ = score_frame(model, rows)
        out[label] = {"version": reg["version"], "precision_at_k": round(precision_at_k(y, np.asarray(p), min(k, len(y))), 4)}
    out["n"] = int(m.sum())
    return out


def _hours_to_first_touch(rows) -> list[float]:
    out = []
    for r in rows:
        try:
            out.append((datetime.fromisoformat(r["first_touch_at"]) - datetime.fromisoformat(r["lead_created_at"])).total_seconds() / 3600)
        except (TypeError, ValueError):
            continue
    return sorted(h for h in out if h >= 0)


def _speed(hours: list[float]) -> dict:
    if not hours:
        return {"n": 0}
    return {"n": len(hours), "median_hours": round(hours[len(hours) // 2], 1), "within_24h": round(sum(h <= 24 for h in hours) / len(hours), 3)}


def speed_to_lead(conn: sqlite3.Connection) -> dict:
    """Hours from the lead's creation to the first SDR touch (BrightPath answer 4: the median was 3 days).

    The top-level numbers are for leads this service routed; `baseline` is every worked lead it did not route
    (the history, picked by gut feel)."""
    routed = _hours_to_first_touch(conn.execute(
        "SELECT o.first_touch_at, o.lead_created_at FROM outcomes o WHERE o.worked=1 AND o.contact_id IN "
        "(SELECT contact_id FROM routing_log WHERE decision IN ('assigned','holdout_assigned'))").fetchall())
    baseline = _hours_to_first_touch(conn.execute(
        "SELECT o.first_touch_at, o.lead_created_at FROM outcomes o WHERE o.worked=1 AND o.contact_id NOT IN "
        "(SELECT contact_id FROM routing_log WHERE decision IN ('assigned','holdout_assigned'))").fetchall())
    return {**_speed(routed), "baseline": _speed(baseline)}


def speed_effect(conn: sqlite3.Connection, fresh_hours: float = 24.0) -> dict:
    """Demo rate for leads first touched inside the fresh window against after it, from outcomes: the measured
    value of the router's `stale_lead_factor`. Confounded by which leads got fast touches; read it as a range."""
    rows = conn.execute("SELECT first_touch_at, lead_created_at, demo_booked FROM outcomes WHERE worked=1 AND first_touch_at IS NOT NULL").fetchall()
    fast, slow = [0, 0], [0, 0]
    for r in rows:
        try:
            h = (datetime.fromisoformat(r["first_touch_at"]) - datetime.fromisoformat(r["lead_created_at"])).total_seconds() / 3600
        except (TypeError, ValueError):
            continue
        g = fast if h <= fresh_hours else slow
        g[0] += 1
        g[1] += r["demo_booked"]
    if min(fast[0], slow[0]) < 100:
        return {"n_fast": fast[0], "n_slow": slow[0]}
    rf, rs = fast[1] / fast[0], slow[1] / slow[0]
    return {"n_fast": fast[0], "n_slow": slow[0], "rate_fast": round(rf, 3), "rate_slow": round(rs, 3), "factor": round(rs / rf, 2) if rf else None}


def success_by_month(conn: sqlite3.Connection) -> list[dict]:
    """BrightPath answer 9, month by month at the same 12 SDRs: leads worked, demos booked (the SQLs), and how fast
    the first touch came. A month is 'routed' when most of its first touches were leads this service assigned."""
    rows = conn.execute(
        "SELECT substr(o.first_touch_at, 1, 7) month, o.first_touch_at, o.lead_created_at, o.demo_booked, "
        "CASE WHEN o.contact_id IN (SELECT contact_id FROM routing_log WHERE decision IN ('assigned','holdout_assigned')) THEN 1 ELSE 0 END routed "
        "FROM outcomes o WHERE o.worked=1 AND o.first_touch_at IS NOT NULL").fetchall()
    months: dict[str, list] = {}
    for r in rows:
        months.setdefault(r["month"], []).append(r)
    out = []
    for m in sorted(months):
        rs = months[m]
        sp = _speed(_hours_to_first_touch(rs))
        out.append({"month": m, "worked": len(rs), "demos": sum(r["demo_booked"] for r in rs),
                    "routed_share": round(sum(r["routed"] for r in rs) / len(rs), 2), **{k: sp.get(k) for k in ("median_hours", "within_24h")}})
    return out


# ---------------------------------------------------------------- loss reasons and the MQL gate
def classify_losses(hs: HubSpotClient, conn: sqlite3.Connection, llm) -> dict:
    """Read every closed-lost reason not read yet into a fixed category (llm/loss_reasons.py). Idempotent by deal."""
    from ..llm.loss_reasons import read_loss_reason
    done = {r["deal_id"] for r in conn.execute("SELECT deal_id FROM loss_reasons")}
    counts: dict[str, int] = {}
    for d in hs.deals_modified_since(datetime(2000, 1, 1)):
        if d.dealstage != "closedlost" or d.id in done:
            continue
        r = read_loss_reason(llm, d.closed_lost_reason or "")
        conn.execute("INSERT OR REPLACE INTO loss_reasons(deal_id, contact_id, category, quote, source, raw, created_at) VALUES(?,?,?,?,?,?,?)",
                     (d.id, d.contact_id, r.category, r.quote, r.source, d.closed_lost_reason or "", now_iso()))
        counts[r.category] = counts.get(r.category, 0) + 1
    return counts


def loss_report(hs: HubSpotClient, conn: sqlite3.Connection) -> dict:
    """Loss categories by company size band, as the record shows it (blank size is its own column)."""
    from ..scoring.features import size_band
    table: dict[str, dict[str, int]] = {}
    companies: dict = {}
    for r in conn.execute("SELECT contact_id, category FROM loss_reasons").fetchall():
        c = hs.get_contact(r["contact_id"])
        if c is None:
            continue
        if c.company_id not in companies:
            companies[c.company_id] = hs.get_company(c.company_id) if c.company_id else None
        co = companies[c.company_id]
        band = size_band(co.num_employees if co else None)
        table.setdefault(r["category"], {}).setdefault(band, 0)
        table[r["category"]][band] += 1
    return table


def mql_gate_report(hs: HubSpotClient, conn: sqlite3.Connection, months: int = 3, as_of: Optional[datetime] = None) -> list[dict]:
    """Per channel: how many leads reach MQL, and how many are missing a title or a company size. Shows where
    HubSpot's gate, which only the MQLs pass to the pool, drops leads before this service sees them."""
    as_of = as_of or clock(conn)
    rows: dict[str, dict] = {}
    companies: dict = {}
    for c in hs.list_contacts(created_since=as_of - timedelta(days=31 * months)):
        if c.company_id not in companies:
            companies[c.company_id] = hs.get_company(c.company_id) if c.company_id else None
        co = companies[c.company_id]
        g = rows.setdefault(c.channel, {"channel": c.channel, "leads": 0, "mql": 0, "no_title": 0, "no_size": 0})
        g["leads"] += 1
        g["mql"] += int(c.lifecycle_stage != "lead")
        g["no_title"] += int(not c.job_title)
        g["no_size"] += int(not (co and co.num_employees))
    out = []
    for g in sorted(rows.values(), key=lambda x: -x["leads"]):
        n = g["leads"]
        out.append({"channel": g["channel"], "leads": n, "mql_share": round(g["mql"] / n, 3),
                    "no_title": round(g["no_title"] / n, 3), "no_size": round(g["no_size"] / n, 3)})
    return out
