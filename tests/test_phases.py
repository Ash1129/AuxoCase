"""The Next phases page: both experiments' readiness read live, the gain estimate and the gate's loss from the stored
evaluation, the switches thrown from the page and recorded with a name, a refusal shown and nothing set."""
import json

import pytest
from fastapi.testclient import TestClient

from auxocase.data.generate import generate
from auxocase.feedback import loop as fb
from auxocase.hubspot.mock import MockHubSpot
from auxocase.llm.client import Offline
from auxocase.pool import ranks_below_gate
from auxocase.routing import tier2
from auxocase.scoring.pipeline import Scorer
from auxocase.service.app import create_app
from auxocase.service.views import leadership
from auxocase.storage.db import connect, get_setting

from test_leadership import REPORT


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    path = tmp_path_factory.mktemp("base") / "base.db"
    conn = connect(path)
    hs = MockHubSpot(conn, faithful_delivery=True)
    hs.ensure_properties()
    generate(conn, months=8, seed=5)
    fb.sync_outcomes(hs, conn)
    Scorer(hs, conn, None, Offline()).rescore_pool()
    conn.close()
    return path


@pytest.fixture
def world(base, tmp_path, monkeypatch):
    for var in ("HUBSPOT_PRIVATE_APP_TOKEN", "HUBSPOT_APP_SECRET", "AUXO_ALLOWED_HOSTS"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("AUXO_LLM", "offline")
    path = tmp_path / "w.db"
    src = connect(base)
    src.execute(f"VACUUM INTO '{path}'")
    src.close()
    monkeypatch.setattr(leadership, "REPORT", tmp_path / "missing-evaluation.json")
    return path, connect(path), TestClient(create_app(path, scheduler=False))


def test_the_page_reads_both_phases_live_and_says_what_the_estimate_needs(world):
    path, conn, client = world
    r = client.get("/phases")
    assert r.status_code == 200
    t = r.text
    assert "phase 2 off · phase 3 off" in t and "0 of 100" in t and "0 of 200" in t
    assert "no estimate of the gain yet" in t and "no matured outcome below the gate yet" in t and "no lift measured yet" in t
    assert "Run <code>python run.py eval</code>" in t and "No switch set from this page yet" in t
    assert "<script>" not in t and "onclick=" not in t
    assert 'href="/phases"' in client.get("/").text                                      # in the navigation


def test_the_estimate_and_the_gates_loss_come_from_the_stored_evaluation(world, tmp_path, monkeypatch):
    path, conn, client = world
    report = {**REPORT, "mql_gate": [{"channel": "referral", "leads": 1904, "mqls": 185, "mql_share": 0.097, "no_title": 0.492, "no_size": 0.484}]}
    p = tmp_path / "evaluation.json"
    p.write_text(json.dumps(report))
    monkeypatch.setattr(leadership, "REPORT", p)
    t = client.get("/phases").text
    assert "+44" in t and "+40 to +48 with the months resampled" in t and "About 321 of the 500" in t
    assert "referral" in t and "1,904" in t and "10%" in t and "49%" in t and "48%" in t
    assert str(p) in t


def test_switches_thrown_from_the_page_are_set_recorded_and_refused_when_not_ready(world):
    path, conn, client = world
    # Exploration below the gate: set, recorded with a name, and shown.
    r = client.post("/phases/gate", data={"who": "Dana from sales", "note": "see what is down there", "explore": "0.5"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/phases?phase=gate&set=1#ph-gate-log"
    assert get_setting(conn, "explore_below_gate_share") == "0.5"
    rows = conn.execute("SELECT * FROM phase_decisions").fetchall()
    assert len(rows) == 1 and (rows[0]["phase"], rows[0]["switch"], rows[0]["value"], rows[0]["who"]) == ("gate", "explore", "0.5", "Dana from sales")
    t = client.get("/phases").text
    assert "phase 2 exploring" in t and "Half of the exploration slice sent below the gate" in t and "Dana from sales" in t
    assert "see what is down there" in t and "Leave as is (50%)" in t
    # Ranking on is refused before the labels are in: a notice, nothing set, nothing recorded.
    r = client.post("/phases/gate", data={"who": "Dana from sales", "switch": "on"}, follow_redirects=False)
    assert r.status_code == 303 and "refused=" in r.headers["location"]
    assert not ranks_below_gate(conn) and conn.execute("SELECT COUNT(*) n FROM phase_decisions").fetchone()["n"] == 1
    t = client.get(r.headers["location"]).text
    assert "Not set." in t and "0 of 100 worked leads from below the gate have matured labels" in t
    assert t.index('id="ph-gate-log"') < t.index("Not set.") < t.index('id="ph-line"')    # under the phase's "Thrown so far"
    assert '<option value="on" selected>On</option>' in t and 'value="Dana from sales"' in t   # the choices kept in the form
    assert t.count(" selected") == 1 and 'name="force" value="1" checked' not in t
    # Forced on: recorded as such, and the waiting plain leads are marked for scoring.
    r = client.post("/phases/gate", data={"who": "Dana from sales", "switch": "on", "force": "1"}, follow_redirects=False)
    assert r.status_code == 303 and ranks_below_gate(conn)
    row = conn.execute("SELECT * FROM phase_decisions ORDER BY id DESC LIMIT 1").fetchone()
    assert (row["switch"], row["value"], row["forced"]) == ("on", "on", 1)
    t = client.get("/phases").text
    assert "phase 2 ranking" in t and "Ranking below the gate switched on, ahead of the outcomes" in t
    # Nothing changed: nothing recorded. A bad share: refused.
    r = client.post("/phases/gate", data={"who": "Dana from sales", "explore": "0.5", "switch": "on"}, follow_redirects=False)
    assert r.headers["location"] == "/phases?phase=gate&set=0#ph-gate-log" and "Nothing changed." in client.get(r.headers["location"]).text
    assert conn.execute("SELECT COUNT(*) n FROM phase_decisions").fetchone()["n"] == 2
    r = client.post("/phases/gate", data={"who": "Dana from sales", "explore": "1.5"}, follow_redirects=False)
    assert "refused=" in r.headers["location"] and get_setting(conn, "explore_below_gate_share") == "0.5"
    # Phase 3 the same way: the share set and shown, the band refused until the labels mature.
    r = client.post("/phases/line", data={"who": "Mo from marketing", "explore": "0.2"}, follow_redirects=False)
    assert r.status_code == 303 and tier2.explore_share(conn) == 0.2
    t = client.get("/phases").text
    assert "phase 3 exploring" in t and "A fifth of the leads below the line put in the sequence" in t and "Mo from marketing" in t
    r = client.post("/phases/line", data={"who": "Mo from marketing", "switch": "on"}, follow_redirects=False)
    assert "refused=" in r.headers["location"] and not tier2.band_on(conn)
    assert client.post("/phases/other", data={"who": "x"}).status_code == 404
    assert client.post("/phases/gate", data={"note": "no name"}).status_code == 422


def test_leadership_points_at_next_phases(world, tmp_path, monkeypatch):
    path, conn, client = world
    p = tmp_path / "evaluation.json"
    p.write_text(json.dumps(REPORT))
    monkeypatch.setattr(leadership, "REPORT", p)
    t = client.get("/leadership").text
    assert 'href="/phases#ph-gate"' in t and 'href="/phases#ph-line"' in t and "phase 3" in t
