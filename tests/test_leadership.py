"""The leadership page reads its figures from the stored evaluation and the queue-limit experiment, does the
per-1,000 arithmetic itself, and says what to run when either file is missing."""
import json

import pytest
from fastapi.testclient import TestClient

from auxocase.hubspot.mock import MockHubSpot
from auxocase.hubspot.models import Owner
from auxocase.service.app import create_app
from auxocase.service.views import leadership
from auxocase.storage.db import connect, set_setting


def _month(m, worked, demos, routed, hours=70.0, within=0.2):
    return {"month": m, "worked": worked, "demos": demos, "routed_share": routed, "median_hours": hours, "within_24h": within}


HISTORY = [f"{y}-{mo:02d}" for y, mo in [(2027, m) for m in range(7, 13)] + [(2028, m) for m in range(1, 7)]]
REPORT = {
    "generated_at": "2026-10-29T03:40:26", "live": "demo-v7",
    # a partial month either side of the history, twelve whole history months at 150 demos, three routed at 240
    "success_by_month": [_month("2027-06", 10, 3, 0.0)] + [_month(m, 500, 150, 0.0) for m in HISTORY]
                        + [_month("2028-07", 40, 9, 0.0)] + [_month(m, 480, 240, 1.0, 12.0, 0.7) for m in ("2028-08", "2028-09", "2028-10")],
    "speed_to_lead": {"n": 1234, "median_hours": 12.4, "within_24h": 0.7, "baseline": {"n": 5555, "median_hours": 80.2, "within_24h": 0.15}},
    "slices": {"assigned": {"assigned": 1300, "worked": 1200, "booked": 600}, "holdout_assigned": {"assigned": 100, "worked": 100, "booked": 10}},
    "capacity_head_to_head": {
        "months": ["2028-05", "2028-06", "2028-07", "2028-08", "2028-09", "2028-10"], "capacity": 500, "per_month": [],
        "mean": {"hubspot": 280.0, "points": 330.0, "model": 333.3, "random": 250.0, "best_mql": 360.0, "model_all": 377.0, "best_all": 420.0},
        "model_vs_hubspot": {"mean": 53.3, "lo": 50.1, "hi": 56.6}, "model_vs_points": {"mean": 3.3, "lo": 1.0, "hi": 5.0},
        "all_vs_mql": {"mean": 43.7, "lo": 40.2, "hi": 47.9}, "below_gate_picked": 321.0, "below_gate_by_channel": {"referral": 150, "webinar": 140}},
    "head_to_head": {"k": 321, "months": ["2028-05", "2028-06"], "model_precision_at_k": 0.4511, "points_table_precision_at_k": 0.4402,
                     "hubspot_score_precision_at_k": 0.37, "random_precision": 0.35},
    "metrics": {"ece": 0.0151},
    "speed_effect": {"n_fast": 2000, "n_slow": 5000, "rate_fast": 0.6, "rate_slow": 0.3, "factor": 0.49},
    "below_gate": {"explore_below_gate_share": 0.0, "ranks_below_gate": False, "assigned": 0, "worked": 0, "matured": 0, "booked": 0,
                   "observed_rate": None, "predicted_rate": None, "assigned_by_rank": 0, "needed": 100, "ready": False},
}
LIMITS = {"runs": [], "summary": [
    {"freshness": "off", "stale_lead_factor": 1.0, "limit": 3, "seeds": [1, 2, 3], "within_24h_mean": 0.14, "within_24h_min": 0.12,
     "within_24h_max": 0.16, "demos_mean": 211.6, "demos_min": 201, "demos_max": 220},
    {"freshness": "on", "stale_lead_factor": 0.5, "limit": 1, "seeds": [1, 2, 3], "within_24h_mean": 0.61, "within_24h_min": 0.58,
     "within_24h_max": 0.66, "demos_mean": 266.2, "demos_min": 259, "demos_max": 271}]}


