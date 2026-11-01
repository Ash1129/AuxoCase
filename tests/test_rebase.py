"""Rebasing the mock onto the present: demo-clock dates move back whole years, wall-time logs stay, the clock runs live
and a replayed month freezes it again."""
import json
from datetime import datetime, timedelta

from auxocase.data.generate import generate
from auxocase.data.rebase import rebase, shift_text, span_to_now, wall_window, years_to_now
from auxocase.feedback import loop as fb
from auxocase.hubspot.mock import MockHubSpot
from auxocase.llm.client import Offline
from auxocase.scoring.pipeline import Scorer
from auxocase.storage.db import clock, clock_frozen, connect, freeze_clock, get_setting


def test_shift_text_keeps_the_rest_and_lands_a_leap_day_on_the_28th():
    assert shift_text("2028-02-29T10:00:00", 2) == "2026-02-28T10:00:00"
    assert shift_text("2028-02-29T10:00:00", 4) == "2024-02-29T10:00:00"
    assert shift_text("2028-05", 2) == "2026-05" and shift_text("2025-08-17T11:46:25.311415", 1) == "2024-08-17T11:46:25.311415"
    assert shift_text("not a date", 2) == "not a date" and shift_text(None, 2) is None


def test_rebase_moves_demo_dates_keeps_wall_logs_and_runs_the_clock_live(tmp_path):
    conn = connect(tmp_path / "r.db")
    hs = MockHubSpot(conn, faithful_delivery=True)
    hs.ensure_properties()
    generate(conn, months=4, seed=5)                                   # Aug to Nov 2025 on the demo clock
    fb.sync_outcomes(hs, conn)
    Scorer(hs, conn, None, Offline()).rescore_pool()                   # wall-time scores, and contacts touched now
    conn.execute("UPDATE hs_contacts SET createdate='2024-02-29T09:00:00' WHERE id=(SELECT MIN(id) FROM hs_contacts)")
    conn.commit()
    before = clock(conn)
    assert before.year == 2025 and clock_frozen(conn)
    scores_before = conn.execute("SELECT MIN(created_at), MAX(created_at) FROM scores").fetchone()
    wall_start = wall_window(conn)[0]                                  # the first logged run: the service's rows start here
    wall_mods = conn.execute("SELECT COUNT(*) FROM hs_contacts WHERE lastmodifieddate >= ?", (scores_before[0],)).fetchone()[0]
    assert wall_mods > 0
    years = 2025 - datetime.utcnow().year + 2                           # whatever lands the data two years back from here
    evaluation = tmp_path / "evaluation.json"
    evaluation.write_text(json.dumps({"generated_at": scores_before[0], "metrics": {"validation_months": ["2025-10", "2025-11"]},
                                      "monitor": [{"period": "2025-11", "created_at": scores_before[1]}]}))
    out = rebase(conn, years=years, evaluation=evaluation)
    assert out["years"] == years and out["rows"]["hs_contacts.createdate"] > 0 and out["clock_live"]
    # The data moved whole years; the leap day landed on the 28th.
    assert conn.execute("SELECT MAX(substr(createdate,1,4)) FROM hs_contacts").fetchone()[0] == str(2025 - years)
    assert conn.execute("SELECT createdate FROM hs_contacts WHERE id=(SELECT MIN(id) FROM hs_contacts)").fetchone()[0] == shift_text("2024-02-29T09:00:00", years)
    assert get_setting(conn, "generated_as_of") == shift_text(before.isoformat(), years)
    # The service's own timestamps did not move.
    assert conn.execute("SELECT MIN(created_at), MAX(created_at) FROM scores").fetchone() == scores_before
    assert conn.execute("SELECT COUNT(*) FROM hs_contacts WHERE lastmodifieddate >= ?", (scores_before[0],)).fetchone()[0] == wall_mods
    assert conn.execute("SELECT COUNT(*) FROM hs_property_history WHERE source='auxocase' AND changed_at < ?", (wall_start,)).fetchone()[0] == 0
    # The evaluation's months moved, its wall times did not.
    ev = json.loads(evaluation.read_text())
    assert ev["metrics"]["validation_months"] == [shift_text("2025-10", years), shift_text("2025-11", years)]
    assert ev["generated_at"] == scores_before[0] and ev["monitor"][0]["created_at"] == scores_before[1]
    # The clock runs live now, and a replayed month freezes it again.
    assert not clock_frozen(conn) and abs((clock(conn) - datetime.utcnow()).total_seconds()) < 5
    freeze_clock(conn, "2030-01-01T00:00:00")
    assert clock_frozen(conn) and clock(conn).year == 2030
    assert years_to_now(conn) == 2030 - datetime.utcnow().year


