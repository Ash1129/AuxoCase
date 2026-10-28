"""The queue-limit experiment behind BrightPath answer 4: how freshness and small queues change speed and demos.

One base is built the way `run.py demo` builds it (history, first model approved by both teams, pool scored
and routed). Each routing setup is then replayed for one simulated month from that same base, once per seed,
and the table reports the mean and the range across seeds. One seed alone is too noisy to rank setups that are
a few dozen demos apart.

Every run uses the offline reader, so the numbers depend only on the code, the seeds and the setup.
"""
from __future__ import annotations

import os
import shutil
import statistics
import tempfile
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# (stale_lead_factor, capacity_limit). A factor of 1.0 means no freshness discount.
CONFIGS: list[tuple[float, int]] = [(1.0, 3), (0.5, 3), (0.5, 2), (0.5, 1)]
FIRST_SEED = 101


def build_demo_base(db_path: Path | str, months: int = 36, seed: int = 7, llm=None) -> dict:
    """What `run.py demo` does before it serves: history, a first model approved by both teams, the pool
    scored and routed. Returns what happened at each step; `model` is None when the history is too thin."""
    from ..feedback import loop as fb
    from ..hubspot.mock import MockHubSpot
    from ..llm.client import make_llm
    from ..routing.engine import Router
    from ..scoring.pipeline import Scorer, load_live_model
    from ..storage.db import connect
    from .generate import generate

    conn = connect(db_path)
    hs = MockHubSpot(conn)
    hs.ensure_properties()
    out: dict = {"history": generate(conn, months=months, seed=seed), "model": None}
    fb.sync_outcomes(hs, conn)
    try:
        model, m = fb.train_candidate(hs, conn, notes="first model from the last 12 months of history")
        fb.approve(conn, model.version, "marketing", "demo marketing lead", "approve", "demo")
        fb.approve(conn, model.version, "sales", "demo sales lead", "approve", "demo")
        out["model"] = {"version": model.version, "lift": m.lift_at_k, "k": m.k}
    except ValueError as e:
        out["model_error"] = str(e)
    out["scored"] = len(Scorer(hs, conn, load_live_model(conn), llm or make_llm()).rescore_pool())
    d = Router(hs, conn).run()
    out["routing"] = {k: sum(1 for x in d if x.decision == k) for k in {x.decision for x in d}}
    conn.close()
    return out


@dataclass(frozen=True)
class Job:
    base: str
    workdir: str
    factor: float
    limit: int
    seed: int
    days: int = 30
    new_leads: int = 6000


def run_one(job: Job) -> dict:
    """One routing setup for one simulated month, on its own copy of the base (and its own model folder)."""
    os.environ["AUXO_LLM"] = "offline"
    os.environ.pop("AUXO_MODEL_DIR", None)          # model pickles must sit beside each copy, not be shared
    from ..hubspot.mock import MockHubSpot
    from ..storage.db import connect, set_setting
    from .simulate import simulate_month

    base = Path(job.base)
    run_dir = Path(job.workdir) / f"f{job.factor}-l{job.limit}-s{job.seed}"
    run_dir.mkdir(parents=True, exist_ok=True)
    db = run_dir / "run.db"
    src = connect(base)
    src.execute(f"VACUUM INTO '{db}'")
    src.close()
    if (base.parent / "models").exists():
        shutil.copytree(base.parent / "models", run_dir / "models", dirs_exist_ok=True)

    conn = connect(db)
    hs = MockHubSpot(conn)
    set_setting(conn, "stale_lead_factor", str(job.factor))
    conn.execute("UPDATE hs_owners SET capacity_limit=?", (job.limit,))
    r = simulate_month(conn, hs, seed=job.seed, new_leads=job.new_leads, days=job.days)
    conn.close()
    return {"freshness": "off" if job.factor == 1.0 else "on", "stale_lead_factor": job.factor, "limit": job.limit,
            "seed": job.seed, "worked": r["worked"], "touched_within_24h": r["touched_within_24h"],
            "booked": r["booked"], "assigned": r["assigned"], "held": r["held"]}


def run_table(seeds: int = 5, processes: Optional[int] = None, configs: list[tuple[float, int]] = CONFIGS,
              history_months: int = 36, days: int = 30, new_leads: int = 6000,
              workdir: Optional[Path] = None) -> tuple[list[dict], list[dict]]:
    """Build the base once, replay every (setup, seed) pair, and return (per-run rows, per-setup summary)."""
    from ..llm.client import Offline

    os.environ["AUXO_LLM"] = "offline"
    os.environ.pop("AUXO_MODEL_DIR", None)          # the base's model must sit beside it, where run_one copies from
    tmp = None
    if workdir is None:
        tmp = tempfile.TemporaryDirectory(prefix="auxo-queue-limits-")
        workdir = Path(tmp.name)
    try:
        base = Path(workdir) / "base" / "base.db"
        base.parent.mkdir(parents=True, exist_ok=True)
        build_demo_base(base, months=history_months, llm=Offline())
        jobs = [Job(str(base), str(workdir), f, lim, FIRST_SEED + i, days, new_leads)
                for f, lim in configs for i in range(seeds)]
        processes = max(1, min(processes or os.cpu_count() or 1, len(jobs)))
        if processes == 1:
            runs = [run_one(j) for j in jobs]
        else:
            with ProcessPoolExecutor(max_workers=processes) as pool:
                runs = list(pool.map(run_one, jobs))
        return runs, summarise(runs)
    finally:
        if tmp is not None:
            tmp.cleanup()


def summarise(runs: list[dict]) -> list[dict]:
    """Mean and min-max per setup, in the order the setups first appear."""
    out: list[dict] = []
    for key in dict.fromkeys((r["stale_lead_factor"], r["limit"]) for r in runs):
        rs = [r for r in runs if (r["stale_lead_factor"], r["limit"]) == key]
        share = [r["touched_within_24h"] / r["worked"] if r["worked"] else 0.0 for r in rs]
        demos = [r["booked"] for r in rs]
        out.append({"freshness": rs[0]["freshness"], "stale_lead_factor": key[0], "limit": key[1],
                    "seeds": [r["seed"] for r in rs],
                    "within_24h_mean": statistics.mean(share), "within_24h_min": min(share), "within_24h_max": max(share),
                    "demos_mean": statistics.mean(demos), "demos_min": min(demos), "demos_max": max(demos)})
    return out


def markdown(summary: list[dict]) -> str:
    rows = ["| Freshness discount | Queue limit | Touched within 24 h | Demos |", "|---|---|---|---|"]
    for s in summary:
        rows.append(f"| {s['freshness']} | {s['limit']} | "
                    f"{100 * s['within_24h_mean']:.0f}% ({100 * s['within_24h_min']:.0f}–{100 * s['within_24h_max']:.0f}%) | "
                    f"{s['demos_mean']:.0f} ({s['demos_min']}–{s['demos_max']}) |")
    return "\n".join(rows)
