"""AuxoCase command line.

  python run.py seed [--months 14]         fill the mock HubSpot with BrightPath-like history
  python run.py score [--all]              score new/changed leads from the change feed (or the whole pool)
  python run.py route                      assign within capacity, hold the rest, create tasks
  python run.py sync                       pull outcomes (meetings, deals, lead status) into the service
  python run.py monitor                    the per-channel p-chart
  python run.py train                      train a candidate on the last 12 months of mature labels
  python run.py approve VERSION TEAM NAME  record an approval (both teams -> live)
  python run.py simulate [--months 2]      replay months: score, route, SDRs act, outcomes, retrain
  python run.py eval                       the evaluation report (writes docs/evaluation.md)
  python run.py queue-limits [--seeds 5]   one simulated month per routing setup and seed: speed and demos
  python run.py serve [--port 8000]        the pages and the webhook endpoint
  python run.py demo                       seed + score + route + sync + train + approve + serve, in one go
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from auxocase.feedback import loop as fb                     # noqa: E402
from auxocase.hubspot.mock import MockHubSpot                # noqa: E402
from auxocase.llm.client import make_llm                     # noqa: E402
from auxocase.routing.engine import Router                   # noqa: E402
from auxocase.scoring.pipeline import Scorer, load_live_model  # noqa: E402
from auxocase.storage.db import DEFAULT_DB, connect, dumps   # noqa: E402


def _ctx(db):
    conn = connect(db)
    hs = MockHubSpot(conn)
    hs.ensure_properties()
    return conn, hs


def _remove_db(db):
    for ext in ("", "-wal", "-shm"):
        p = Path(str(db) + ext)
        if p.exists():
            p.unlink()


def cmd_seed(a):
    from auxocase.data.generate import generate
    if Path(a.db).exists() and not a.force:
        print(f"{a.db} exists; pass --force to regenerate")
        return
    _remove_db(a.db)
    conn, hs = _ctx(a.db)
    summary = generate(conn, months=a.months, seed=a.seed)
    print(json.dumps(summary, indent=1, default=str))


def cmd_score(a):
    conn, hs = _ctx(a.db)
    sc = Scorer(hs, conn, load_live_model(conn), make_llm())
    res = sc.rescore_pool() if a.all else sc.process_events(max_events=a.max_events)
    sc.refresh_ranks()
    flagged = sum(1 for r in res if r.review_flag)
    print(f"scored {len(res)} leads with {sc.model_version}; {flagged} flagged for a person; reader: {sc.llm.name}")


def cmd_route(a):
    conn, hs = _ctx(a.db)
    d = Router(hs, conn).run()
    counts = {}
    for x in d:
        counts[x.decision] = counts.get(x.decision, 0) + 1
    print(json.dumps(counts))


def cmd_sync(a):
    conn, hs = _ctx(a.db)
    print(json.dumps(fb.sync_outcomes(hs, conn)))


def cmd_monitor(a):
    conn, hs = _ctx(a.db)
    for p in fb.channel_monitor(hs, conn):
        print(f"{p.period} {p.channel:9s} n={p.n:4d} demos={p.demos:3d} observed={p.demo_rate if p.demo_rate is not None else float('nan'):.3f} predicted={p.predicted_rate if p.predicted_rate is not None else float('nan'):.3f} {p.flag}")


def cmd_train(a):
    conn, hs = _ctx(a.db)
    fb.sync_outcomes(hs, conn)
    model, m = fb.train_candidate(hs, conn, notes=a.notes, prefer=a.prefer)
    print(f"candidate {model.version} ({model.family}): precision@{m.k}={m.precision_at_k} vs HubSpot's points {m.hubspot_precision_at_k} (lift {m.lift_at_k}x; cold-start table {m.points_precision_at_k}), AUC-PR {m.auc_pr} (base {m.base_rate}), ECE {m.ece}")
    print("by channel:", json.dumps(m.by_channel))
    print("challenger:", json.dumps(m.challenger))


def cmd_approve(a):
    conn, hs = _ctx(a.db)
    res = fb.approve(conn, a.version, a.team, a.name, a.decision, a.note)
    print(json.dumps(res))
    if res["status"] == "live":
        print("rescoring the pool on the new model, best-ranked leads first...")
        print(json.dumps(Scorer(hs, conn, load_live_model(conn), make_llm()).rescore_stale()))


def cmd_simulate(a):
    from auxocase.data.simulate import simulate_month
    conn, hs = _ctx(a.db)
    for i in range(a.months):
        print(f"--- simulated month {i + 1} ---")
        print(json.dumps(simulate_month(conn, hs, seed=a.seed + i), default=str))


def cmd_losses(a):
    conn, hs = _ctx(a.db)
    print(json.dumps(fb.classify_losses(hs, conn, make_llm())))


def cmd_eval(a):
    from auxocase.evaluation import write_report
    conn, hs = _ctx(a.db)
    out = write_report(hs, conn, Path("docs/evaluation.md"))
    print(out)


def cmd_serve(a):
    import uvicorn
    os.environ["AUXO_DB"] = str(a.db)
    os.environ.setdefault("AUXO_SCHEDULER", "on")      # jobs run on timers unless .env says AUXO_SCHEDULER=off
    from auxocase.service.app import create_app
    uvicorn.run(create_app(a.db), host="127.0.0.1", port=a.port)


def cmd_queue_limits(a):
    from auxocase.data.queue_limits import markdown, run_table
    runs, summary = run_table(seeds=a.seeds, processes=a.processes)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"runs": runs, "summary": summary}, indent=1))
    print(markdown(summary))
    print(f"\nper-run results: {out}")


def cmd_demo(a):
    from auxocase.data.queue_limits import build_demo_base
    _remove_db(a.db)
    out = build_demo_base(a.db, months=36, seed=7)
    print(json.dumps(out["history"], indent=1, default=str))
    if out["model"]:
        print(f"live model {out['model']['version']}: lift {out['model']['lift']}x at K={out['model']['k']}")
    else:
        print("no model yet:", out.get("model_error"))
    print(f"scored {out['scored']} leads in the pool")
    print("routing:", out["routing"])
    conn, hs = _ctx(a.db)
    fb.channel_monitor(hs, conn)
    cmd_serve(a)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", default=str(DEFAULT_DB))
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("seed"); s.add_argument("--months", type=int, default=36); s.add_argument("--seed", type=int, default=7); s.add_argument("--force", action="store_true"); s.set_defaults(fn=cmd_seed)
    s = sub.add_parser("score"); s.add_argument("--all", action="store_true"); s.add_argument("--max-events", type=int, default=500); s.set_defaults(fn=cmd_score)
    s = sub.add_parser("route"); s.set_defaults(fn=cmd_route)
    s = sub.add_parser("sync"); s.set_defaults(fn=cmd_sync)
    s = sub.add_parser("monitor"); s.set_defaults(fn=cmd_monitor)
    s = sub.add_parser("train"); s.add_argument("--notes", default=""); s.add_argument("--prefer", choices=["lr", "hgb"], default=None); s.set_defaults(fn=cmd_train)
    s = sub.add_parser("approve"); s.add_argument("version"); s.add_argument("team"); s.add_argument("name"); s.add_argument("--decision", default="approve"); s.add_argument("--note", default=""); s.set_defaults(fn=cmd_approve)
    s = sub.add_parser("simulate"); s.add_argument("--months", type=int, default=1); s.add_argument("--seed", type=int, default=101); s.set_defaults(fn=cmd_simulate)
    s = sub.add_parser("losses"); s.set_defaults(fn=cmd_losses)
    s = sub.add_parser("eval"); s.set_defaults(fn=cmd_eval)
    s = sub.add_parser("queue-limits"); s.add_argument("--seeds", type=int, default=5); s.add_argument("--processes", type=int, default=None)
    s.add_argument("--out", default="outputs/queue_limits.json"); s.set_defaults(fn=cmd_queue_limits)
    s = sub.add_parser("serve"); s.add_argument("--port", type=int, default=8000); s.set_defaults(fn=cmd_serve)
    s = sub.add_parser("demo"); s.add_argument("--port", type=int, default=8000); s.set_defaults(fn=cmd_demo)
    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
