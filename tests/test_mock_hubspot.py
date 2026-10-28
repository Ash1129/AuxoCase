"""The mock must refuse and misbehave the way HubSpot does, or tests built on it prove nothing."""
from datetime import datetime

import pytest

from auxocase.hubspot.mock import MockHubSpot
from auxocase.hubspot.models import Contact, Owner
from auxocase.storage.db import connect


@pytest.fixture
def hs():
    conn = connect(":memory:")
    client = MockHubSpot(conn, faithful_delivery=True)
    client.ensure_properties()
    for i in range(1, 4):
        client.add_owner(Owner(id=f"o{i}", name=f"Rep {i}", email=f"rep{i}@x.test"))
    return client


def _contact(i: int, **kw) -> Contact:
    return Contact(id=f"c{i}", email=f"c{i}@x.test", lifecycle_stage="marketingqualifiedlead", **kw)


def test_compare_and_set_owner_refuses_second_assignment(hs):
    hs.add_contact(_contact(1), emit_event=False)
    assert hs.set_owner("c1", "o1", expected_current=None) is True
    assert hs.set_owner("c1", "o2", expected_current=None) is False
    c = hs.get_contact("c1")
    assert c.hubspot_owner_id == "o1"
    assert c.hs_lead_status == "NEW"
    assert c.lifecycle_stage == "salesqualifiedlead"
    # A reassignment that names the current owner goes through.
    assert hs.set_owner("c1", "o2", expected_current="o1") is True
    assert hs.open_lead_counts() == {"o1": 0, "o2": 1, "o3": 0}


def test_first_touch_frees_capacity_but_lead_stays_open(hs):
    hs.add_contact(_contact(1), emit_event=False)
    hs.set_owner("c1", "o1", expected_current=None)
    assert hs.waiting_lead_counts()["o1"] == 1
    hs.simulate_sdr_activity("c1", "o1", "worked", datetime(2026, 1, 5, 10))
    assert hs.get_contact("c1").hs_lead_status == "IN_PROGRESS"
    assert hs.open_lead_counts()["o1"] == 1        # still the SDR's lead
    assert hs.waiting_lead_counts()["o1"] == 0     # but no longer holding the slot


def test_batch_update_chunks_and_multi_status(hs):
    for i in range(250):
        hs.add_contact(_contact(i), emit_event=False)
    updates = [(f"c{i}", {"auxo_score": float(i % 100), "auxo_route": "held"}) for i in range(250)]
    updates[17] = ("nope", {"auxo_score": 1.0})
    updates[40] = ("c40", {"not_a_property": 1})
    hs.reset_request_count()
    result = hs.batch_update_contacts(updates)
    assert hs.request_count() == 3
    assert set(result.failed) == {"nope", "c40"}
    assert "OBJECT_NOT_FOUND" in result.failed["nope"]
    assert "PROPERTY_DOESNT_EXIST" in result.failed["c40"]
    assert len(result.ok) == 248
    assert hs.get_contact("c41").auxo_score == 41.0
    assert hs.get_contact("c40").auxo_score is None


def test_auxo_writes_do_not_enqueue_events_but_notes_do(hs):
    hs.add_contact(_contact(1), emit_event=False)
    hs.batch_update_contacts([("c1", {"auxo_score": 80.0, "auxo_reason": "referral + director"})])
    assert hs.poll_events() == []
    hs.batch_update_contacts([("c1", {"notes": "asked for pricing"})])
    events = hs.poll_events()
    assert [(e.subscription_type, e.property_name) for e in events] == [("contact.propertyChange", "notes")]
    # History is kept for both, with this service as the source.
    rows = hs.conn.execute("SELECT property, source FROM hs_property_history WHERE object_id='c1' ORDER BY seq").fetchall()
    assert [tuple(r) for r in rows] == [("auxo_score", "auxocase"), ("auxo_reason", "auxocase"), ("notes", "auxocase")]
    # Turning suppression off restores HubSpot's default behaviour.
    hs.set_event_suppression(False)
    hs.batch_update_contacts([("c1", {"auxo_score": 81.0})])
    assert [e.property_name for e in hs.poll_events()] == ["auxo_score"]


