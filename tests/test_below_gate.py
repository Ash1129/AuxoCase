"""Below HubSpot's MQL gate: off by default, plain leads counted and moved to SQL when assigned, exploration there
first (at its share, however small the passes), ranking there only when switched on, and reports and a badge that
know which leads the gate passed."""
import json
from datetime import timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from auxocase.data.generate import generate
from auxocase.feedback import loop as fb
from auxocase.hubspot.models import BatchResult
from auxocase.hubspot.mock import MockHubSpot
from auxocase.hubspot.real import RealHubSpot
from auxocase.llm.client import Offline
from auxocase.pool import BELOW_GATE_REASON, below_gate_contacts, passed_gate, pool_contacts
from auxocase.routing.engine import Router
from auxocase.scoring.pipeline import RESCORE_PENDING, Scorer
from auxocase.service.app import create_app
from auxocase.storage.db import clock, connect, get_setting, set_setting


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
    """A copy of the base with one SDR free for two leads and everyone else full."""
    path = tmp_path / "w.db"
    src = connect(base)
    src.execute(f"VACUUM INTO '{path}'")
    src.close()
    conn = connect(path)
    hs = MockHubSpot(conn, faithful_delivery=True)
    conn.execute("UPDATE hs_owners SET capacity_limit=0")
    owner = hs.list_owners()[0]
    conn.execute("UPDATE hs_owners SET capacity_limit=2 WHERE id=?", (owner.id,))
    conn.execute("UPDATE hs_contacts SET hs_lead_status='IN_PROGRESS' WHERE hs_lead_status IN ('NEW','OPEN') AND hubspot_owner_id IS NOT NULL")
    return path, conn, hs, owner


def _assigned(decisions):
    return [d for d in decisions if d.decision in ("assigned", "holdout_assigned")]


def _log(conn, contact_id):
    return conn.execute("SELECT * FROM routing_log WHERE contact_id=? ORDER BY id DESC LIMIT 1", (contact_id,)).fetchone()


def test_off_by_default_the_pool_and_every_assignment_stay_above_the_gate(world):
    _, conn, hs, owner = world
    assert below_gate_contacts(hs, conn), "the base must have plain leads waiting below the gate"
    assert all(c.lifecycle_stage == "marketingqualifiedlead" for c in pool_contacts(hs, conn))
    d = _assigned(Router(hs, conn, exploration_share=1.0).run())       # every free slot to the exploration slice
    assert d and all(x.reason != BELOW_GATE_REASON and not x.below_gate for x in d)
    assert all(not hs.get_contact(x.contact_id).auxo_below_gate for x in d)


def test_an_assigned_plain_lead_moves_to_sql_and_counts_against_capacity(world):
    _, conn, hs, owner = world
    before = hs.waiting_lead_counts()[owner.id]
    lead = below_gate_contacts(hs, conn)[0]
    assert hs.set_owner(lead.id, owner.id)
    c = hs.get_contact(lead.id)
    assert c.lifecycle_stage == "salesqualifiedlead" and c.hs_lead_status == "NEW"
    assert hs.waiting_lead_counts()[owner.id] == before + 1
    # A plain lead given an owner in HubSpot directly, without the move to SQL, still takes a slot.
    other = below_gate_contacts(hs, conn)[0]
    hs.batch_update_contacts([(other.id, {"hubspot_owner_id": owner.id, "hs_lead_status": "NEW"})])
    assert hs.get_contact(other.id).lifecycle_stage == "lead"
    assert hs.waiting_lead_counts()[owner.id] == before + 2


def test_exploration_below_the_gate_hands_out_scored_plain_leads_and_learns_from_them(world):
    _, conn, hs, owner = world
    fb.switch_below_gate(conn, explore=1.0)
    d = _assigned(Router(hs, conn, exploration_share=1.0).run())
    assert len(d) == 2 and all(x.decision == "holdout_assigned" and x.reason == BELOW_GATE_REASON for x in d)
    for x in d:
        c = hs.get_contact(x.contact_id)
        assert c.lifecycle_stage == "salesqualifiedlead" and c.hubspot_owner_id == owner.id
        assert c.auxo_below_gate and c.auxo_holdout and c.auxo_probability is not None   # scored as it was picked
        row = _log(conn, x.contact_id)
        assert row["below_gate"] == 1 and row["probability"] == c.auxo_probability       # recorded at hand-out
        assert not passed_gate(c)                                                         # SQL now, but not through the gate
    assert all(c.lifecycle_stage == "marketingqualifiedlead" for c in pool_contacts(hs, conn))   # the ranked pool is unchanged
    # The SDR books one; outcome sync and the training table see it like any other worked exploration lead.
    picked = d[0].contact_id
    hs.simulate_sdr_activity(picked, owner.id, "demo_booked", clock(conn))
    fb.sync_outcomes(hs, conn)
    assert conn.execute("SELECT demo_booked FROM outcomes WHERE contact_id=?", (picked,)).fetchone()["demo_booked"] == 1
    table = fb.build_training_table(hs, conn)
    row = table.contact_ids[table.contact_ids == picked].index[0]
    assert table.label_state[row] == "booked" and bool(table.holdout[row])
    assert hs.get_contact(picked).auxo_below_gate and hs.get_contact(picked).lifecycle_stage == "opportunity"


