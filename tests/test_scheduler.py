"""The built-in scheduler: which jobs run when, what gets logged, and that the server starts and stops it."""
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from auxocase.data.generate import generate
from auxocase.hubspot.mock import MockHubSpot
from auxocase.service.app import create_app
from auxocase.service.scheduler import DEFAULT_JOBS, Job, Scheduler, enabled, jobs_from_env
from auxocase.storage.db import connect

T0 = datetime(2030, 1, 1, 9, 0)


@pytest.fixture(scope="module")
def base_db(tmp_path_factory):
    path = tmp_path_factory.mktemp("sched") / "base.db"
    conn = connect(path)
    MockHubSpot(conn).ensure_properties()
    generate(conn, months=6, seed=13)
    conn.close()
    return path


@pytest.fixture
def db(base_db, tmp_path, monkeypatch):
    monkeypatch.setenv("AUXO_LLM", "offline")
    monkeypatch.delenv("AUXO_SCHEDULER", raising=False)
    path = tmp_path / "s.db"
    conn = connect(base_db)
    conn.execute(f"VACUUM INTO '{path}'")
    conn.close()
    return path


def _log(conn):
    return [(r["job"], r["status"]) for r in conn.execute("SELECT job, status FROM run_log WHERE job LIKE 'scheduled.%' ORDER BY id")]


def test_first_start_runs_score_and_route_then_each_job_on_its_interval(db):
    s = Scheduler(db, jobs=list(DEFAULT_JOBS), score_batch=10)
    every5 = ["score", "rescore", "route", "touches", "outreach"]
    assert s.run_due(T0) == [(j, "ok") for j in every5]                    # the slow jobs wait one interval
    assert s.run_due(T0 + timedelta(seconds=30)) == []
    assert [j for j, _ in s.run_due(T0 + timedelta(minutes=1))] == ["rescore", "touches", "outreach"]
    assert [j for j, _ in s.run_due(T0 + timedelta(minutes=5))] == every5
    hour = s.run_due(T0 + timedelta(minutes=61))
    assert [j for j, _ in hour] == every5 + ["remind", "sync"] and all(st == "ok" for _, st in hour)
    assert [j for j, _ in s.run_due(T0 + timedelta(days=1, minutes=1))] == every5 + ["remind", "sync", "monitor"]
    assert _log(s.conn)[:2] == [("scheduled.score", "ok"), ("scheduled.rescore", "ok")]
    assert len(_log(s.conn)) == 5 + 3 + 5 + 7 + 8


def test_a_restart_does_not_rerun_jobs_that_ran_recently(db):
    Scheduler(db, jobs=list(DEFAULT_JOBS), score_batch=10).run_due(T0)
    again = Scheduler(db, jobs=list(DEFAULT_JOBS), score_batch=10)
    assert [j for j, _ in again.run_due(T0 + timedelta(minutes=2))] == ["rescore", "touches", "outreach"]   # only the one-minute jobs


def test_a_failing_job_is_logged_and_the_rest_still_run(db):
    s = Scheduler(db, jobs=[Job("route", 5, True), Job("score", 5, True)], score_batch=10)

    def broken():
        raise RuntimeError("HubSpot said 502")
    s.actions["route"] = broken
    assert s.run_due(T0) == [("route", "failed"), ("score", "ok")]
    row = s.conn.execute("SELECT detail FROM run_log WHERE job='scheduled.route'").fetchone()
    assert "502" in row["detail"]
    assert s.run_due(T0 + timedelta(minutes=1)) == []              # a failure still counts as this interval's run


def test_too_few_labels_to_train_is_skipped_not_failed(db):
    s = Scheduler(db, jobs=[Job("train", 60, True)])

    def too_few():
        from auxocase.service.scheduler import Skipped
        raise Skipped("need at least 60 demos")
    s.actions["train"] = too_few
    assert s.run_due(T0) == [("train", "skipped")]


def test_intervals_come_from_the_environment():
    jobs = {j.name: j.every_min for j in jobs_from_env({"AUXO_EVERY_SCORE_MIN": "2", "AUXO_EVERY_TRAIN_MIN": "0"})}
    assert jobs["score"] == 2 and jobs["train"] == 0 and jobs["sync"] == 60
    assert enabled({"AUXO_SCHEDULER": "on"}) and not enabled({"AUXO_SCHEDULER": "off"}, default=True)
    assert not enabled({}) and enabled({}, default=True)


def test_a_job_set_to_zero_never_runs(db):
    s = Scheduler(db, jobs=[Job("score", 0, True), Job("route", 5, True)], score_batch=10)
    assert s.run_due(T0) == [("route", "ok")]


def test_repeated_routing_passes_never_hold_more_than_the_cap(db):
    """Routing every few minutes while everyone is full must not grow the manager's queue past its limit."""
    from auxocase.llm.client import Offline
    from auxocase.routing.engine import Router
    from auxocase.scoring.pipeline import Scorer
    conn = connect(db)
    hs = MockHubSpot(conn)
    Scorer(hs, conn, None, Offline()).rescore_pool()     # routing only places scored leads
    conn.execute("UPDATE hs_owners SET capacity_limit=0")
    router = Router(hs, conn, hold_limit=5)
    held = lambda: conn.execute("SELECT COUNT(*) n FROM held_queue WHERE released_at IS NULL").fetchone()["n"]
    router.run()
    assert held() == 5
    router.run()
    router.run()
    assert held() == 5


def test_the_server_starts_and_stops_the_scheduler(db):
    off = create_app(db)
    assert off.state.scheduler is None
    assert 'data-scheduler="off"' in TestClient(off).get("/").text

    app = create_app(db, scheduler=True)
    with TestClient(app) as client:
        assert app.state.scheduler.running
        assert 'data-scheduler="on"' in client.get("/").text
    assert not app.state.scheduler.running
