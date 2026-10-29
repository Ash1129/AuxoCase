"""The SDR works in HubSpot: first touch read from HubSpot's activity, and outreach drafted on assignment."""
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from auxocase.data.generate import generate
from auxocase.feedback import loop as fb
from auxocase.hubspot.mock import MockHubSpot
from auxocase.llm.client import Offline
from auxocase.routing.engine import Router
from auxocase.routing.outreach import prepare_outreach, stored_draft
from auxocase.routing.touches import detect_first_touches
from auxocase.scoring.pipeline import Scorer
from auxocase.service.app import create_app
from auxocase.storage.db import clock, connect


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
def world(base, tmp_path):
    """A fresh copy of the base per test, with one SDR at a limit of one and exactly one lead waiting for them."""
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
    waiting = _waiting(conn, owner.id)
    assert len(waiting) == 1
    return path, conn, hs, hs.list_owners()[0], waiting[0]


def _waiting(conn, owner_id):
    return [r["id"] for r in conn.execute("SELECT id FROM hs_contacts WHERE hubspot_owner_id=? AND hs_lead_status IN ('NEW','OPEN')", (owner_id,))]


def test_an_email_logged_in_hubspot_is_the_first_touch_and_refills_the_slot(world):
    _, conn, hs, owner, lead = world
    at = clock(conn)
    hs.log_engagement(lead, "EMAIL", "OUTGOING", at, owner.id, subject="Payroll at Granite Logistics")
    r = detect_first_touches(hs, conn)
    assert r["first_touches"] == 1 and r["assigned"] == 1
    assert hs.get_contact(lead).hs_lead_status == "ATTEMPTED_TO_CONTACT"
    out = conn.execute("SELECT worked, first_touch_at FROM outcomes WHERE contact_id=?", (lead,)).fetchone()
    assert out["worked"] == 1 and out["first_touch_at"] == at.isoformat(timespec="seconds")
    refill = _waiting(conn, owner.id)
    assert len(refill) == 1 and refill[0] != lead                         # the next lead took the freed slot
    assert detect_first_touches(hs, conn)["first_touches"] == 0           # read again: a lead is touched once
    stale = {s["contact_id"] for s in Router(hs, conn).stale_assignments(now=at + timedelta(days=3))}
    assert lead not in stale and refill[0] in stale                       # no reminder for a lead already emailed


def test_only_the_sdrs_outgoing_activity_on_a_waiting_lead_counts(world):
    _, conn, hs, owner, lead = world
    at = clock(conn)
    hs.log_engagement(lead, "EMAIL", "INCOMING", at)                      # the lead writing in is not the SDR's touch
    assert detect_first_touches(hs, conn)["first_touches"] == 0
    assert hs.get_contact(lead).hs_lead_status in ("NEW", "OPEN")
    hs.log_engagement(lead, "CALL", "OUTGOING", at, owner.id, connected=True)
    assert detect_first_touches(hs, conn)["first_touches"] == 1
    assert hs.get_contact(lead).hs_lead_status == "CONNECTED"
    worked = _waiting(conn, owner.id)[0]                                   # the lead that refilled the slot
    hs.batch_update_contacts([(worked, {"hs_lead_status": "IN_PROGRESS"})])  # the SDR clicked Worked first
    hs.log_engagement(worked, "EMAIL", "OUTGOING", at, owner.id)           # already moved on by the SDR: left alone
    assert detect_first_touches(hs, conn)["first_touches"] == 0
    assert hs.get_contact(worked).hs_lead_status == "IN_PROGRESS"


def test_assignment_drafts_step_one_and_enrols_the_lead_in_the_sdrs_sequence(world):
    _, conn, hs, owner, lead = world
    r = prepare_outreach(hs, conn, Offline(), sequence="seq-42")
    assert r["enrolled"] == 1 and r["drafted"] == 1
    row = stored_draft(conn, lead)
    assert row["status"] == "enrolled" and row["sequence_id"] == "seq-42" and row["enrollment_id"]
    c = hs.get_contact(lead)
    assert c.auxo_email_subject == row["subject"] and c.auxo_email_body == row["body"]   # the sequence's step-one tokens
    assert owner.meetings_link and owner.meetings_link in c.auxo_email_body
    enrolled = hs.sequence_enrollments(lead)
    assert [(e.sequence_id, e.sender_email, e.status) for e in enrolled] == [("seq-42", owner.email, "ACTIVE")]
    assert prepare_outreach(hs, conn, Offline(), sequence="seq-42")["drafted"] == 0     # each assignment once


def test_without_a_sequence_the_draft_waits_on_the_lead(world):
    _, conn, hs, owner, lead = world
    assert prepare_outreach(hs, conn, Offline(), sequence="")["drafted"] == 1
    assert stored_draft(conn, lead)["status"] == "drafted" and not hs.sequence_enrollments(lead)
    assert hs.get_contact(lead).auxo_email_body


def test_a_refused_enrolment_is_recorded_and_an_emailed_lead_gets_no_step_one(world):
    _, conn, hs, owner, lead = world
    hs.enroll_in_sequence(lead, "another-sequence", owner.email)          # HubSpot allows one sequence at a time
    assert prepare_outreach(hs, conn, Offline(), sequence="seq-42")["refused"] == 1
    assert stored_draft(conn, lead)["status"] == "refused"

    hs.log_engagement(lead, "EMAIL", "OUTGOING", clock(conn), owner.id)
    detect_first_touches(hs, conn)                                        # frees the slot: a new lead is assigned
    new = _waiting(conn, owner.id)[0]
    hs.log_engagement(new, "EMAIL", "OUTGOING", clock(conn), owner.id)    # the SDR emailed before the job ran
    r = prepare_outreach(hs, conn, Offline(), sequence="seq-42")
    assert r["drafted"] == 0 and stored_draft(conn, new) is None


def test_lead_page_walks_from_the_first_email_to_a_follow_up(world):
    path, conn, hs, owner, lead = world
    prepare_outreach(hs, conn, Offline(), sequence="seq-42")
    subject = stored_draft(conn, lead)["subject"]
    client = TestClient(create_app(path))
    page = client.get(f"/lead/{lead}").text
    assert "Draft: first email" in page and "enrolled in sequence seq-42" in page and owner.meetings_link in page
    assert "Demo: act in HubSpot." in page

    r = client.post(f"/lead/{lead}/hubspot", data={"kind": "email", "subject": subject}, follow_redirects=False)
    assert r.status_code == 303
    page = client.get(f"/lead/{lead}").text
    assert "first touch" in page and "Draft the follow-up" in page and "Email from the SDR" in page
    page = client.get(f"/lead/{lead}?draft=1").text
    assert "Draft: follow-up" in page and f"Re: {subject}" in page

    client.post(f"/lead/{lead}/hubspot", data={"kind": "reply", "subject": subject})
    page = client.get(f"/lead/{lead}").text
    assert "replied: answer from HubSpot" in page and "Draft the" not in page
    assert hs.sequence_enrollments(lead)[0].status == "UNENROLLED"         # a reply ends the sequence
