"""Rough call notes written up and put where HubSpot keeps them: the summary on the lead, the size on the company,
the next step as a task, the follow-up drafted from the call; and the minutes saved back as capacity."""
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from auxocase import capacity
from auxocase.data.generate import generate
from auxocase.feedback import loop as fb
from auxocase.hubspot.mock import MockHubSpot
from auxocase.llm.callnotes import offline_facts, offline_next, offline_outcome, write_up
from auxocase.llm.client import Offline
from auxocase.routing import callnotes
from auxocase.routing.engine import Router
from auxocase.routing.outreach import log_demo_activity, prepare_outreach
from auxocase.routing.touches import detect_first_touches
from auxocase.scoring.pipeline import Scorer
from auxocase.service.app import create_app
from auxocase.storage.db import connect

NOTES = "spoke w/ Ravi, 40 ppl, on gusto, renews in jan, cfo decides. wants demo next week"


def test_the_facts_and_the_next_step_come_from_the_notes():
    now = datetime(2026, 11, 2, 9, 0)
    f = offline_facts(NOTES)
    assert f["employees"] == 40 and f["provider"] == "Gusto" and f["timeline"].lower().startswith("renews in jan") and "cfo decides" in f["decision_maker"].lower()
    assert offline_next(NOTES) == "demo" and offline_outcome(NOTES) == "connected"
    assert offline_next("no answer, try again thursday") == "call_back" and offline_outcome("no answer, try again thursday") == "no_answer"
    assert offline_next("happy with ADP, not interested") == "not_interested" and offline_facts("happy with ADP")["provider"] == "ADP"
    w = write_up(Offline(), NOTES, now=now)
    assert w.summary.startswith("Spoke with Ravi") and w.summary.endswith(".") and w.next == "demo" and w.when == "2026-11-09"
    assert w.notes_line(now) == f"Call 2026-11-02 (connected): {w.summary} Learned: employees: 40; timeline: {f['timeline']}; provider: Gusto; decision maker: {f['decision_maker']}."
    assert write_up(Offline(), "left vm", now=now, disposition="no_answer").outcome == "no_answer"     # HubSpot's outcome stands


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
    """One SDR with one lead waiting, the first email drafted, call notes written up from now."""
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
    assert capacity.set_assumptions(conn, call_notes=True) == [capacity.CALL_NOTES_AUTO]
    return path, conn, hs, owner, lead


def test_a_call_with_notes_is_written_up_and_put_in_hubspot(world):
    path, conn, hs, owner, lead = world
    assert callnotes.write_up_new(hs, conn, Offline())["written"] == 0
    log_demo_activity(hs, conn, lead, "call", disposition="connected", body=NOTES)
    detect_first_touches(hs, conn, refill=False)
    out = callnotes.write_up_new(hs, conn, Offline())
    assert out["written"] == 1 and callnotes.write_up_new(hs, conn, Offline())["written"] == 0          # once
    w = callnotes.latest(conn, lead)
    assert w["next_step"] == "demo" and w["facts"]["employees"] == 40 and w["outcome"] == "connected"
    assert "notes" in w["acted"] and "company size 40" in w["acted"] and "task to book the demo on" in w["acted"]
    c = hs.get_contact(lead)
    assert c.notes.endswith(w["summary"] + " Learned: employees: 40; timeline: " + w["facts"]["timeline"] + "; provider: Gusto; decision maker: " + w["facts"]["decision_maker"] + ".")
    assert hs.get_company(c.company_id).num_employees == 40
    task = [t for t in hs.list_tasks(owner.id) if t.contact_id == lead][-1]
    assert task.subject.startswith("Book the demo:") and task.due_at.date().isoformat() == w["next_when"]
    assert w["subject"] and owner.meetings_link in w["body"]                                            # the follow-up, drafted from the call
    client = TestClient(create_app(path, scheduler=False))
    page = client.get(f"/lead/{lead}").text
    assert "· written up</h3>" in page and "Learned: employees 40" in page and "Next: book the demo on" in page
    assert f'data-mail="mail-call-{lead}"' in page and "company size 40" in page              # the follow-up, editable in place


def test_not_interested_closes_and_the_demo_button_carries_the_notes(world):
    path, conn, hs, owner, lead = world
    client = TestClient(create_app(path, scheduler=False))
    r = client.post(f"/lead/{lead}/hubspot", data={"kind": "call", "disposition": "connected", "notes": "spoke, happy with ADP, not interested"}, follow_redirects=False)
    assert r.status_code == 303
    w = callnotes.latest(conn, lead)
    assert w["next_step"] == "not_interested" and "closed in HubSpot" in w["acted"] and w["body"] == ""
    assert hs.get_contact(lead).hs_lead_status == "UNQUALIFIED" and "provider: ADP" in hs.get_contact(lead).notes
    assert "written up" in client.get(f"/lead/{lead}").text
    # A call logged without notes is left alone; the switch off leaves everything alone.
    log_demo_activity(hs, conn, lead, "call", disposition="no_answer")
    assert callnotes.write_up_new(hs, conn, Offline())["written"] == 0
    capacity.set_assumptions(conn, call_notes=False)
    log_demo_activity(hs, conn, lead, "call", disposition="no_answer", body="no answer, try again")
    assert callnotes.write_up_new(hs, conn, Offline()) == {"auto": False}


def test_the_minutes_saved_on_logging_come_back_as_capacity(world):
    path, conn, hs, owner, lead = world
    capacity.set_assumptions(conn, call_notes=False)
    assert capacity.call_share(conn) == 0.5 and capacity.minutes_per_lead(conn) == 30.0                 # no first touch seen yet: a guess
    capacity.set_assumptions(conn, call_notes=True)
    assert capacity.call_log_saving(conn) == 1.5 and capacity.minutes_per_lead(conn) == 28.5             # half the touches are calls, 3 minutes each
    assert round(capacity.touches_per_month(conn, 12)) == 526
    log_demo_activity(hs, conn, lead, "call", disposition="connected", body=NOTES)
    detect_first_touches(hs, conn, refill=False)
    assert capacity.call_share(conn) == 1.0 and capacity.minutes_per_lead(conn) == 27.0                  # every touch seen so far was a call
    client = TestClient(create_app(path, scheduler=False))
    t = client.get("/leadership").text
    assert "less 3.0 saved on logging calls" in t and 'value="auto" selected>Written up' in t and ">556<" in t
    r = client.post("/leadership/capacity", data={"call_notes": "sdr", "minutes_call_log": "6"}, follow_redirects=False)
    assert r.headers["location"] == "/leadership?set=2#ld-capacity"
    t = client.get("/leadership").text
    assert ">500<" in t and "Call notes written up for the SDR, 600." in t
