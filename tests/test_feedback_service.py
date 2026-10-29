"""The learning loop, the month simulator and the web service."""
import pytest
from fastapi.testclient import TestClient

from auxocase.data.generate import generate
from auxocase.data.simulate import simulate_month
from auxocase.feedback import loop as fb
from auxocase.hubspot.mock import MockHubSpot
from auxocase.service.app import create_app
from auxocase.service.security import approval_token, lead_token
from auxocase.storage.db import connect


def _seen(conn, hs, contact_id):
    """The version token a page would have put in the form for this lead."""
    return lead_token(conn, hs.get_contact(contact_id))


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    path = tmp_path_factory.mktemp("db") / "s.db"
    conn = connect(path)
    hs = MockHubSpot(conn, faithful_delivery=True)
    hs.ensure_properties()
    generate(conn, months=8, seed=5)
    fb.sync_outcomes(hs, conn)
    model, _ = fb.train_candidate(hs, conn)
    fb.approve(conn, model.version, "marketing", "m", "approve")
    fb.approve(conn, model.version, "sales", "s", "approve")
    return path, conn, hs


def test_sync_outcomes_three_label_states(db):
    _, conn, hs = db
    table = fb.build_training_table(hs, conn, window_months=8)
    states = set(table.label_state.unique())
    assert states == {"booked", "worked_no_demo", "not_worked"}
    assert (table.label_state == "not_worked").mean() > 0.5        # most MQLs are never worked, as in the brief


def test_channel_monitor_holds_small_groups_and_flags_nothing_wild(db):
    _, conn, hs = db
    pts = fb.channel_monitor(hs, conn, months=3)
    assert pts
    assert all(p.flag == "held" for p in pts if p.n < 30)
    assert all(p.lcl is not None for p in pts if p.flag != "held")


def test_send_back_retires_candidate_and_shadow_is_kept(db):
    _, conn, hs = db
    cand, _ = fb.train_candidate(hs, conn, notes="second")
    assert fb.approve(conn, cand.version, "sales", "s", "send_back", "not better")["status"] == "sent_back"
    cand2, _ = fb.train_candidate(hs, conn, notes="third")
    fb.approve(conn, cand2.version, "marketing", "m", "approve")
    fb.approve(conn, cand2.version, "sales", "s", "approve")
    statuses = {r["version"]: r["status"] for r in conn.execute("SELECT version, status FROM model_registry").fetchall()}
    assert statuses[cand2.version] == "live" and "shadow" in statuses.values() and statuses[cand.version] == "sent_back"
    cmp = fb.shadow_comparison(hs, conn)
    assert cmp is None or {"live", "shadow"} <= set(cmp)


def test_simulated_month_closes_the_loop(db):
    _, conn, hs = db
    out = simulate_month(conn, hs, seed=9, new_leads=2000)
    assert out["scored"] > 0 and out["worked"] > 0
    assert out["booked"] > 0
    assert out["candidate"] and "version" in out["candidate"]
    slices = {r["decision"] for r in conn.execute("SELECT DISTINCT decision FROM routing_log").fetchall()}
    assert "holdout_assigned" in slices


def test_service_pages_and_actions(db):
    path, conn, hs = db
    app = create_app(path)
    client = TestClient(app)
    assert client.get("/").status_code == 200
    owner = hs.list_owners()[0]
    r = client.get(f"/queue/{owner.id}")
    assert r.status_code == 200 and "queue" in r.text.lower()
    lead = next(c for c in hs.list_contacts() if c.hubspot_owner_id == owner.id)
    r = client.post(f"/lead/{lead.id}/action", data={"owner_id": owner.id, "action": "skipped", "note": "wrong fit", "seen": _seen(conn, hs, lead.id)}, follow_redirects=False)
    assert r.status_code == 303
    assert conn.execute("SELECT COUNT(*) n FROM overrides WHERE contact_id=?", (lead.id,)).fetchone()["n"] == 1
    r = client.post(f"/lead/{lead.id}/action", data={"owner_id": owner.id, "action": "promote", "move": "40", "seen": _seen(conn, hs, lead.id)}, follow_redirects=False)
    assert r.status_code == 303                                    # bounded: 40 is clipped to 5
    assert client.get(f"/lead/{lead.id}").status_code == 200
    assert client.get("/held").status_code == 200
    assert client.get("/model").status_code == 200
    assert client.post("/jobs/score?mode=events").json()["model_version"].startswith("demo-v")
    assert "decisions" in client.post("/jobs/route").json()
    assert client.post("/webhooks/hubspot", json=[{"objectId": lead.id, "subscriptionType": "contact.propertyChange", "propertyName": "notes", "occurredAt": 0, "eventId": 1, "attemptNumber": 0}]).json()["accepted"] == 1


