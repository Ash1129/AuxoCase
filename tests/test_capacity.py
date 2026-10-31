"""Capacity from SDR minutes: the defaults reproduce the brief's 500, a quicker touch is more leads worked, the inputs
are set from the Leadership page and refused out of range, and the capacity line and Overview follow."""
import pytest
from fastapi.testclient import TestClient

from auxocase import capacity
from auxocase.data.generate import generate
from auxocase.hubspot.mock import MockHubSpot
from auxocase.llm.client import Offline
from auxocase.scoring.pipeline import Scorer
from auxocase.service.app import create_app
from auxocase.storage.db import connect, get_setting, set_setting


@pytest.fixture
def world(tmp_path, monkeypatch):
    for var in ("HUBSPOT_PRIVATE_APP_TOKEN", "HUBSPOT_APP_SECRET", "AUXO_ALLOWED_HOSTS"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("AUXO_LLM", "offline")
    path = tmp_path / "c.db"
    conn = connect(path)
    MockHubSpot(conn, faithful_delivery=True).ensure_properties()
    generate(conn, months=3, seed=5)
    return conn, TestClient(create_app(path, scheduler=False))


def test_the_defaults_give_the_briefs_500_and_the_minutes_are_the_lever(world):
    conn, _ = world
    assert round(capacity.touches_per_month(conn, 12)) == 500 and round(capacity.touches_per_month(conn)) == 500
    b = capacity.breakdown(conn, 12)
    assert (b["per_month"], b["minutes_touch"], b["minutes_reply"], b["per_sdr"], b["override"]) == (500, 20.0, 10.0, 41.7, None)
    assert capacity.set_assumptions(conn, minutes_touch=12) == ["sdr_minutes_touch"]
    assert round(capacity.touches_per_month(conn, 12)) == 682                      # the same hours, quicker touches: 22 minutes a lead
    assert capacity.set_assumptions(conn, minutes_reply=8) == ["sdr_minutes_reply"] and round(capacity.touches_per_month(conn, 12)) == 682
    assert capacity.set_assumptions(conn, minutes_touch=12, minutes_reply=8, hours=20.8) == []  # nothing changed: 20.8 is the page's 20.833
    with pytest.raises(ValueError, match="between 1 and 240"):
        capacity.set_assumptions(conn, minutes_touch=0, hours=1000)
    assert get_setting(conn, "sdr_minutes_touch") == "12" and get_setting(conn, "sdr_touch_hours_month") is None   # nothing set
    set_setting(conn, "team_capacity_per_month", "600")
    assert capacity.touches_per_month(conn, 12) == 600.0 and capacity.breakdown(conn, 12)["override"] == 600.0
    capacity.set_assumptions(conn, hours=20)
    assert get_setting(conn, "team_capacity_per_month") is None and round(capacity.touches_per_month(conn, 12)) == 655


def test_the_leadership_page_sets_the_capacity_and_the_line_follows(world):
    conn, client = world
    Scorer(MockHubSpot(conn, faithful_delivery=True), conn, None, Offline()).rescore_pool()   # the Overview draws its line from a scored pool
    t = client.get("/leadership").text
    assert 'id="ld-capacity"' in t and "12 SDRs × 20.8 h ÷ 30 min" in t and 'value="20"' in t and "At 15 minutes a cold touch, 600" in t
    assert "500 leads a month" in client.get("/").text
    r = client.post("/leadership/capacity", data={"minutes_touch": "10", "minutes_reply": "", "hours": ""}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/leadership?set=1#ld-capacity"
    t = client.get(r.headers["location"]).text
    assert "<b>Set.</b>" in t and "12 SDRs × 20.8 h ÷ 20 min" in t and ">750<" in t and 'value="10"' in t
    assert "750 leads a month" in client.get("/").text                            # the Overview's line reads it
    r = client.post("/leadership/capacity", data={"minutes_touch": "0"}, follow_redirects=False)
    assert "refused=" in r.headers["location"] and "Not set." in client.get(r.headers["location"]).text
    assert get_setting(conn, "sdr_minutes_touch") == "10"
    r = client.post("/leadership/capacity", data={"minutes_touch": "10"}, follow_redirects=False)
    assert r.headers["location"] == "/leadership?set=0#ld-capacity" and "Nothing changed." in client.get(r.headers["location"]).text
