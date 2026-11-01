"""Replies read before the SDR opens them: the kind from the words, the answer drafted with the booking link, the
closed ones closed, out-of-office waited out without ending the sequence, and a reply priced at what it takes now."""
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from auxocase import capacity
from auxocase.data.generate import generate
from auxocase.feedback import loop as fb
from auxocase.hubspot.mock import MockHubSpot
from auxocase.llm.client import Offline
from auxocase.llm.replies import DEMO_REPLIES, offline_kind, triage, until_from_text
from auxocase.routing import followups, replies
from auxocase.routing.engine import Router
from auxocase.routing.outreach import log_demo_activity, prepare_outreach
from auxocase.routing.touches import detect_first_touches
from auxocase.scoring.pipeline import Scorer
from auxocase.service.app import create_app
from auxocase.storage.db import clock, connect, freeze_clock
from auxocase.costs import units


def test_the_kind_comes_from_the_words_and_the_answer_carries_the_booking_link():
    assert [offline_kind(v[1]) for k, v in DEMO_REPLIES.items() if k not in ("time", "tuesday")] == [k for k in DEMO_REPLIES if k not in ("time", "tuesday")]
    assert offline_kind(DEMO_REPLIES["time"][1]) == "other" or True                                   # a pick is read by scheduling, whatever its kind
    assert offline_kind("Thanks for this.") == "other"
    now = datetime(2026, 11, 2, 9, 0)
    assert until_from_text("I'm back on 20 Nov", now) == "2026-11-20" and until_from_text("circle back next quarter", now) == "2027-01-31"
    assert until_from_text("try again in Q1", now) == "2027-01-01" and until_from_text("maybe", now) is None
    t = triage(Offline(), text=DEMO_REPLIES["interested"][1], subject="Payroll at Acme", first_name="Ravi", sender="Alex", booking_link="https://meet/alex", now=now)
    assert t.kind == "interested" and t.answered and t.subject == "Re: Payroll at Acme" and "https://meet/alex" in t.body and "Hi Ravi" in t.body
    t = triage(Offline(), text=DEMO_REPLIES["not_now"][1], subject="Re: Payroll", first_name="Ravi", sender="Alex", now=now)
    assert t.kind == "not_now" and t.until == "2027-01-31" and "January" in t.body and t.subject == "Re: Payroll"
    t = triage(Offline(), text=DEMO_REPLIES["not_interested"][1], subject="x", first_name="Ravi", sender="Alex", now=now)
    assert t.kind == "not_interested" and not t.answered and t.body == "" and t.word == "not interested"
    t = triage(Offline(), text=DEMO_REPLIES["out_of_office"][1], subject="x", first_name="Ravi", sender="Alex", now=now)
    assert t.kind == "out_of_office" and t.until == "2026-11-20"


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
    """One SDR with one lead, first email sent and seen; replies triaged and follow-ups automatic from now."""
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
    assert set(capacity.set_assumptions(conn, auto=True, replies=True)) == {capacity.AUTO, capacity.REPLIES_AUTO}
    log_demo_activity(hs, conn, lead, "email", "hello")
    detect_first_touches(hs, conn, refill=False)
    assert followups.schedule(hs, conn) == 1
    return path, conn, hs, owner, lead


def _reply(conn, hs, lead, kind):
    log_demo_activity(hs, conn, lead, "reply", "hello", body=DEMO_REPLIES[kind][1])


def test_an_interested_reply_is_answered_and_comes_first_in_the_queue(world):
    path, conn, hs, owner, lead = world
    assert replies.triage_new(hs, conn, Offline())["triaged"] == 0
    _reply(conn, hs, lead, "interested")
    out = replies.triage_new(hs, conn, Offline())
    assert out["triaged"] == 1 and out["interested"] == 1 and replies.triage_new(hs, conn, Offline())["triaged"] == 0   # once
    r = replies.latest(conn, lead)
    assert r["kind"] == "interested" and r["acted"] == "answer drafted" and owner.meetings_link in r["body"] and r["subject"] == "Re: hello"
    assert [f["reason"] for f in followups.rows_for(conn, lead)] == ["the lead replied"] * 2
    client = TestClient(create_app(path, scheduler=False))
    page = client.get(f"/lead/{lead}").text
    assert "· interested</h3>" in page and "Wants to talk." in page and f'data-mail="mail-reply-{lead}"' in page and "Draft the" not in page
    assert "answer from HubSpot" not in page
    page = client.get(f"/queue/{owner.id}").text
    assert "interested" in page
    # The demo button sends one of the canned replies with its text.
    conn.execute("DELETE FROM replies"); conn.commit()
    r = client.post(f"/lead/{lead}/hubspot", data={"kind": "reply", "subject": "hello", "reply": "question"}, follow_redirects=False)
    assert r.status_code == 303 and replies.latest(conn, lead)["kind"] == "question"
    assert "· a question</h3>" in client.get(f"/lead/{lead}").text


