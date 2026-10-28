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
- first, what BrightPath's leadership said a win looks like (answer 9): more
  demos booked and faster follow-up at the same 12 SDRs, month by month;
- speed to lead from the lead's creation, routed leads against the history;
- where HubSpot's MQL gate drops leads, by channel, with their missing fields;
- why deals were lost, from the free-text reasons read into categories.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from typing import Optional
from pathlib import Path

import numpy as np

from .feedback import loop as fb
from .hubspot.client import HubSpotClient
from .scoring.model import precision_at_k
from .storage.db import clock, loads


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
    if live:
        from .scoring.model import DemoModel as _DM
        out["truth_check"] = whole_pool_truth_check(hs, conn, _DM.load(Path(live["artifact_path"])))
    out["speed_to_lead"] = fb.speed_to_lead(conn)
    out["success_by_month"] = fb.success_by_month(conn)
    out["speed_effect"] = fb.speed_effect(conn)
    from .storage.db import get_setting
    out["stale_lead_factor"] = get_setting(conn, "stale_lead_factor") or "1.0"
    out["mql_gate"] = fb.mql_gate_report(hs, conn)
    out["losses"] = fb.loss_report(hs, conn)
    out["enrichment"] = [dict(r) for r in conn.execute(
        "SELECT COUNT(*) asked, SUM(filled <> '{}') filled, SUM(asked LIKE '%job_title%') asked_title, "
        "SUM(filled LIKE '%job_title%') got_title, SUM(asked LIKE '%num_employees%') asked_size, "
        "SUM(filled LIKE '%num_employees%') got_size FROM enrichment_log").fetchall()][0]
    out["monitor"] = [dict(x) for x in conn.execute("SELECT * FROM channel_monitor WHERE created_at=(SELECT MAX(created_at) FROM channel_monitor) ORDER BY period, channel").fetchall()]
    return out