def test_auxo_properties_must_exist_before_writing():
    hs = MockHubSpot(connect(":memory:"), faithful_delivery=True)
    hs.add_contact(_contact(1), emit_event=False)
    result = hs.batch_update_contacts([("c1", {"auxo_score": 1.0})])
    assert "c1" in result.failed
    assert set(hs.ensure_properties()) >= {"auxo_score", "auxo_route"}
    assert hs.ensure_properties() == []
    assert hs.batch_update_contacts([("c1", {"auxo_score": 1.0})]).all_ok


def test_poll_events_with_chaos_delivers_everything_at_least_once():
    hs = MockHubSpot(connect(":memory:"), faithful_delivery=False, chaos_seed=3)
    for i in range(400):
        hs.add_contact(_contact(i))
    seen: dict[str, int] = {}
    swapped = 0
    while True:
        batch = hs.poll_events(max_events=100)
        if not batch:
            break
        seqs = [int(e.event_id.split("-")[1]) for e in batch]
        swapped += sum(1 for a, b in zip(seqs, seqs[1:]) if a > b)
        for e in batch:
            seen[e.event_id] = seen.get(e.event_id, 0) + 1
    assert len(seen) == 400
    assert any(n > 1 for n in seen.values()), "expected some duplicates"
    assert swapped > 0, "expected some reordering"
    assert hs.poll_events() == []


def test_simulate_sdr_activity_leaves_hubspot_shaped_rows(hs):
    hs.add_contact(_contact(1), emit_event=False)
    t0 = datetime(2026, 1, 5, 9)
    hs.simulate_sdr_activity("c1", "o1", "worked", t0)
    hs.simulate_sdr_activity("c1", "o1", "demo_booked", datetime(2026, 1, 9), amount=24000)
    hs.simulate_sdr_activity("c1", "o1", "closed_won", datetime(2026, 3, 1))
    c = hs.get_contact("c1")
    assert c.hubspot_owner_id == "o1" and c.lifecycle_stage == "customer"
    deals = hs.deals_for_contact("c1")
    assert len(deals) == 1 and deals[0].dealstage == "closedwon" and deals[0].hs_is_closed_won
    assert deals[0].closedate == datetime(2026, 3, 1) and deals[0].amount == 24000
    meetings = hs.meetings_for_contact("c1")
    assert len(meetings) == 1 and meetings[0].outcome == "COMPLETED"
    first_touch = hs.conn.execute(
        "SELECT changed_at, value, source FROM hs_property_history WHERE object_id='c1' AND property='hs_lead_status' ORDER BY seq LIMIT 1"
    ).fetchone()
    assert tuple(first_touch) == (t0.isoformat(), "IN_PROGRESS", "sdr")
    assert [e.subscription_type for e in hs.poll_events()].count("deal.propertyChange") == 1
    assert len(hs.deals_modified_since(datetime(2026, 2, 1))) == 1
    assert hs.meetings_since(datetime(2026, 1, 1))[0].contact_id == "c1"


def test_search_modified_since_is_ordered_and_capped(hs):
    for i in range(5):
        hs.add_contact(_contact(i, createdate=datetime(2026, 1, i + 1), lastmodifieddate=datetime(2026, 1, i + 1)), emit_event=False)
    got = hs.search_contacts_modified_since(datetime(2026, 1, 3), limit=2)
    assert [c.id for c in got] == ["c2", "c3"]
    assert hs.list_contacts(lifecycle_stage="lead") == []
    task = hs.create_task("o1", "c2", "Call", "ranked 3 of 40")
    assert hs.list_tasks("o1")[0].id == task.id
