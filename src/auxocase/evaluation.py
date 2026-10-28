"""The evaluation report: the numbers both teams look at, written to docs/evaluation.md.

What it reports and why (L03, L04, L05, L12, L13):
- precision and lift at the capacity cut-off (K=500 a month) for the live
  model against the points model and HubSpot's own score, on a time-ordered
  validation window, computed on worked leads only and said to be so;
- AUC-PR beside its baseline, Brier and ECE with the reliability table;
- predicted vs observed demo rate by channel, the brief's own complaint;
- the exploration slice: how many holdout leads were worked and how they
  booked against the ranked ones (the only unbiased lift estimate);
- override counts and override accuracy where outcomes exist;
- speed to lead.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

import numpy as np

from .feedback import loop as fb
from .hubspot.client import HubSpotClient
from .scoring.model import precision_at_k
from .storage.db import loads


def _fmt(x, nd=3):
    try:
        return f"{float(x):.{nd}f}"
    except (TypeError, ValueError):
        return "n/a"


def evaluate(hs: HubSpotClient, conn: sqlite3.Connection) -> dict:
    out: dict = {"generated_at": datetime.utcnow().isoformat(timespec="seconds")}
    live = fb.live_version(conn)
    out["live"] = live["version"] if live else None
    out["metrics"] = loads(live["metrics"]) if live else None

    # HubSpot's points vs ours on the same labelled rows (worked leads only).
    table = fb.build_training_table(hs, conn, window_months=14)
    m = table.label_state.isin(["booked", "worked_no_demo"]).to_numpy()
    y = (table.label_state[m] == "booked").astype(int).to_numpy()
    out["labelled_rows"] = int(m.sum())
    out["positives"] = int(y.sum())
    if live and m.sum() >= 100:
        from .scoring.model import DemoModel, score_frame
        model = DemoModel.load(Path(live["artifact_path"]))
        rows = table.X[m].to_dict("records")
        p, _ = score_frame(model, rows)
        ids = table.contact_ids[m].tolist()
        hub = np.array([(hs.get_contact(i).hubspot_score if hs.get_contact(i) else 0) for i in ids], dtype=float)
        k = min(500, len(y))
        out["head_to_head"] = {
            "k": k,
            "model_precision_at_k": round(precision_at_k(y, np.asarray(p), k), 4),
            "points_table_precision_at_k": round(precision_at_k(y, table.points[m].to_numpy().astype(float), k), 4),
            "hubspot_score_precision_at_k": round(precision_at_k(y, hub, k), 4),
            "random_precision": round(float(y.mean()), 4),
        }
        ch = table.X.loc[m, "channel"].to_numpy()
        out["by_channel_all_history"] = {c: {"n": int((ch == c).sum()), "observed": round(float(y[ch == c].mean()), 3), "predicted": round(float(np.asarray(p)[ch == c].mean()), 3)} for c in sorted(set(ch))}

    # Exploration slice vs ranked slice.
    r = conn.execute("SELECT r.decision, COUNT(*) n, SUM(o.worked) worked, SUM(o.demo_booked) booked FROM routing_log r LEFT JOIN outcomes o ON o.contact_id=r.contact_id "
                     "WHERE r.decision IN ('assigned','holdout_assigned') GROUP BY r.decision").fetchall()
    out["slices"] = {row["decision"]: {"assigned": row["n"], "worked": row["worked"] or 0, "booked": row["booked"] or 0} for row in r}

    ov = conn.execute("SELECT action, COUNT(*) n, SUM(COALESCE(o.demo_booked,0)) booked FROM overrides v LEFT JOIN outcomes o ON o.contact_id=v.contact_id GROUP BY action").fetchall()
    out["overrides"] = {row["action"]: {"n": row["n"], "booked": row["booked"] or 0} for row in ov}
    out["speed_to_lead"] = fb.speed_to_lead(conn)
    out["monitor"] = [dict(x) for x in conn.execute("SELECT * FROM channel_monitor WHERE created_at=(SELECT MAX(created_at) FROM channel_monitor) ORDER BY period, channel").fetchall()]
    return out


def write_report(hs: HubSpotClient, conn: sqlite3.Connection, path: Path) -> str:
    e = evaluate(hs, conn)
    L = [f"# Evaluation report", "", f"Generated {e['generated_at']} UTC. Live model: `{e['live'] or 'none (points table)'}`.", ""]
    L += ["All precision figures are computed on leads an SDR actually worked, because unworked leads have no label. They are biased by the SDRs' past choices until the exploration slice has run for a few months (knowledge base L05).", ""]
    if e.get("head_to_head"):
        h = e["head_to_head"]
        L += ["## Head to head at the capacity line", "", f"K = {h['k']} (one month of SDR capacity), {e['labelled_rows']} labelled leads, {e['positives']} demos.", "",
              "| Ranker | Precision@K |", "|---|---|",
              f"| AuxoCase live model | **{h['model_precision_at_k']}** |",
              f"| Points table (channel-aware, cold start) | {h['points_table_precision_at_k']} |",
              f"| HubSpot's current points (title, size, pages) | {h['hubspot_score_precision_at_k']} |",
              f"| Random (base rate) | {h['random_precision']} |", ""]
    if e.get("metrics"):
        m = e["metrics"]
        L += ["## Validation window (time-ordered)", "", f"Months {', '.join(m['validation_months'])}: precision@{m['k']} {m['precision_at_k']} vs HubSpot's points {m['hubspot_precision_at_k']} (lift {m['lift_at_k']}x) and the service's cold-start table {m['points_precision_at_k']} (lift {m['lift_over_points']}x); AUC-PR {m['auc_pr']} against a base rate of {m['base_rate']}; AUC-ROC {m['auc_roc']}; Brier {m['brier']}; ECE {m['ece']}. Challenger: {m['challenger']}.", "",
              "| Predicted | n | Observed |", "|---|---|---|"] + [f"| {b['lo']}–{b['hi']} | {b['n']} | {b['observed']} |" for b in m["calibration_bins"]] + [""]
    if e.get("by_channel_all_history"):
        L += ["## By channel (all labelled history)", "", "| Channel | n | Observed demo rate | Predicted |", "|---|---|---|---|"]
        L += [f"| {c} | {v['n']} | {v['observed']} | {v['predicted']} |" for c, v in e["by_channel_all_history"].items()] + [""]
    if e.get("slices"):
        L += ["## Ranked slice vs exploration slice", "", "| Slice | Assigned | Worked | Booked |", "|---|---|---|---|"]
        L += [f"| {k} | {v['assigned']} | {v['worked']} | {v['booked']} |" for k, v in e["slices"].items()] + [""]
    if e.get("overrides"):
        L += ["## SDR overrides", "", "| Action | Count | Booked |", "|---|---|---|"] + [f"| {k} | {v['n']} | {v['booked']} |" for k, v in e["overrides"].items()] + [""]
    s = e.get("speed_to_lead") or {}
    if s.get("n"):
        L += ["## Speed to lead", "", f"{s['n']} assigned leads touched; median {s['median_hours']} h to first touch; {round(100 * s['within_24h'])}% within 24 h.", ""]
    if e.get("monitor"):
        L += ["## Channel monitor (latest run)", "", "| Period | Channel | Worked | Demos | Observed | Predicted | Flag |", "|---|---|---|---|---|---|---|"]
        L += [f"| {p['period']} | {p['channel']} | {p['n']} | {p['demos']} | {_fmt(p['demo_rate'])} | {_fmt(p['predicted_rate'])} | {p['flag']} |" for p in e["monitor"]] + [""]
    text = "\n".join(L)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return text
