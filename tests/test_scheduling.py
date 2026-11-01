"""Demos booked for the SDR: times offered from the calendar, the lead's pick read and booked in HubSpot, the
confirmation sent, a clash answered with new times; and the minutes saved back as capacity."""
import re
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from auxocase import capacity
from auxocase.data.generate import generate
from auxocase.feedback import loop as fb
from auxocase.hubspot.mock import MockHubSpot
from auxocase.llm.client import Offline
from auxocase.llm.replies import DEMO_REPLIES, picked_when
from auxocase.llm.scheduling import picked_time, slot_text, slots, utc_naive
from auxocase.routing import followups, replies, scheduling
from auxocase.routing.engine import Router
from auxocase.routing.outreach import log_demo_activity, prepare_outreach
from auxocase.routing.touches import detect_first_touches
from auxocase.scoring.pipeline import Scorer
from auxocase.service import tz
from auxocase.service.app import create_app
from auxocase.storage.db import clock, connect, freeze_clock

MON = datetime(2026, 11, 2, 14, 0)        # Monday 2 Nov, 09:00 ET


def test_times_are_offered_on_working_days_and_the_pick_is_read_from_the_words():
    offered = slots([], MON)
    assert [slot_text(t) for t in offered] == ["Tue 3 Nov, 10:00 ET", "Tue 3 Nov, 14:00 ET", "Wed 4 Nov, 10:00 ET"]
    assert [slot_text(t) for t in slots([offered[0]], MON)] == ["Tue 3 Nov, 14:00 ET", "Wed 4 Nov, 10:00 ET", "Wed 4 Nov, 14:00 ET"]   # a meeting taken
    friday = datetime(2026, 11, 6, 20, 0)
    assert slot_text(slots([], friday)[0]) == "Mon 9 Nov, 10:00 ET"                                      # no weekend
    assert picked_time("Tuesday at 2pm works for me.", MON) == offered[1]
    assert picked_time("The second one works for me, send the invite.", MON, offered) == offered[1]
    assert slot_text(picked_time("can we do Nov 5 at 10?", MON)) == "Thu 5 Nov, 10:00 ET"
    assert slot_text(picked_time("the 5th at 10:30 works", MON)) == "Thu 5 Nov, 10:30 ET"
    assert slot_text(picked_time("tomorrow at noon", MON)) == "Tue 3 Nov, 12:00 ET"
    assert picked_time("2pm then", MON, offered) == offered[1] and picked_time("Wednesday works", MON, offered) == offered[2]
    assert picked_time("sounds good", MON) is None and picked_time("next week?", MON, offered) is None
    assert picked_when("2026-11-03T14:00", MON) == offered[1].isoformat(timespec="seconds") and picked_when("junk", MON) is None
    assert picked_when("2026-10-01T14:00", MON) is None                                                  # in the past


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
    """One SDR with one lead, first email sent and seen; replies triaged and demos booked for the SDR from now."""
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
    assert set(capacity.set_assumptions(conn, auto=True, replies=True, scheduling=True)) == {capacity.AUTO, capacity.REPLIES_AUTO, capacity.SCHEDULING_AUTO}
    freeze_clock(conn, MON.isoformat(timespec="seconds"))
    log_demo_activity(hs, conn, lead, "email", "hello")
    detect_first_touches(hs, conn, refill=False)
    assert followups.schedule(hs, conn) == 1
    return path, conn, hs, owner, lead


def _reply(conn, hs, lead, kind):
    log_demo_activity(hs, conn, lead, "reply", "hello", body=DEMO_REPLIES[kind][1])