@pytest.fixture
def client(tmp_path, monkeypatch):
    for var in ("HUBSPOT_PRIVATE_APP_TOKEN", "HUBSPOT_APP_SECRET", "AUXO_ALLOWED_HOSTS"):
        monkeypatch.delenv(var, raising=False)
    path = tmp_path / "s.db"
    conn = connect(path)
    hs = MockHubSpot(conn)
    hs.ensure_properties()
    for i in range(3):
        hs.add_owner(Owner(id=f"90{i}", name=f"SDR {i}", email=f"sdr{i}@brightpath.example", capacity_limit=1))
    set_setting(conn, "stale_lead_factor", "0.5")
    monkeypatch.setattr(leadership, "REPORT", tmp_path / "missing-evaluation.json")
    monkeypatch.setattr(leadership, "QUEUE_LIMITS", tmp_path / "missing-queue_limits.json")
    return TestClient(create_app(path, scheduler=False))


def _write(tmp_path, monkeypatch, report=True, limits=True):
    if report:
        p = tmp_path / "evaluation.json"
        p.write_text(json.dumps(REPORT))
        monkeypatch.setattr(leadership, "REPORT", p)
    if limits:
        q = tmp_path / "queue_limits.json"
        q.write_text(json.dumps(LIMITS))
        monkeypatch.setattr(leadership, "QUEUE_LIMITS", q)


def test_headline_numbers_come_from_the_stored_files(client, tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch)
    r = client.get("/leadership")
    assert r.status_code == 200
    t = r.text
    # per 1,000 over the brief's 6,000 leads a month: 1,800 demos / 72,000 leads before, 720 / 18,000 after
    assert "25.0" in t and "40.0" in t and "+60%" in t
    assert "1,800 demos from 72,000 new leads over the twelve history months" in t and "720 from 18,000 over the three routed months" in t
    assert "brief's 6,000 new leads a month" in t
    assert "150 → 240" in t and "500 and 480 leads worked a month" in t and "90 more demos a month" in t
    assert "80 h → 12 h" in t and "15% → 70%" in t and "5,555" in t and "1,234" in t
    # the partial months are named, not counted
    assert "Jun 2027 and Jul 2028 are left out" in t
    # the ranking, the exploration slice and the gate, from the same file
    assert "would book 333" in t and "against 280" in t and "+50 to +57 with the months resampled" in t
    assert "7% of slots go to a lead picked at random" in t and "about 50, not 10" in t and "roughly 13 a month" in t
    assert "+44 a month" in t and "About 321 of the 500" in t
    assert "the model is ahead of the cold-start table: 0.451 against 0.440 at K = 321" in t
    # the queue-limit experiment, with the setup in use marked
    assert "212" in t and "201–220" in t and "266" in t and "the setup in use now" in t and "--seeds 3" in t
    # sources named, figures daggered on the mock, no empty states
    assert str(leadership.REPORT) in t and str(leadership.QUEUE_LIMITS) in t
    assert "†" in t and "Simulated · synthetic data" in t
    assert "Run <code>python run.py eval</code>" not in t and "Run <code>python run.py queue-limits</code>" not in t
    assert "<script>" not in t and "onclick=" not in t


def test_each_panel_says_what_to_run_when_its_file_is_missing(client, tmp_path, monkeypatch):
    r = client.get("/leadership")
    assert r.status_code == 200
    assert "Run <code>python run.py eval</code>" in r.text and "Run <code>python run.py queue-limits</code>" in r.text
    assert "25.0" not in r.text and "Simulated · synthetic data" not in r.text

    _write(tmp_path, monkeypatch, report=True, limits=False)            # one file without the other
    r = client.get("/leadership")
    assert r.status_code == 200 and "25.0" in r.text
    assert "Run <code>python run.py queue-limits</code>" in r.text and "Run <code>python run.py eval</code>" not in r.text