def test_readiness_compares_outcomes_with_the_score_at_hand_out_once_labels_mature(world):
    _, conn, hs, owner = world
    set_setting(conn, "pool_max_age_days", "3")
    candidates = below_gate_contacts(hs, conn)
    Scorer(hs, conn, None, Offline()).score_batch(candidates)                  # scored once before (ranking on for a while, say)
    hs.batch_update_contacts([(c.id, {"pricing_page_views": 9, "num_form_submissions": 4}) for c in candidates])   # then they moved
    fb.switch_below_gate(conn, explore=1.0)
    d = _assigned(Router(hs, conn, exploration_share=1.0).run())
    at_hand_out = {x.contact_id: hs.get_contact(x.contact_id).auxo_probability for x in d}
    for x in d:
        hs.simulate_sdr_activity(x.contact_id, owner.id, "worked", clock(conn))
    hs.simulate_sdr_activity(d[0].contact_id, owner.id, "demo_booked", clock(conn))
    fb.sync_outcomes(hs, conn)
    s = fb.below_gate_readiness(conn)
    assert (s["assigned"], s["worked"], s["matured"]) == (2, 2, 0)            # too young to count yet
    set_setting(conn, "clock", (clock(conn) + timedelta(days=45)).isoformat())
    Scorer(hs, conn, None, Offline()).score_batch([hs.get_contact(i) for i in at_hand_out])   # a later rescore must not change it
    s = fb.below_gate_readiness(conn)
    assert (s["matured"], s["booked"], s["observed_rate"]) == (2, 1, 0.5) and not s["ready"]
    assert s["predicted_rate"] == round(sum(at_hand_out.values()) / 2, 3)
    first = [conn.execute("SELECT probability FROM scores WHERE contact_id=? ORDER BY id LIMIT 1", (i,)).fetchone()["probability"] for i in at_hand_out]
    assert round(sum(first) / 2, 3) != s["predicted_rate"]                    # the oldest score would have said something else


def test_shares_hold_over_many_small_passes_with_a_fresh_router_each_time(world):
    """The scheduler, the refill and the pages build a new Router for every pass, and most passes free one slot."""
    _, conn, hs, owner = world
    conn.execute("UPDATE hs_owners SET capacity_limit=1 WHERE id=?", (owner.id,))
    fb.switch_below_gate(conn, explore=0.5)
    picks = []
    for _ in range(8):
        d = _assigned(Router(hs, conn, exploration_share=0.5).run())
        assert len(d) == 1
        picks.append(d[0])
        hs.batch_update_contacts([(d[0].contact_id, {"hs_lead_status": "IN_PROGRESS"})])   # touched: the slot frees
    assert sum(p.decision == "holdout_assigned" for p in picks) == 4                       # half of eight
    assert sum(p.below_gate for p in picks) == 2                                           # half of those four
    assert len({p.contact_id for p in picks if p.below_gate}) == 2


def test_switching_ranking_on_is_refused_until_ready_and_then_scores_the_waiting_plain_leads(world):
    _, conn, hs, owner = world
    with pytest.raises(ValueError, match="0 of 100"):
        fb.switch_below_gate(conn, explore=0.5, rank=True)
    assert get_setting(conn, "explore_below_gate_share") is None and get_setting(conn, "pool_stages") is None   # nothing changed
    with pytest.raises(ValueError):
        fb.switch_below_gate(conn, explore=1.5)
    set_setting(conn, "pool_max_age_days", "3")                                # a few days of leads keeps the rescore short
    waiting = below_gate_contacts(hs, conn)
    assert waiting and all(c.auxo_probability is None for c in waiting)        # never scored: they were not in the pool
    fb.switch_below_gate(conn, rank=True, force=True)
    assert get_setting(conn, RESCORE_PENDING) and below_gate_contacts(hs, conn) == []
    assert Router(hs, conn).pool() and not any(c.lifecycle_stage == "lead" for c in Router(hs, conn).pool())   # until scored
    r = Scorer(hs, conn, None, Offline()).rescore_stale()                      # what the scheduler's rescore job runs
    assert r["rescored"] >= len(waiting) and r["left"] == 0 and get_setting(conn, RESCORE_PENDING) is None
    plain = [c for c in Router(hs, conn).pool() if c.lifecycle_stage == "lead"]
    assert plain and all(c.auxo_rank for c in plain)
    # Make the MQLs a poor bet, so the ranked top of the pool, with no exploration, is plain leads.
    hs.batch_update_contacts([(c.id, {"auxo_probability": 0.01}) for c in pool_contacts(hs, conn) if c.lifecycle_stage != "lead"])
    d = _assigned(Router(hs, conn, exploration_share=0.0).run())
    assert len(d) == 2 and all(x.decision == "assigned" and x.below_gate for x in d)
    assert all(hs.get_contact(x.contact_id).auxo_below_gate for x in d)
    s = fb.below_gate_readiness(conn)
    assert s["assigned"] == 0 and s["assigned_by_rank"] == 2                   # ranked picks are not unbiased labels


