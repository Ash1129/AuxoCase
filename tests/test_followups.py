"""The follow-up and the last email sent for the SDR: scheduled when the first email is the first touch, drafted and
sent on their day, stopped by a reply, a booking or the SDR, skipped when the SDR sent one, and the minutes they no
longer cost come back as capacity."""
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from auxocase import capacity
from auxocase.data.generate import generate
from auxocase.feedback import loop as fb
from auxocase.hubspot.mock import MockHubSpot
from auxocase.llm.client import Offline
from auxocase.routing import followups
from auxocase.routing.engine import Router
from auxocase.routing.outreach import log_demo_activity, prepare_outreach
from auxocase.routing.touches import detect_first_touches
from auxocase.scoring.pipeline import Scorer
from auxocase.service.app import create_app
from auxocase.storage.db import clock, connect, freeze_clock, set_setting


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
    """One SDR with one lead waiting, the first email drafted, follow-ups automatic from now."""
    for var in ("HUBSPOT_PRIVATE_APP_TOKEN", "HUBSPOT_APP_SECRET", "AUXO_ALLOWED_HOSTS"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("AUXO_LLM", "offline")
    path = tmp_path / "w.db"
    src = connect(base)
    src.execute(f"VACUUM INTO '{path}'")
    src.close()
    conn = connect(path)
    hs = MockHubSpot(conn, faithful_delivery=True)
    conn.execute("UPDATE hs_owners SET capacity_limit=0")
    owner = hs.list_owners()[0]
    conn.execute("UPDATE hs_owners SET capacity_limit=1 WHERE id=?", (owner.id,))
    conn.execute("UPDATE hs_contacts SET hs_lead_status='IN_PROGRESS' WHERE hubspot_owner_id=? AND hs_lead_status IN ('NEW','OPEN')", (owner.id,))
    Router(hs, conn).run()
    lead = conn.execute("SELECT id FROM hs_contacts WHERE hubspot_owner_id=? AND hs_lead_status IN ('NEW','OPEN')", (owner.id,)).fetchone()["id"]
    prepare_outreach(hs, conn, Offline(), sequence="seq-42")
    assert capacity.set_assumptions(conn, auto=True) == [capacity.AUTO]
    return path, conn, hs, owner, lead


def _emails(hs, lead):
    return [e for e in hs.engagements_for_contact(lead) if e.type == "EMAIL"]


def _first_email(conn, hs, lead):
    log_demo_activity(hs, conn, lead, "email", "hello")
    detect_first_touches(hs, conn, refill=False)


def test_the_follow_ups_are_scheduled_from_the_first_email_and_sent_on_their_day(world):
    path, conn, hs, owner, lead = world
    assert followups.run(hs, conn, Offline()) == {"auto": True, "scheduled": 0, "sent": 0, "stopped": 0, "skipped": 0, "waiting": 0}
    _first_email(conn, hs, lead)
    t0 = clock(conn)
    assert followups.schedule(hs, conn) == 1 and followups.schedule(hs, conn) == 0            # once
    rows = followups.rows_for(conn, lead)
    assert [(r["step"], r["status"], r["due_at"][:10]) for r in rows] == [(2, "due", (t0 + timedelta(days=3)).isoformat()[:10]),
                                                                           (3, "due", (t0 + timedelta(days=7)).isoformat()[:10])]
    assert followups.send_due(hs, conn, Offline())["sent"] == 0                               # not their day yet
    freeze_clock(conn, (t0 + timedelta(days=3, hours=1)).isoformat(timespec="seconds"))
    out = followups.run(hs, conn, Offline())
    assert out["sent"] == 1 and out["scheduled"] == 0
    sent = _emails(hs, lead)
    assert len(sent) == 2 and sent[-1].direction == "OUTGOING" and sent[-1].owner_id == owner.id and sent[-1].subject.startswith("Re: hello")
    r2 = followups.rows_for(conn, lead)[0]
    assert r2["status"] == "sent" and r2["subject"] == sent[-1].subject and r2["source"] == "template"
    assert hs.get_contact(lead).auxo_email_subject == sent[-1].subject                        # the tokens the real sequence sends from
    assert followups.send_due(hs, conn, Offline())["sent"] == 0                               # the last email waits for day 7
    freeze_clock(conn, (t0 + timedelta(days=8)).isoformat(timespec="seconds"))
    assert followups.send_due(hs, conn, Offline())["sent"] == 1 and len(_emails(hs, lead)) == 3
    assert [r["status"] for r in followups.rows_for(conn, lead)] == ["sent", "sent"]


def test_a_reply_a_booking_or_the_sdr_stops_them_and_an_sdr_email_counts_as_the_step(world):
    path, conn, hs, owner, lead = world
    _first_email(conn, hs, lead)
    followups.schedule(hs, conn)
    t0 = clock(conn)
    log_demo_activity(hs, conn, lead, "reply", "hello")
    freeze_clock(conn, (t0 + timedelta(days=4)).isoformat(timespec="seconds"))
    assert followups.send_due(hs, conn, Offline())["stopped"] == 1
    assert [(r["status"], r["reason"]) for r in followups.rows_for(conn, lead)] == [("stopped", "the lead replied")] * 2
    assert len(_emails(hs, lead)) == 2                                                        # the first email and the reply only
    # The SDR stops them from the lead page.
    conn.execute("DELETE FROM followups"); conn.execute("DELETE FROM hs_engagements WHERE direction='INCOMING'"); conn.commit()
    followups.schedule(hs, conn)
    client = TestClient(create_app(path, scheduler=False))
    page = client.get(f"/lead/{lead}").text
    assert "Next: the follow-up, sent for" in page and "Stop: I'll write them" in page and "Demo: send it now" in page
    assert "sent for you on" in page and "unless they answer" in page
    r = client.post(f"/lead/{lead}/followups", data={"what": "stop"}, follow_redirects=False)
    assert r.status_code == 303 and followups.stop(conn, lead) == 0
    assert [r["reason"] for r in followups.rows_for(conn, lead)] == ["stopped by the SDR"] * 2
    page = client.get(f"/lead/{lead}").text
    assert "not sent: stopped by the SDR" in page and "Draft the follow-up" in page           # back to the SDR's own draft
    # The SDR sends the follow-up themselves: that step is skipped, the last email still goes.
    conn.execute("DELETE FROM followups"); conn.commit()
    followups.schedule(hs, conn)
    log_demo_activity(hs, conn, lead, "email", "Re: hello")
    out = followups.send_due(hs, conn, Offline())
    assert out["skipped"] == 1 and out["sent"] == 0
    freeze_clock(conn, (t0 + timedelta(days=8)).isoformat(timespec="seconds"))
    assert followups.send_due(hs, conn, Offline())["sent"] == 1
    assert [(r["step"], r["status"]) for r in followups.rows_for(conn, lead)] == [(2, "skipped"), (3, "sent")]
    # Demo: send it now, without waiting for the day; a booking stops the rest.
    conn.execute("DELETE FROM followups"); conn.execute("DELETE FROM hs_engagements WHERE contact_id=? AND id != (SELECT MIN(id) FROM hs_engagements WHERE contact_id=?)", (lead, lead)); conn.commit()
    freeze_clock(conn, t0.isoformat(timespec="seconds"))
    followups.schedule(hs, conn)
    r = client.post(f"/lead/{lead}/followups", data={"what": "now"}, follow_redirects=False)
    assert r.status_code == 303 and [x["status"] for x in followups.rows_for(conn, lead)] == ["sent", "due"]
    assert "sent for you" in client.get(f"/lead/{lead}").text
    hs.batch_update_contacts([(lead, {"hs_lead_status": "CONNECTED"})])
    freeze_clock(conn, (t0 + timedelta(days=8)).isoformat(timespec="seconds"))
    assert followups.send_due(hs, conn, Offline())["stopped"] == 1
    assert followups.rows_for(conn, lead)[1]["reason"] == "the lead moved on"


def test_only_leads_first_emailed_after_the_switch_and_the_minutes_come_back_as_capacity(world):
    path, conn, hs, owner, lead = world
    _first_email(conn, hs, lead)
    capacity.set_assumptions(conn, auto=False)
    assert followups.run(hs, conn, Offline()) == {"auto": False} and followups.schedule(hs, conn) == 0
    capacity.set_assumptions(conn, auto=True)                                                 # on again, a moment later: the touch is before the stamp
    set_setting(conn, capacity.AUTO_SINCE, (datetime.utcnow() + timedelta(seconds=2)).isoformat(timespec="seconds"))
    assert followups.schedule(hs, conn) == 0
    # 30 minutes a lead by hand, 20 with the follow-ups sent for the SDR: the same hours work 750 leads, not 500.
    capacity.set_assumptions(conn, auto=False)
    assert capacity.minutes_per_lead(conn) == 30.0 and round(capacity.touches_per_month(conn, 12)) == 500
    capacity.set_assumptions(conn, auto=True)
    assert capacity.minutes_per_lead(conn) == 20.0 and round(capacity.touches_per_month(conn, 12)) == 750
    client = TestClient(create_app(path, scheduler=False))
    t = client.get("/leadership").text
    assert ">750<" in t and "12 SDRs × 20.8 h ÷ 20 min" in t and "Follow-ups sent by the SDR instead, 500." in t
    assert 'value="auto" selected' in t
    r = client.post("/leadership/capacity", data={"followups": "sdr", "minutes_followup": "10"}, follow_redirects=False)
    assert r.headers["location"] == "/leadership?set=2#ld-capacity"
    t = client.get("/leadership").text
    assert ">375<" in t and "Follow-ups sent for the SDR, 750." in t and 'value="sdr" selected' in t
