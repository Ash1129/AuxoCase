"""The evaluation report: the numbers both teams look at, written to docs/evaluation.md.

What it reports and why (L03, L04, L05, L12, L13):
- precision and lift at the capacity cut-off for the live model against the
  points model and HubSpot's own score, on its test months only (never
  trained or calibrated on), computed on worked leads only and said to be so;
- on the mock, AUXOAI's capacity head to head: for each held-out month, the
  demos each ranker's 500 would book on the generator's truth, worked or
  not, same timing for all, with a range from resampling months, and the
  same for ranking every lead instead of the MQLs only;
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
import pandas as pd

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

    # HubSpot's points vs ours on the same labelled rows (worked leads only), in the live model's test months: the
    # months it never trained on or calibrated on. Without them (a model from before the three-way split) there is
    # no fair comparison on worked leads, and the section is left out rather than scored in-sample.
    table = fb.build_training_table(hs, conn, window_months=14)
    test_months = (out["metrics"] or {}).get("test_months") or []
    in_test = pd.to_datetime(table.created).dt.strftime("%Y-%m").isin(test_months).to_numpy()
    m = table.label_state.isin(["booked", "worked_no_demo"]).to_numpy() & in_test
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
        k = int(round(len(y) / 3))       # capacity is a third of the MQL pool, applied to the labelled rows (as in training)
        out["head_to_head"] = {
            "k": k, "months": test_months,
            "model_precision_at_k": round(precision_at_k(y, np.asarray(p), k), 4),
            "points_table_precision_at_k": round(precision_at_k(y, table.points[m].to_numpy().astype(float), k), 4),
            "hubspot_score_precision_at_k": round(precision_at_k(y, hub, k), 4),
            "random_precision": round(float(y.mean()), 4),
        }
        ch = table.X.loc[m, "channel"].to_numpy()
        out["by_channel_all_history"] = {c: {"n": int((ch == c).sum()), "observed": round(float(y[ch == c].mean()), 3), "predicted": round(float(np.asarray(p)[ch == c].mean()), 3)} for c in sorted(set(ch))}

    # Exploration slice vs ranked slice.
    # Below-gate leads are a different population (they book at a different rate), so they get rows of their own:
    # mixed in, they would move the exploration row and the apparent lift of ranking without the ranking changing.
    r = conn.execute("SELECT r.decision, COALESCE(r.below_gate, 0) bg, COUNT(*) n, SUM(o.worked) worked, SUM(o.demo_booked) booked "
                     "FROM routing_log r LEFT JOIN outcomes o ON o.contact_id=r.contact_id "
                     "WHERE r.decision IN ('assigned','holdout_assigned') GROUP BY r.decision, bg ORDER BY bg, r.decision").fetchall()
    out["slices"] = {row["decision"] + (" (below HubSpot's gate)" if row["bg"] else ""): {"assigned": row["n"], "worked": row["worked"] or 0,
                                                                                        "booked": row["booked"] or 0} for row in r}

    ov = conn.execute("SELECT action, COUNT(*) n, SUM(COALESCE(o.demo_booked,0)) booked FROM overrides v LEFT JOIN outcomes o ON o.contact_id=v.contact_id GROUP BY action").fetchall()
    out["overrides"] = {row["action"]: {"n": row["n"], "booked": row["booked"] or 0} for row in ov}
    if live:
        from .scoring.model import DemoModel as _DM
        out["capacity_head_to_head"] = capacity_head_to_head(hs, conn, _DM.load(Path(live["artifact_path"])))
    out["speed_to_lead"] = fb.speed_to_lead(conn)
    out["success_by_month"] = fb.success_by_month(conn)
    out["speed_effect"] = fb.speed_effect(conn)
    from .storage.db import get_setting
    out["stale_lead_factor"] = get_setting(conn, "stale_lead_factor") or "1.0"
    out["mql_gate"] = fb.mql_gate_report(hs, conn)
    out["below_gate"] = fb.below_gate_readiness(conn)
    out["losses"] = fb.loss_report(hs, conn)
    out["enrichment"] = [dict(r) for r in conn.execute(
        "SELECT COUNT(*) asked, SUM(filled <> '{}') filled, SUM(asked LIKE '%job_title%') asked_title, "
        "SUM(filled LIKE '%job_title%') got_title, SUM(asked LIKE '%num_employees%') asked_size, "
        "SUM(filled LIKE '%num_employees%') got_size FROM enrichment_log").fetchall()][0]
    out["monitor"] = [dict(x) for x in conn.execute("SELECT * FROM channel_monitor WHERE created_at=(SELECT MAX(created_at) FROM channel_monitor) ORDER BY period, channel").fetchall()]
    return out


def capacity_head_to_head(hs: HubSpotClient, conn: sqlite3.Connection, model, capacity: int = 500, max_months: int = 6,
                          boot: int = 2000, seed: int = 17) -> Optional[dict]:
    """Mock only. For each month the live model never trained on (its test months and every month since), the
    `capacity` leads each ranker would put in front of the SDRs and the demos they would book if worked promptly:
    the sum of the generator's hidden demo chances, worked or not. Timing is the same for every ranker, so this
    is the ranking alone, not speed. Two pools: the MQLs HubSpot's gate lets through (what the service ranks
    today) and every lead that arrived (what ranking below the gate would rank). Months are resampled for a
    range. Real data cannot do this: unworked leads have no outcome, which is what the exploration slice is for."""
    if not conn.execute("SELECT 1 FROM sim_truth LIMIT 1").fetchone():
        return None
    metrics = model.metrics
    test_months = getattr(metrics, "test_months", None)
    if not test_months or not getattr(metrics, "held_out", False):
        return None
    import random
    from .data.generate import true_demo_probability, truth_for
    from .scoring.features import feature_row
    from .scoring.model import score_frame
    from .pool import passed_gate
    from .scoring.points import points_score

    start, now = datetime.strptime(min(test_months), "%Y-%m"), clock(conn)
    by_month: dict[str, list] = {}
    for c in hs.list_contacts(created_since=start):
        if c.createdate < now:
            by_month.setdefault(c.createdate.strftime("%Y-%m"), []).append(c)
    sizes = sorted(len(v) for v in by_month.values())
    if not sizes:
        return None
    whole = [m for m in sorted(by_month) if len(by_month[m]) >= 0.6 * sizes[len(sizes) // 2]][-max_months:]
    companies: dict = {}
    rows, below_gate_channels = [], {}
    for month in whole:
        leads = sorted(by_month[month], key=lambda c: c.id)
        for c in leads:
            if c.company_id and c.company_id not in companies:
                companies[c.company_id] = hs.get_company(c.company_id)
        co = [companies.get(c.company_id) for c in leads]
        p_model, _ = score_frame(model, [feature_row(c, k) for c, k in zip(leads, co)])
        p_model = np.asarray(p_model)
        truth = np.array([true_demo_probability(c, k, random.Random(0), truth_for(conn, c.id)) for c, k in zip(leads, co)])
        hub = np.array([c.hubspot_score or 0 for c in leads], dtype=float)
        pts = np.array([points_score(c, k) for c, k in zip(leads, co)], dtype=float)
        mql = np.array([passed_gate(c) for c in leads])

        def demos(score: np.ndarray, pool: np.ndarray) -> float:
            idx = np.flatnonzero(pool)
            top = idx[np.argsort(-score[idx], kind="stable")[:capacity]]
            return float(truth[top].sum())

        everyone = np.ones(len(leads), bool)
        k_mql = min(capacity, int(mql.sum()))
        idx = np.flatnonzero(everyone)
        top_all = idx[np.argsort(-p_model, kind="stable")[:capacity]]
        for i in top_all:
            if not mql[i]:
                below_gate_channels[leads[i].channel] = below_gate_channels.get(leads[i].channel, 0) + 1
        rows.append({
            "month": month, "leads": len(leads), "mqls": int(mql.sum()),
            "hubspot": demos(hub, mql), "points": demos(pts, mql), "model": demos(p_model, mql),
            "random": float(truth[mql].mean() * k_mql), "best_mql": demos(truth, mql),
            "model_all": demos(p_model, everyone), "best_all": demos(truth, everyone),
            "below_gate_picked": int((~mql[top_all]).sum()),
        })
    if not rows:
        return None
    arms = ("hubspot", "points", "model", "random", "best_mql", "model_all", "best_all")
    mean = {a: round(float(np.mean([r[a] for r in rows])), 1) for a in arms}
    rng = np.random.default_rng(seed)

    def ranged(a: str, b: str) -> dict:
        d = np.array([r[a] - r[b] for r in rows])
        sims = d[rng.integers(0, len(d), size=(boot, len(d)))].mean(axis=1)
        return {"mean": round(float(d.mean()), 1), "lo": round(float(np.percentile(sims, 2.5)), 1),
                "hi": round(float(np.percentile(sims, 97.5)), 1)}

    return {"months": [r["month"] for r in rows], "capacity": capacity, "per_month": rows, "mean": mean,
            "model_vs_hubspot": ranged("model", "hubspot"), "model_vs_points": ranged("model", "points"),
            "all_vs_mql": ranged("model_all", "model"),
            "below_gate_picked": round(float(np.mean([r["below_gate_picked"] for r in rows])), 0),
            "below_gate_by_channel": {k: round(v / len(rows)) for k, v in sorted(below_gate_channels.items(), key=lambda kv: -kv[1])}}


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
        L += ["## Head to head on worked leads, in the test months", "",
              f"The live model's test months ({', '.join(h['months'])}): it never trained or calibrated on them. "
              f"K = {h['k']}, a third of the {e['labelled_rows']} worked leads there ({e['positives']} demos), as capacity is a third of the MQLs.", "",
              "| Ranker | Precision@K |", "|---|---|",
              f"| AuxoCase live model | **{h['model_precision_at_k']}** |",
              f"| Points table (channel-aware, cold start) | {h['points_table_precision_at_k']} |",
              f"| HubSpot's current points (title, size, pages) | {h['hubspot_score_precision_at_k']} |",
              f"| Random (base rate) | {h['random_precision']} |", ""]
    t = e.get("capacity_head_to_head")
    if t:
        mn, cap = t["mean"], t["capacity"]
        mv, ap = t["model_vs_hubspot"], t["all_vs_mql"]
        L += ["## Capacity head to head on held-out months, on the generator's truth (mock only)", "",
              f"For each of {len(t['months'])} months the live model never trained on ({', '.join(t['months'])}), the {cap} leads each ranker would hand the SDRs, "
              f"and the demos they would book if worked promptly: the sum of the generator's hidden demo chances, worked or not. Every ranker gets the same timing, "
              "so this is the ranking alone; faster follow-up comes on top. Ranges resample the months (2.5th to 97.5th percentile) and say how steady the gap is "
              "on this generator, not how sure a real portal would be.", "",
              "| Who gets the 500 | Demos a month |", "|---|---|",
              f"| HubSpot's points, top of the MQLs | {mn['hubspot']} |",
              f"| Points table, top of the MQLs | {mn['points']} |",
              f"| AuxoCase model, top of the MQLs (today) | **{mn['model']}** |",
              f"| Random MQLs | {mn['random']} |",
              f"| Best possible among the MQLs | {mn['best_mql']} |",
              f"| AuxoCase model, top of every lead (below the gate too) | **{mn['model_all']}** |",
              f"| Best possible among every lead | {mn['best_all']} |", "",
              f"The model over HubSpot's points: {mv['mean']:+} demos a month ({mv['lo']:+} to {mv['hi']:+}). "
              f"Ranking every lead over ranking the MQLs only: {ap['mean']:+} ({ap['lo']:+} to {ap['hi']:+}); "
              f"about {t['below_gate_picked']:.0f} of the {cap} would be leads HubSpot's gate kept out"
              + (" (" + ", ".join(f"{c} {n}" for c, n in t["below_gate_by_channel"].items()) + ")" if t["below_gate_by_channel"] else "") + ". "
              "The model learned from worked MQLs only, so how it ranks below the gate is a guess the exploration slice must check before it is used.", "",
              "| Month | HubSpot | Model (MQLs) | Model (every lead) | Best (every lead) |", "|---|---|---|---|---|"]
        L += [f"| {r['month']} | {r['hubspot']:.0f} | {r['model']:.0f} | {r['model_all']:.0f} | {r['best_all']:.0f} |" for r in t["per_month"]] + [""]
    if e.get("metrics"):
        m = e["metrics"]
        months = m.get("test_months") or m.get("validation_months") or []
        split = (f"Trained on {m['train_months'][0]} to {m['train_months'][-1]} ({m.get('train_rows', '?')} worked leads), family chosen and calibrated on "
                 f"{', '.join(m['calibration_months'])} ({m.get('calibration_rows', '?')}), tested once on {', '.join(months)} ({m.get('test_rows', '?')})."
                 if m.get("train_months") else f"Months {', '.join(months)} (a model from before the three-way split: it was refitted on them, so these are not held out).")
        L += ["## The live model's test months", "", split + f" Precision@{m['k']} {m['precision_at_k']} vs HubSpot's points {m['hubspot_precision_at_k']} "
              f"(lift {m['lift_at_k']}x) and the service's cold-start table {m['points_precision_at_k']} (lift {m['lift_over_points']}x); AUC-PR {m['auc_pr']} "
              f"against a base rate of {m['base_rate']}; AUC-ROC {m['auc_roc']}; Brier {m['brier']}; ECE {m['ece']}. Challenger: {m['challenger']}.", "",
              "| Predicted | n | Observed |", "|---|---|---|"] + [f"| {b['lo']}–{b['hi']} | {b['n']} | {b['observed']} |" for b in m["calibration_bins"]] + [""]
    if e.get("by_channel_all_history"):
        L += ["## By channel (test months, worked leads)", "", "| Channel | n | Observed demo rate | Predicted |", "|---|---|---|---|"]
        L += [f"| {c} | {v['n']} | {v['observed']} | {v['predicted']} |" for c, v in e["by_channel_all_history"].items()] + [""]
    bg = e.get("below_gate")
    if bg and (bg["assigned"] or bg["explore_below_gate_share"] or bg["ranks_below_gate"]):
        L += ["## Below HubSpot's MQL gate: what the exploration slice has learned", "",
              f"Exploration share drawn below the gate: {bg['explore_below_gate_share']:.0%}; ranking below the gate: {'on' if bg['ranks_below_gate'] else 'off'}. "
              f"{bg['assigned']} plain leads handed out at random, {bg['worked']} worked, {bg['matured']} with a matured label ({bg['booked']} booked); "
              f"{bg['assigned_by_rank']} more handed out by rank. "
              + (f"The model's score at hand-out averaged {bg['predicted_rate']} for the matured ones; {bg['observed_rate']} booked. " if bg["matured"] else "")
              + ("Enough labels to judge ranking below the gate." if bg["ready"] else f"Ranking below the gate waits for {bg['needed']} matured labels."), ""]
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
    # The same figures as data, beside the report: what the Leadership page reads, so the page and the report agree.
    import json
    path.with_suffix(".json").write_text(json.dumps(e, default=str, indent=1))
    return text