def test_the_gate_reports_count_only_what_hubspots_gate_passed(world):
    _, conn, hs, owner = world
    fb.switch_below_gate(conn, explore=1.0)
    channel = lambda: {g["channel"]: g["mqls"] for g in fb.mql_gate_report(hs, conn)}
    before = channel()
    Router(hs, conn, exploration_share=1.0).run()                              # two plain leads move to SQL
    assert channel() == before
    # A plain lead that HubSpot's own gate passes later is an MQL like any other: no flag, no badge.
    plain = below_gate_contacts(hs, conn)[0]
    Scorer(hs, conn, None, Offline()).score_batch([plain])                    # scoring alone does not flag it
    plain = hs.get_contact(plain.id)
    assert not plain.auxo_below_gate and not passed_gate(plain)
    hs.batch_update_contacts([(plain.id, {"lifecycle_stage": "marketingqualifiedlead", "hubspot_score": 90})])
    assert passed_gate(hs.get_contact(plain.id))


def test_the_badge_shows_on_the_queue_and_the_lead_page(world):
    path, conn, hs, owner = world
    fb.switch_below_gate(conn, explore=1.0)
    picked = _assigned(Router(hs, conn, exploration_share=1.0).run())[0].contact_id
    client = TestClient(create_app(path))
    assert "below old threshold" in client.get(f"/queue/{owner.id}").text
    page = client.get(f"/lead/{picked}").text
    assert "below old threshold" in page and "drawn at random for the exploration slice" in page
    mql = next(c for c in pool_contacts(hs, conn))
    assert "below old threshold" not in client.get(f"/lead/{mql.id}").text


def test_a_score_hubspot_refused_is_written_again_on_the_next_run(world):
    _, conn, hs, owner = world
    lead = pool_contacts(hs, conn)[0]
    hs.batch_update_contacts([(lead.id, {"notes": (lead.notes or "") + " New: hiring forty people."})])
    real_update = hs.batch_update_contacts

    def refuse(updates):
        updates = list(updates)
        if any(cid == lead.id and "auxo_score" in props for cid, props in updates):
            return BatchResult(ok=[], failed={lead.id: "PROPERTY_DOESNT_EXIST: auxo_score"})
        return real_update(updates)

    hs.batch_update_contacts = refuse
    first = Scorer(hs, conn, None, Offline()).score_batch([hs.get_contact(lead.id)])[0]
    hs.batch_update_contacts = real_update
    assert "growth" in first.reason and "growth" not in (hs.get_contact(lead.id).auxo_reason or "")   # HubSpot refused it
    assert conn.execute("SELECT 1 FROM scores WHERE contact_id=? AND input_hash=?", (lead.id, first.input_hash)).fetchone() is None
    Scorer(hs, conn, None, Offline()).score_batch([hs.get_contact(lead.id)])  # the next run, HubSpot accepting again
    assert "growth" in hs.get_contact(lead.id).auxo_reason
    assert conn.execute("SELECT 1 FROM scores WHERE contact_id=? AND input_hash=?", (lead.id, first.input_hash)).fetchone()


def test_the_real_client_counts_plain_leads_and_moves_them_to_sql():
    sent = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        sent.append((request.method, request.url.path, body))
        if request.url.path == "/crm/v3/owners":
            return httpx.Response(200, json={"results": [{"id": "900", "email": "sdr1@brightpath.example", "userId": 1}]})
        if request.url.path == "/crm/v3/objects/contacts/search":
            return httpx.Response(200, json={"results": [{"id": "1", "properties": {"hubspot_owner_id": "900"}}]})
        if request.url.path == "/scheduler/v3/meetings/meeting-links":
            return httpx.Response(200, json={"results": []})
        if request.method == "GET" and request.url.path == "/crm/v3/objects/contacts/7":
            return httpx.Response(200, json={"id": "7", "properties": {"lifecyclestage": "lead", "hs_lead_status": ""}})
        return httpx.Response(200, json={"id": "7", "properties": {}})

    hs = RealHubSpot("test-token")
    hs._client = httpx.Client(base_url="https://api.hubapi.com", transport=httpx.MockTransport(handler))
    assert hs.waiting_lead_counts() == {"900": 1}
    search = next(b for m, p, b in sent if p == "/crm/v3/objects/contacts/search")
    stage = next(f for f in search["filterGroups"][0]["filters"] if f["propertyName"] == "lifecyclestage")
    assert "lead" in stage["values"] and "marketingqualifiedlead" in stage["values"]
    assert hs.set_owner("7", "900")
    patch = next(b for m, p, b in sent if m == "PATCH")
    assert patch["properties"] == {"hubspot_owner_id": "900", "hs_lead_status": "NEW", "lifecyclestage": "salesqualifiedlead"}