def test_marking_a_lead_worked_refills_the_sdrs_slot(db):
    """An SDR at their limit who works their waiting lead gets the next lead straight away (BrightPath answer 4)."""
    path, conn, hs = db
    client = TestClient(create_app(path))
    limits = {o.id: o.capacity_limit for o in hs.list_owners()}
    try:
        conn.execute("UPDATE hs_owners SET capacity_limit=0")
        owner = hs.list_owners()[0]
        conn.execute("UPDATE hs_owners SET capacity_limit=1 WHERE id=?", (owner.id,))
        conn.execute("UPDATE hs_contacts SET hs_lead_status='IN_PROGRESS' WHERE hubspot_owner_id=? AND hs_lead_status IN ('NEW','OPEN')", (owner.id,))
        client.post("/jobs/route")
        waiting = [c for c in hs.list_contacts() if c.hubspot_owner_id == owner.id and c.hs_lead_status in ("NEW", "OPEN")]
        assert len(waiting) == 1 and hs.waiting_lead_counts()[owner.id] == 1   # full: one lead waiting, limit one
        again = client.post("/jobs/route").json()["decisions"]
        assert not again.get("assigned") and not again.get("holdout_assigned")  # nothing more while it waits

        r = client.post(f"/lead/{waiting[0].id}/action", data={"owner_id": owner.id, "action": "worked", "seen": _seen(conn, hs, waiting[0].id)}, follow_redirects=False)
        assert r.status_code == 303
        refill = [c for c in hs.list_contacts() if c.hubspot_owner_id == owner.id and c.hs_lead_status in ("NEW", "OPEN")]
        assert len(refill) == 1 and refill[0].id != waiting[0].id               # a new lead took the freed slot
        assert hs.get_contact(waiting[0].id).hs_lead_status == "IN_PROGRESS"   # the worked lead stays theirs
        assert conn.execute("SELECT decision FROM routing_log WHERE contact_id=? ORDER BY id DESC LIMIT 1",
                            (refill[0].id,)).fetchone()["decision"] in ("assigned", "holdout_assigned")
    finally:
        for oid, limit in limits.items():
            conn.execute("UPDATE hs_owners SET capacity_limit=? WHERE id=?", (limit, oid))


def test_lead_page_offers_an_email_link_and_a_first_email_draft(db):
    path, conn, hs = db
    client = TestClient(create_app(path))
    c = next(c for c in hs.list_contacts() if c.email and c.first_name)
    page = client.get(f"/lead/{c.id}").text
    assert f'href="mailto:{c.email}"' in page and f"/lead/{c.id}?draft=1" in page
    assert "Open in your email app" not in page                       # no draft until asked for
    page = client.get(f"/lead/{c.id}?draft=1").text
    assert "Open in your email app" in page and f"Hi {c.first_name}," in page
    assert "Drafted by the template" in page                          # tests run the offline reader
    assert f"mailto:{c.email}?subject=" in page