def test_the_live_clock_shows_eastern_time_with_seconds_and_ticks(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from auxocase.service import tz
    from auxocase.service.app import create_app
    assert tz.clock_text(datetime(2026, 10, 31, 15, 16, 42)) == "31 Oct 2026 11:16:42"                 # EDT
    assert tz.clock_text(datetime(2026, 11, 2, 15, 16, 42), weekday=True) == "Mon 02 Nov 2026, 10:16:42"  # EST
    assert tz.clock_iso(datetime(2026, 10, 31, 15, 16, 42)) == "2026-10-31T15:16:42Z" and tz.LABEL == "ET"
    for var in ("HUBSPOT_PRIVATE_APP_TOKEN", "HUBSPOT_APP_SECRET", "AUXO_ALLOWED_HOSTS"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("AUXO_LLM", "offline")
    path = tmp_path / "c.db"
    conn = connect(path)
    MockHubSpot(conn, faithful_delivery=True).ensure_properties()
    generate(conn, months=3, seed=5)
    conn.close()
    client = TestClient(create_app(path, scheduler=False))
    t = client.get("/").text
    assert "on the demo clock" in t and "data-clock" not in t and "· frozen" in t          # frozen: as before
    conn = connect(path)
    rebase(conn, years=0)
    conn.close()
    t = client.get("/").text
    assert '<time data-clock datetime="' in t and 'data-tz="America/New_York" data-weekday>' in t and "</time> ET" in t
    assert "· live" in t and "UTC" not in t.split('id="ov-pool"')[0]
    assert "tick()" in client.get("/assets/app.js").text


def test_an_exact_rebase_lands_the_newest_lead_on_now_and_keeps_the_services_rows():
    by = timedelta(days=72, hours=9, minutes=1, seconds=41)
    assert shift_text("2027-01-12T23:01:41", by) == "2026-11-01T14:00:00" and shift_text("2027-01-12T23:01:41.500000", by) == "2026-11-01T14:00:00.500000"
    assert shift_text("2027-01-12", by) == "2026-11-01" and shift_text("2027-01", by) == "2026-10" and shift_text("words", by) == "words"


def test_an_exact_rebase_on_a_mock_the_simulator_ran_ahead(tmp_path):
    conn = connect(tmp_path / "x.db")
    hs = MockHubSpot(conn, faithful_delivery=True)
    hs.ensure_properties()
    generate(conn, months=3, seed=5)
    fb.sync_outcomes(hs, conn)
    Scorer(hs, conn, None, Offline()).rescore_pool()                   # the service's wall-time rows start here
    now = datetime.utcnow().replace(microsecond=0)
    newest = conn.execute("SELECT MAX(createdate) FROM hs_contacts").fetchone()[0]
    ahead = now + timedelta(days=72, hours=9)                           # as if the simulator ran months past the wall clock
    conn.execute("UPDATE hs_contacts SET createdate=? WHERE createdate=?", (ahead.isoformat(timespec="seconds"), newest))
    conn.execute("INSERT INTO routing_log(contact_id, owner_id, decision, reason, created_at) VALUES('x', '900', 'assigned', 'wall', ?)",
                 (now.isoformat(timespec="seconds"),))                  # written by the service on the live clock: stays
    conn.commit()
    span = span_to_now(conn, now)
    assert span == timedelta(days=72, hours=9)
    out = rebase(conn, exact=True, now=now)
    assert out["years"] is None and out["span"] == str(span) and out["clock_live"] and out["rows"]["hs_contacts.createdate"] > 0
    assert conn.execute("SELECT MAX(createdate) FROM hs_contacts").fetchone()[0] == now.isoformat(timespec="seconds")
    assert conn.execute("SELECT created_at FROM routing_log WHERE contact_id='x'").fetchone()[0] == now.isoformat(timespec="seconds")
    assert span_to_now(conn, now) == timedelta(0) and not clock_frozen(conn)
