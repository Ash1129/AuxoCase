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


def _touch(conn, contact_id, owner_id, at):
    conn.execute("INSERT INTO touches(contact_id, owner_id, engagement_id, type, touched_at, status_set, detected_at) VALUES(?,?,?,?,?,?,?)",
                 (contact_id, owner_id, f"e-{contact_id}", "EMAIL", at, "ATTEMPTED_TO_CONTACT", at))


def test_the_minutes_and_hours_are_read_from_the_sdrs_first_touches_once_there_are_enough(world):
    conn, client = world
    assert capacity.measured(conn)["touches"] == 0 and capacity.source(conn, "sdr_minutes_touch") == "assumed"
    assert "Read from the SDRs' own work, once there is some." in client.get("/leadership").text
    # One SDR, a morning of four touches 15 minutes apart: measured so far, the assumption stays.
    for i, hm in enumerate(("10:00", "10:15", "10:30", "10:45")):
        _touch(conn, f"c-0-{i}", "900", f"2026-09-01T{hm}:00")
    conn.commit()
    m = capacity.measured(conn)
    assert (m["touches"], m["sdrs"], m["days"], m["gaps"], m["minutes_touch"], m["minutes_in_force"], m["hours"]) == (4, 1, 1, 3, 15.0, False, None)
    assert capacity.unit(conn, "sdr_minutes_touch") == 20.0 and round(capacity.touches_per_month(conn, 12)) == 500
    t = client.get("/leadership").text
    assert "Read from the SDRs' own work so far:</b> 4 first touches by 1 SDR on 1 day since 2026-08-02, 15 minutes between one first touch and the next (the middle of 3 gaps)." in t
    assert "The 20 minutes a cold touch stays assumed until 2 SDRs have 30 gaps between them." in t and "(measured)" not in t
    # Three SDRs, twelve days each of the same morning: enough gaps and enough days, so both figures take over.
    for d in range(1, 13):
        for o in ("900", "901", "902"):
            for i, hm in enumerate(("10:00", "10:15", "10:30", "10:45")):
                if not (d == 1 and o == "900"):
                    _touch(conn, f"c-{d}-{o}-{i}", o, f"2026-09-{d:02d}T{hm}:00")
    _touch(conn, "c-lunch", "900", "2026-09-12T13:00:00")              # 2h15 later: a new session, no gap counted
    _touch(conn, "c-same-a", "901", "2026-09-13T00:00:00")             # a day with no time: a day, not a gap
    _touch(conn, "c-same-b", "901", "2026-09-13T00:00:00")
    conn.commit()
    m = capacity.measured(conn)
    assert (m["touches"], m["sdrs"], m["gaps"], m["minutes_touch"], m["minutes_in_force"]) == (147, 3, 108, 15.0, True)
    assert (m["hours"], m["hours_sdrs"], m["hours_in_force"]) == (12.3, 3, True)         # 12 mornings of 45 + 15 minutes (900 and 901 one more session of a touch), in a 30-day window, scaled to 30.4
    assert capacity.unit(conn, "sdr_minutes_touch") == 15.0 and capacity.unit(conn, "sdr_touch_hours_month") == 12.3
    assert capacity.source(conn, "sdr_minutes_touch") == "measured" and round(capacity.touches_per_month(conn, 12)) == 354   # 12 × 12.3 h ÷ 25 min
    t = client.get("/leadership").text
    assert "15 the cold touch (measured)" in t and "12.3 h (measured)" in t and "The measured minutes are in force. The measured hours are in force." in t
    # A typed figure wins; typing the measured figure back drops it.
    assert capacity.set_assumptions(conn, minutes_touch=20) == ["sdr_minutes_touch"] and capacity.source(conn, "sdr_minutes_touch") == "typed"
    assert "The typed 20 minutes a cold touch wins; type the measured 15 to let the measured figure rule." in client.get("/leadership").text
    assert capacity.set_assumptions(conn, minutes_touch=15) == ["sdr_minutes_touch"] and get_setting(conn, "sdr_minutes_touch") is None
    assert capacity.source(conn, "sdr_minutes_touch") == "measured" and capacity.set_assumptions(conn, minutes_touch=15) == []