def test_decisions_page_lists_model_and_lead_calls(db):
    """Approvals, send-backs and every SDR call show up on one page with who, what and why."""
    path, conn, hs = db
    client = TestClient(create_app(path))
    model, _ = fb.train_candidate(hs, conn, notes="for the decisions page")
    r = client.post(f"/model/{model.version}/approve", data={"team": "sales", "approver": "Dana from sales", "seen": approval_token(conn, model.version),
                                                              "decision": "send_back", "note": "wait for more referral data"}, follow_redirects=False)
    assert r.status_code == 303
    owner = hs.list_owners()[0]
    leads = [c for c in hs.list_contacts() if c.hubspot_owner_id == owner.id][:3]
    for c, action, note in zip(leads, ("worked", "junk", "skipped"), ("", "", "already a customer")):
        client.post(f"/lead/{c.id}/action", data={"owner_id": owner.id, "action": action, "note": note, "seen": _seen(conn, hs, c.id)}, follow_redirects=False)
    page = client.get("/decisions")
    assert page.status_code == 200
    text = page.text
    assert "Sent back" in text and "Dana from sales" in text and "wait for more referral data" in text
    assert "Approved" in text                                         # the fixture's two approvals of the live model
    assert "already a customer" in text and owner.name in text
    for c in leads:
        assert f"/lead/{c.id}" in text
    assert 'href="/decisions"' in client.get("/").text               # reachable from the navigation


def test_a_model_going_live_rescores_the_pool_best_ranked_first(db):
    """Kit step 10: the approved score reaches every lead in the pool, not only leads that change later."""
    from auxocase.llm.client import Offline
    from auxocase.pool import pool_contacts
    from auxocase.scoring.pipeline import RESCORE_PENDING, Scorer, load_live_model
    from auxocase.storage.db import get_setting

    path, conn, hs = db
    Scorer(hs, conn, load_live_model(conn), Offline()).rescore_pool()           # the pool on the current live model
    model, _ = fb.train_candidate(hs, conn, notes="next month's candidate")
    client = TestClient(create_app(path))
    client.post(f"/model/{model.version}/approve", data={"team": "marketing", "approver": "m", "decision": "approve", "seen": approval_token(conn, model.version)})
    assert get_setting(conn, RESCORE_PENDING) is None                           # one approval is not enough
    client.post(f"/model/{model.version}/approve", data={"team": "sales", "approver": "s", "decision": "approve", "seen": approval_token(conn, model.version)})
    assert get_setting(conn, RESCORE_PENDING) == model.version

    sc = Scorer(hs, conn, load_live_model(conn), Offline())
    stale = sc.stale_pool()
    ranks = [c.auxo_rank for c in stale if c.auxo_rank]
    assert stale and ranks == sorted(ranks)                                     # best-ranked first
    no_time = sc.rescore_stale(budget_s=0)                                      # out of time: nothing done, still pending
    assert no_time["pending"] and no_time["rescored"] == 0 and 0 < no_time["left"] <= len(stale)   # (the background run is under way)

    client.app.state.rescore_thread.join(timeout=600)                           # the approval started it in the background
    assert {c.auxo_model_version for c in pool_contacts(hs, conn) if c.auxo_probability is not None} == {model.version}
    assert get_setting(conn, RESCORE_PENDING) is None
    assert "rescoring" not in client.get("/model").text
    assert sc.rescore_stale() == {"pending": False}


def test_unchanged_notes_are_not_read_again_when_a_lead_is_rescored(db):
    from auxocase.llm.client import Offline
    from auxocase.scoring.pipeline import Scorer, load_live_model

    class Counting(Offline):
        reads = 0

        def complete(self, system, user, max_tokens=600):
            if user.startswith("TASK: signals"):
                Counting.reads += 1
            return super().complete(system, user, max_tokens)

    path, conn, hs = db
    c = next(c for c in hs.list_contacts(lifecycle_stage="marketingqualifiedlead") if (c.notes or "").strip())
    llm = Counting()
    Scorer(hs, conn, None, llm).score_batch([c])                                # the points table reads the notes
    assert Counting.reads == 1
    Scorer(hs, conn, load_live_model(conn), llm).score_batch([hs.get_contact(c.id)])   # a new model, same notes
    assert Counting.reads == 1
    hs.batch_update_contacts([(c.id, {"notes": c.notes + " We are hiring twenty more people next quarter."})])
    Scorer(hs, conn, load_live_model(conn), llm).score_batch([hs.get_contact(c.id)])   # new notes are read
    assert Counting.reads == 2