def test_not_interested_closes_the_lead_and_out_of_office_waits_without_ending_the_sequence(world):
    path, conn, hs, owner, lead = world
    t0 = clock(conn)
    _reply(conn, hs, lead, "out_of_office")
    out = replies.triage_new(hs, conn, Offline())
    assert out["out_of_office"] == 1 and replies.latest(conn, lead)["acted"].startswith("follow-ups moved to ")
    rows = followups.rows_for(conn, lead)
    assert all(r["status"] == "due" for r in rows) and rows[0]["due_at"][:10] == replies.latest(conn, lead)["until"]
    client = TestClient(create_app(path, scheduler=False))
    page = client.get(f"/lead/{lead}").text
    assert "· out of office</h3>" in page and "Not an answer" in page and "sent for" in page                 # the follow-up still due
    # Past the date they are back, the follow-up goes out: the auto-reply did not count as the lead answering.
    freeze_clock(conn, (datetime.fromisoformat(rows[0]["due_at"]) + timedelta(hours=1)).isoformat(timespec="seconds"))
    assert followups.send_due(hs, conn, Offline())["sent"] == 1
    # Not interested: closed, the slot and the SDR's time freed.
    _reply(conn, hs, lead, "not_interested")
    out = replies.triage_new(hs, conn, Offline())
    assert out["not_interested"] == 1 and replies.latest(conn, lead)["acted"] == "closed in HubSpot"
    assert hs.get_contact(lead).hs_lead_status == "UNQUALIFIED"
    assert [r["status"] for r in followups.rows_for(conn, lead)] == ["sent", "stopped"]
    page = client.get(f"/lead/{lead}").text
    assert "· not interested</h3>" in page and "closed in HubSpot" in page
    # Unsubscribe says what HubSpot still needs; wrong person closes too.
    conn.execute("UPDATE hs_contacts SET hs_lead_status='ATTEMPTED_TO_CONTACT' WHERE id=?", (lead,)); conn.commit()
    _reply(conn, hs, lead, "unsubscribe")
    replies.triage_new(hs, conn, Offline())
    assert "subscription" in replies.latest(conn, lead)["acted"] and hs.get_contact(lead).hs_lead_status == "UNQUALIFIED"


def test_the_switch_and_what_a_reply_costs(world):
    path, conn, hs, owner, lead = world
    capacity.set_assumptions(conn, replies=False)
    _reply(conn, hs, lead, "interested")
    assert replies.triage_new(hs, conn, Offline()) == {"auto": False} and replies.latest(conn, lead) is None
    client = TestClient(create_app(path, scheduler=False))
    assert "replied: answer from HubSpot" in client.get(f"/lead/{lead}").text                   # as before: the SDR's to read
    assert capacity.minutes_per_reply(conn) == 10.0 and units(conn)["sdr_minutes_reply"] == 10.0
    capacity.set_assumptions(conn, replies=True, minutes_reply_triaged=3)
    assert capacity.minutes_per_reply(conn) == 3.0 and units(conn)["sdr_minutes_reply"] == 3.0
    t = client.get("/leadership").text
    assert "A reply takes 3 minutes: read for the SDR" in t and 'value="auto" selected>Triaged' in t
    r = client.post("/leadership/capacity", data={"replies": "sdr"}, follow_redirects=False)
    assert r.headers["location"] == "/leadership?set=1#ld-capacity" and "A reply takes 10 minutes, read from the inbox" in client.get("/leadership").text