def whole_pool_truth_check(hs: HubSpotClient, conn: sqlite3.Connection, model, days: int = 31) -> Optional[dict]:
    """Mock only. Rank the newest `days` of MQLs (worked or not) by the model and by HubSpot's score, and average
    the generator's hidden demo chance (touched promptly) in each top third: what each ranker would deliver if
    the SDRs worked its list. Real data cannot do this; the exploration slice estimates it over months."""
    if not conn.execute("SELECT 1 FROM sim_truth LIMIT 1").fetchone():
        return None
    import random
    from .data.generate import true_demo_probability, truth_for
    from .scoring.features import feature_row
    from .scoring.model import score_frame
    since = clock(conn) - timedelta(days=days)
    pool = [c for stage in ("marketingqualifiedlead", "salesqualifiedlead", "opportunity", "customer")
            for c in hs.list_contacts(lifecycle_stage=stage, created_since=since)]   # every MQL, whatever happened since
    if len(pool) < 100:
        return None
    companies = {c.company_id: hs.get_company(c.company_id) for c in pool if c.company_id}
    p_model, _ = score_frame(model, [feature_row(c, companies.get(c.company_id)) for c in pool])
    truth = np.array([true_demo_probability(c, companies.get(c.company_id), random.Random(0), truth_for(conn, c.id)) for c in pool])
    hub = np.array([c.hubspot_score or 0 for c in pool], dtype=float)
    k = max(1, len(pool) // 3)
    top = lambda s: round(float(truth[np.argsort(-np.asarray(s, dtype=float), kind="stable")[:k]].mean()), 3)
    return {"n": len(pool), "k": k, "model": top(p_model), "hubspot_score": top(hub), "random": round(float(truth.mean()), 3),
            "best_possible": round(float(np.sort(truth)[-k:].mean()), 3)}


def write_report(hs: HubSpotClient, conn: sqlite3.Connection, path: Path) -> str:
    e = evaluate(hs, conn)
    L = [f"# Evaluation report", "", f"Generated {e['generated_at']} UTC. Live model: `{e['live'] or 'none (points table)'}`.", ""]
    full = sorted(m["worked"] for m in e.get("success_by_month", []))
    months = [m for m in e.get("success_by_month", []) if full and m["worked"] >= 0.6 * full[len(full) // 2]]   # whole months only
    if months:
        before = [m for m in months if m["routed_share"] < 0.5]
        after = [m for m in months if m["routed_share"] >= 0.5]
        L += ["## The win BrightPath asked for: more demos, faster follow-up, same 12 SDRs", "",
              "Month of first touch. Before: the history, SDRs choosing by gut feel and HubSpot's score. After: leads this service ranked and routed.", "",
              "| Month | Routed | Worked | Demos booked | Demo rate | Median hours to first touch | Touched within 24 h |", "|---|---|---|---|---|---|---|"]
        L += [f"| {m['month']} | {'yes' if m['routed_share'] >= 0.5 else 'no'} | {m['worked']} | {m['demos']} | {_fmt(m['demos'] / m['worked'], 2)} | "
              f"{_fmt(m['median_hours'], 0)} | {_fmt(100 * (m['within_24h'] or 0), 0)}% |" for m in months] + [""]
        if before and after:
            b_d = sum(m["demos"] for m in before) / len(before)
            a_d = sum(m["demos"] for m in after) / len(after)
            b_w = sum(m["worked"] for m in before) / len(before)
            a_w = sum(m["worked"] for m in after) / len(after)
            L += [f"Average per month: {b_d:.0f} demos from {b_w:.0f} leads worked before, {a_d:.0f} from {a_w:.0f} after "
                  f"({(a_d / b_d - 1) * 100:+.0f}% demos at the same capacity). On the mock, the speed effect is the generator's assumption from "
                  "BrightPath's answer 4 (a lead touched within a day books about twice as often); on the real portal it is measured.", ""]
    L += ["All precision figures are computed on leads an SDR actually worked, because unworked leads have no label. They are biased by the SDRs' past choices until the exploration slice has run for a few months (knowledge base L05).", ""]
    if e.get("head_to_head"):
        h = e["head_to_head"]
        L += ["## Head to head at the capacity line", "", f"K = {h['k']} (one month of SDR capacity), {e['labelled_rows']} labelled leads, {e['positives']} demos.", "",
              "| Ranker | Precision@K |", "|---|---|",
              f"| AuxoCase live model | **{h['model_precision_at_k']}** |",
              f"| Points table (channel-aware, cold start) | {h['points_table_precision_at_k']} |",
              f"| HubSpot's current points (title, size, pages) | {h['hubspot_score_precision_at_k']} |",
              f"| Random (base rate) | {h['random_precision']} |", ""]
    t = e.get("truth_check")
    if t:
        L += ["## Across the whole pool, on the generator's truth (mock only)", "",
              f"The newest month's {t['n']} MQLs, worked or not, ranked by each ranker; the average hidden chance of a demo, if touched promptly, in each top third ({t['k']} leads). "
              "On leads the SDRs chose, HubSpot's score barely loses (above); across everything, it barely beats random.", "",
              "| Ranker | Demo chance in the top third |", "|---|---|",
              f"| AuxoCase live model | **{t['model']}** |", f"| HubSpot's current points | {t['hubspot_score']} |",
              f"| Random | {t['random']} |", f"| Best possible (the truth itself) | {t['best_possible']} |", ""]
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
    if s.get("n") or (s.get("baseline") or {}).get("n"):
        b = s.get("baseline") or {}
        L += ["## Speed to lead (lead created to first touch)", "", "| Leads | Touched | Median hours | Within 24 h |", "|---|---|---|---|"]
        if b.get("n"):
            L += [f"| Not routed by AuxoCase (history) | {b['n']} | {b['median_hours']} | {round(100 * b['within_24h'])}% |"]
        if s.get("n"):
            L += [f"| Routed by AuxoCase | {s['n']} | {s['median_hours']} | {round(100 * s['within_24h'])}% |"]
        L += [""]
    se = e.get("speed_effect") or {}
    if se.get("factor") is not None:
        L += [f"Measured from outcomes: leads first touched within a day booked at {se['rate_fast']}, later ones at {se['rate_slow']} "
              f"(factor {se['factor']}; {se['n_fast']} and {se['n_slow']} leads). Routing discounts a lead past its first day by "
              f"`stale_lead_factor` = {e.get('stale_lead_factor')}; keep the two close.", ""]
    if e.get("mql_gate"):
        L += ["## Where HubSpot's MQL gate drops leads (last three months)", "",
              "Only MQLs reach the pool this service ranks. Leads missing a title or a size get no points for them in HubSpot's model, so they pass the gate less often.", "",
              "| Channel | Leads | Reach MQL | No job title | No company size |", "|---|---|---|---|---|"]
        L += [f"| {g['channel']} | {g['leads']} | {round(100 * g['mql_share'])}% | {round(100 * g['no_title'])}% | {round(100 * g['no_size'])}% |" for g in e["mql_gate"]] + [""]
    en = e.get("enrichment") or {}
    if en.get("asked"):
        L += ["## Enrichment", "", f"Asked the enrichment tool about {en['asked']} pool leads missing a field; it filled at least one field for {en['filled'] or 0}. "
              f"Titles: {en['got_title'] or 0} of {en['asked_title'] or 0}. Sizes: {en['got_size'] or 0} of {en['asked_size'] or 0}.", ""]
    if e.get("losses"):
        bands = ["under_50", "50_149", "150_299", "300_500", "over_500", "unknown"]
        L += ["## Why deals were lost (free-text reasons read into categories, quote checked)", "",
              "| Reason | " + " | ".join(b.replace("_", " ") for b in bands) + " | Total |", "|---|" + "---|" * (len(bands) + 1)]
        for cat, row in sorted(e["losses"].items(), key=lambda kv: -sum(kv[1].values())):
            L += [f"| {cat.replace('_', ' ')} | " + " | ".join(str(row.get(b, 0)) for b in bands) + f" | {sum(row.values())} |"]
        L += [""]
    if e.get("monitor"):
        L += ["## Channel monitor (latest run)", "", "| Period | Channel | Worked | Demos | Observed | Predicted | Flag |", "|---|---|---|---|---|---|---|"]
        L += [f"| {p['period']} | {p['channel']} | {p['n']} | {p['demos']} | {_fmt(p['demo_rate'])} | {_fmt(p['predicted_rate'])} | {p['flag']} |" for p in e["monitor"]] + [""]
    text = "\n".join(L)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return text