def test_an_interested_reply_gets_times_and_the_pick_books_the_demo(world):
    path, conn, hs, owner, lead = world
    _reply(conn, hs, lead, "interested")
    assert replies.triage_new(hs, conn, Offline())["interested"] == 1
    r = replies.latest(conn, lead)
    assert r["acted"] == "times offered: Tue 3 Nov, 10:00 ET, Tue 3 Nov, 14:00 ET, Wed 4 Nov, 10:00 ET; sent" and r["sent_at"] == MON.isoformat(timespec="seconds")
    assert "Would one of these work" in r["body"] and owner.meetings_link in r["body"] and r["source"] == "scheduling"
    sent = [e for e in hs.engagements_for_contact(lead) if e.direction == "OUTGOING" and e.subject == "Re: hello"]
    assert len(sent) == 1 and sent[0].body == r["body"]                                                 # sent from the SDR's inbox
    b = scheduling.latest(conn, lead)
    assert b["status"] == "offered" and len(b["offered"]) == 3 and hs.meetings_for_contact(lead) == []
    client = TestClient(create_app(path, scheduler=False))
    page = client.get(f"/lead/{lead}").text
    assert "Sent for " in page and "waiting on " in page and f'action="/lead/{lead}/send"' not in page       # sent: nothing left to send
    assert "Sent for you" in client.get(f"/queue/{owner.id}").text                                      # the queue opens it in full
    # The pick: booked on the SDR's calendar, confirmed, the follow-ups stopped, the demo counted.
    freeze_clock(conn, (MON + timedelta(hours=2)).isoformat(timespec="seconds"))
    _reply(conn, hs, lead, "time")
    assert replies.triage_new(hs, conn, Offline())["triaged"] == 1
    r = replies.latest(conn, lead)
    assert r["acted"] == "demo booked for Tue 3 Nov, 14:00 ET; confirmation sent" and "Booked: Tuesday 3 November at 14:00 ET" in r["body"]
    m = hs.meetings_for_contact(lead)
    assert len(m) == 1 and m[0].owner_id == owner.id and slot_text(m[0].start_time) == "Tue 3 Nov, 14:00 ET" and m[0].outcome == "SCHEDULED"
    assert scheduling.booked(conn, lead)["meeting_id"] == m[0].id
    assert all(f["status"] == "stopped" for f in followups.rows_for(conn, lead))
    fb.sync_outcomes(hs, conn)
    assert conn.execute("SELECT demo_booked FROM outcomes WHERE contact_id=?", (lead,)).fetchone()[0] == 1
    page = client.get(f"/lead/{lead}").text
    assert "Demo booked · Tue 3 Nov, 14:00 ET" in page and "Nothing to send" in page and "Demo booked for Tue 3 Nov, 14:00 ET by the service" in page
    assert "Demo booked · Tue 3 Nov, 14:00 ET" in client.get(f"/queue/{owner.id}").text
    # A later reply is the SDR's again: a demo is on the calendar.
    _reply(conn, hs, lead, "question")
    replies.triage_new(hs, conn, Offline())
    assert replies.latest(conn, lead)["acted"] == "answer drafted"


def test_a_taken_time_gets_new_times_and_the_switch_leaves_the_answer_a_draft(world):
    path, conn, hs, owner, lead = world
    hs.create_meeting(owner.id, "someone-else", utc_naive(datetime(2026, 11, 3, 14, 0, tzinfo=tz.TZ)), None, "busy")
    _reply(conn, hs, lead, "tuesday")
    replies.triage_new(hs, conn, Offline())
    r = replies.latest(conn, lead)
    assert r["acted"].startswith("Tue 3 Nov, 14:00 ET is taken; times offered: Tue 3 Nov, 10:00 ET, Wed 4 Nov, 10:00 ET, Wed 4 Nov, 14:00 ET")
    assert "That time is taken on my side" in r["body"] and scheduling.latest(conn, lead)["status"] == "clash" and hs.meetings_for_contact(lead) == []
    capacity.set_assumptions(conn, scheduling=False)
    _reply(conn, hs, lead, "time")
    replies.triage_new(hs, conn, Offline())
    r = replies.latest(conn, lead)
    assert r["acted"] == "answer drafted" and r["sent_at"] is None and r["source"] == "template"


def test_the_minutes_saved_on_booking_come_back_as_capacity(world):
    path, conn, hs, owner, lead = world
    capacity.set_assumptions(conn, auto=False, scheduling=False)
    assert capacity.minutes_per_lead(conn) == 30.0 and capacity.booking_saving(conn) == 0.0
    share = capacity.demo_share(conn)
    assert 0.2 < share < 0.6                                                                                    # from the outcomes synced
    capacity.set_assumptions(conn, scheduling=True)
    assert capacity.booking_saving(conn) == share * 7 and capacity.minutes_per_lead(conn) == 30.0 - share * 7
    client = TestClient(create_app(path, scheduler=False))
    t = client.get("/leadership").text
    assert "saved on booking demos" in t and 'value="auto" selected>Booked for the SDR' in t and "Demos booked by the SDR instead, 500." in t
    r = client.post("/leadership/capacity", data={"scheduling": "sdr", "minutes_booking": "12"}, follow_redirects=False)
    assert r.headers["location"] == "/leadership?set=2#ld-capacity"
    t = client.get("/leadership").text
    shown = int(re.search(r"Demos booked for the SDR, ([\d,]+)\.", t).group(1).replace(",", ""))
    assert ">500<" in t and abs(shown - 12 * 20.8333333 * 60 / (30 - share * 11)) <= 1.5                    # the page rounds the share
