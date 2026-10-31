"""Runs the service's jobs on timers inside the server process.

The jobs are the ones the pages and `run.py` already expose; the scheduler only
decides when to run them:

| job     | default   | what it does                                                      |
|---------|-----------|-------------------------------------------------------------------|
| score   | 5 min     | score leads that are new or changed, then refresh pool ranks      |
| rescore | 1 min     | after a model goes live, rescore the pool on it, best leads first |
| route   | 5 min     | fill free SDR slots, hold strong leads that waited, create tasks  |
| touches | 1 min     | first email or call logged in HubSpot -> lead worked, slot refilled; new replies read and acted on (off by default) |
| outreach| 1 min     | draft the first email for new assignments, enrol in the sequence  |
| followups| 5 min    | send the follow-up and the last email for the SDR, on their day (off by default) |
| remind  | 60 min    | reminder task for assigned leads untouched past the SLA           |
| sync    | 60 min    | pull meetings, deal stages and lead status back into outcomes     |
| monitor | 1 day     | per-channel check of observed against predicted demo rate         |
| train   | 30 days   | train a candidate model; it goes live only with both approvals   |

Rules:
- One thread runs the jobs one after another, so two runs never overlap. A job
  is due when its interval has passed since its last run; a turn missed while
  something slow ran is not made up later.
- Last-run times are kept in the database (wall-clock time), so a restart does
  not re-run monthly training. On the very first start only `score` and
  `route` run at once; the rest wait one interval.
- Every run writes one `run_log` row (`scheduled.<job>`): ok, skipped (the job
  had nothing it could do, e.g. too few labels to train) or failed. A failing
  job never stops the others.
- Scoring reads at most `AUXO_SCORE_BATCH` changed leads per run, and outreach
  drafts at most `AUXO_OUTREACH_BATCH`, so a burst of new leads cannot hold
  the loop for hours when Claude reads the notes or writes the emails.
- The scheduler can train a model but never approves one.

Configuration (environment, e.g. `.env`): `AUXO_SCHEDULER` on/off (on by
default under `run.py serve`, off for `create_app` in tests),
`AUXO_EVERY_<JOB>_MIN` to change an interval (0 turns that job off),
`AUXO_SCORE_BATCH`, `AUXO_OUTREACH_BATCH`, `AUXO_SEQUENCE_ID` (the HubSpot
sequence new assignments join), `AUXO_SCHEDULER_TICK_S` for how often it checks.

Timing uses wall-clock time, while the jobs use the service clock. On the demo
data that clock is frozen at the end of the generated history: leads that were
already a day old there get held, but nothing new ever ages into a hold or a
reminder. Against a real portal the service clock is wall time. The held queue
is capped as a whole (Router.hold_limit), so routing every few minutes while
the team is full cannot grow the manager's page without end.
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Optional

from ..feedback import loop as fb
from ..hubspot.client import HubSpotClient
from ..hubspot.mock import MockHubSpot
from ..llm.client import make_llm
from ..routing.engine import Router
from ..routing import followups, replies
from ..routing.outreach import DEFAULT_LIMIT as DEFAULT_OUTREACH_BATCH, prepare_outreach
from ..routing.touches import detect_first_touches
from ..routing.tier2 import detect_replies, run_tier2
from ..scoring.pipeline import Scorer, load_live_model
from ..storage.db import connect, dumps, get_setting, now_iso, set_setting

DEFAULT_SCORE_BATCH = 25
DEFAULT_TICK_S = 30
DEFAULT_RESCORE_BUDGET_S = 45       # rescoring stops after this long per run, so the other jobs keep their minute


class Skipped(Exception):
    """The job ran but had nothing it could do; logged as 'skipped', not 'failed'."""


@dataclass(frozen=True)
class Job:
    name: str
    every_min: int
    run_on_first_start: bool = False


DEFAULT_JOBS = (
    Job("score", 5, run_on_first_start=True),
    Job("rescore", 1, run_on_first_start=True),
    Job("route", 5, run_on_first_start=True),
    Job("touches", 1, run_on_first_start=True),
    Job("outreach", 1, run_on_first_start=True),
    Job("followups", 5),
    Job("remind", 60),
    Job("sync", 60),
    Job("monitor", 24 * 60),
    Job("train", 30 * 24 * 60),
)


def jobs_from_env(env: Optional[dict] = None) -> list[Job]:
    env = os.environ if env is None else env
    out = []
    for j in DEFAULT_JOBS:
        raw = env.get(f"AUXO_EVERY_{j.name.upper()}_MIN")
        out.append(Job(j.name, int(raw) if raw not in (None, "") else j.every_min, j.run_on_first_start))
    return out


def enabled(env: Optional[dict] = None, default: bool = False) -> bool:
    env = os.environ if env is None else env
    raw = (env.get("AUXO_SCHEDULER") or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "on", "true", "yes")


class Scheduler:
    def __init__(self, db_path, hs: Optional[HubSpotClient] = None, jobs: Optional[list[Job]] = None,
                 score_batch: Optional[int] = None, tick_s: Optional[float] = None):
        # Its own connection, so a scheduled write never lands inside a request's transaction. The mock lives in
        # that database too; the real client keeps webhook events in memory, so that one is shared with the server.
        self.conn = connect(db_path)
        self.hs: HubSpotClient = hs if hs is not None and not isinstance(hs, MockHubSpot) else MockHubSpot(self.conn)
        self.llm = make_llm(conn=self.conn)
        self.jobs = jobs if jobs is not None else jobs_from_env()
        self.score_batch = score_batch or int(os.environ.get("AUXO_SCORE_BATCH") or DEFAULT_SCORE_BATCH)
        self.outreach_batch = int(os.environ.get("AUXO_OUTREACH_BATCH") or DEFAULT_OUTREACH_BATCH)
        self.rescore_budget_s = float(os.environ.get("AUXO_RESCORE_BUDGET_S") or DEFAULT_RESCORE_BUDGET_S)
        self.tick_s = tick_s or float(os.environ.get("AUXO_SCHEDULER_TICK_S") or DEFAULT_TICK_S)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.actions: dict[str, Callable[[], dict]] = {
            "score": self._score, "rescore": self._rescore, "route": self._route, "touches": self._touches, "outreach": self._outreach, "followups": self._followups, "remind": self._remind,
            "sync": self._sync, "monitor": self._monitor, "train": self._train,
        }

    # ---- the jobs ------------------------------------------------------------------
    def _scorer(self) -> Scorer:
        return Scorer(self.hs, self.conn, load_live_model(self.conn), self.llm)

    def _score(self) -> dict:
        sc = self._scorer()
        scored = sc.process_events(max_events=self.score_batch)
        sc.refresh_ranks()
        return {"scored": len(scored), "model": sc.model_version, "reader": self.llm.name}

    def _rescore(self) -> dict:
        return self._scorer().rescore_stale(budget_s=self.rescore_budget_s)

    def _route(self) -> dict:
        d = Router(self.hs, self.conn).run()
        counts: dict[str, int] = {}
        for x in d:
            counts[x.decision] = counts.get(x.decision, 0) + 1
        t2 = run_tier2(self.hs, self.conn, self.llm, limit=self.outreach_batch)     # phase 3, a no-op while off
        if t2["emailed"] or t2["control"] or t2["expired"]:
            counts["automated"] = t2
        return counts

    def _touches(self) -> dict:
        out = detect_first_touches(self.hs, self.conn)
        handed = detect_replies(self.hs, self.conn)
        if handed["replied"]:
            out["replies"] = handed
        read = replies.triage_new(self.hs, self.conn, self.llm, limit=self.outreach_batch)   # a no-op while the switch is off
        if read.get("triaged"):
            out["triaged"] = read
        return out

    def _outreach(self) -> dict:
        return prepare_outreach(self.hs, self.conn, self.llm, limit=self.outreach_batch)

    def _followups(self) -> dict:
        out = followups.run(self.hs, self.conn, self.llm, limit=self.outreach_batch)
        if not out["auto"]:
            raise Skipped("follow-ups are the SDR's: the switch is off")
        return out

    def _remind(self) -> dict:
        return {"reminders": Router(self.hs, self.conn).re_alert_stale()}

    def _sync(self) -> dict:
        return fb.sync_outcomes(self.hs, self.conn)

    def _monitor(self) -> dict:
        pts = fb.channel_monitor(self.hs, self.conn)
        flags: dict[str, int] = {}
        for p in pts:
            flags[p.flag] = flags.get(p.flag, 0) + 1
        return flags

    def _train(self) -> dict:
        try:
            model, m = fb.train_candidate(self.hs, self.conn, notes="scheduled monthly retrain")
        except ValueError as e:          # too few mature labels yet
            raise Skipped(str(e)) from e
        return {"candidate": model.version, "precision_at_k": m.precision_at_k, "lift": m.lift_at_k}

    # ---- timing ----------------------------------------------------------------------
    def _last_key(self, job: Job) -> str:
        return f"scheduler.last_run.{job.name}"

    def due(self, now: datetime) -> list[Job]:
        out = []
        for job in self.jobs:
            if job.every_min <= 0:
                continue
            last = get_setting(self.conn, self._last_key(job))
            if last is None:
                if job.run_on_first_start:
                    out.append(job)
                else:            # first start: wait one interval rather than train or sync the moment the server boots
                    set_setting(self.conn, self._last_key(job), now.isoformat(timespec="seconds"))
                continue
            if now - datetime.fromisoformat(last) >= timedelta(minutes=job.every_min):
                out.append(job)
        return out

    def run_due(self, now: Optional[datetime] = None) -> list[tuple[str, str]]:
        """Run every job that is due, in order, and log each. Returns (job, status) pairs."""
        now = now or datetime.utcnow()
        results = []
        for job in self.due(now):
            if self._stop.is_set():
                break
            results.append((job.name, self.run_one(job, now)))
        return results

    def run_one(self, job: Job, now: Optional[datetime] = None) -> str:
        now = now or datetime.utcnow()
        started = now_iso()
        try:
            detail, status = dumps(self.actions[job.name]()), "ok"
        except Skipped as e:
            detail, status = str(e)[:500], "skipped"
        except Exception as e:  # one broken job must not stop the rest
            detail, status = f"{type(e).__name__}: {e}"[:500], "failed"
        set_setting(self.conn, self._last_key(job), now.isoformat(timespec="seconds"))
        self.conn.execute("INSERT INTO run_log(job, started_at, finished_at, status, detail) VALUES(?,?,?,?,?)",
                          (f"scheduled.{job.name}", started, now_iso(), status, detail))
        return status

    # ---- the background thread ---------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="auxocase-scheduler", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def _loop(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            self.run_due()
            self._stop.wait(max(1.0, self.tick_s - (time.monotonic() - started)))
