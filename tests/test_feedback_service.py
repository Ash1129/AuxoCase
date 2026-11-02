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
    work = client.get("/model").text                                   # the default tab is the one with the sign-off
    assert 'href="/model?tab=work" aria-current="page"' in work and "Sign off" in work and "Channel monitor" in work
    assert 'class="versus"' not in work and "Calibration" not in work
    decides = client.get("/model?tab=decides").text
    assert 'href="/model?tab=decides" aria-current="page"' in decides and "Calibration" in decides and "Since routing began" in decides
    assert "Sign off" not in decides and 'action="/model/' not in decides
    assert client.get("/model?tab=nonsense").text == work                    # an unknown tab is the work tab
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
    assert f'data-mail="mail-email-{c.id}"' not in page               # no draft until asked for
    page = client.get(f"/lead/{c.id}?draft=1").text
    assert f'data-mail="mail-email-{c.id}"' in page and f"Hi {c.first_name}," in page
    assert "Drafted by the template" in page                          # tests run the offline reader
    assert 'name="body"' in page and 'data-edit>Edit</button>' in page and f'data-preview="/lead/{c.id}/preview"' in page
    assert f"Send to {c.email}" in page and f'id="mail-email-{c.id}"' in page
    assert "BrightPath Payroll</span>" in page and "#b68235" in page    # the preview carries the header and the colours
    assert 'type="button" disabled' in page                            # no mailbox in the test environment: the button is off


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
    assert "Sent back" in text and "Test Admin" in text and "wait for more referral data" in text    # the person logged in
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


def _scored_pool(conn, hs):
    """Routing hands out scored leads only; score the pool here rather than rely on an earlier test having done it."""
    from auxocase.llm.client import Offline
    from auxocase.scoring.pipeline import Scorer, load_live_model
    Scorer(hs, conn, load_live_model(conn), Offline()).rescore_pool()


def test_skipping_a_lead_frees_the_slot_without_making_it_a_label(db):
    """A skip is the SDR's call not to work a lead: the slot refills, and the lead is not taught to the model as worked."""
    path, conn, hs = db
    _scored_pool(conn, hs)
    client = TestClient(create_app(path))
    limits = {o.id: o.capacity_limit for o in hs.list_owners()}
    try:
        conn.execute("UPDATE hs_owners SET capacity_limit=0")
        owner = hs.list_owners()[0]
        conn.execute("UPDATE hs_owners SET capacity_limit=1 WHERE id=?", (owner.id,))
        conn.execute("UPDATE hs_contacts SET hs_lead_status='IN_PROGRESS' WHERE hubspot_owner_id=? AND hs_lead_status IN ('NEW','OPEN')", (owner.id,))
        client.post("/jobs/route")
        waiting = [c for c in hs.list_contacts() if c.hubspot_owner_id == owner.id and c.hs_lead_status in ("NEW", "OPEN")]
        assert len(waiting) == 1
        for reason, status in (("timing", "BAD_TIMING"), ("wrong fit", "UNQUALIFIED")):
            lead = waiting[0]
            r = client.post(f"/lead/{lead.id}/action", data={"owner_id": owner.id, "action": "skipped", "note": reason,
                                                             "seen": _seen(conn, hs, lead.id)}, follow_redirects=False)
            assert r.status_code == 303
            assert hs.get_contact(lead.id).hs_lead_status == status
            waiting = [c for c in hs.list_contacts() if c.hubspot_owner_id == owner.id and c.hs_lead_status in ("NEW", "OPEN")]
            assert len(waiting) == 1 and waiting[0].id != lead.id                  # the slot was refilled at once
            fb.sync_outcomes(hs, conn)
            assert conn.execute("SELECT worked FROM outcomes WHERE contact_id=?", (lead.id,)).fetchone()["worked"] == 0
    finally:
        for oid, limit in limits.items():
            conn.execute("UPDATE hs_owners SET capacity_limit=? WHERE id=?", (limit, oid))


def test_the_queue_holds_only_what_auxocase_handed_out_and_still_needs_a_call(db, monkeypatch):
    """An SDR owns months of leads worked before AuxoCase; the queue is the waiting lead, the ones touched in HubSpot
    that need a call logged, and today's decisions, not all of those."""
    from auxocase.service.app import State
    from auxocase.service.views.queue import queue_page
    from auxocase.storage.db import clock

    path, conn, hs = db
    _scored_pool(conn, hs)
    st = State(path)
    owner = hs.list_owners()[1]
    owned = sum(1 for c in hs.list_contacts() if c.hubspot_owner_id == owner.id)
    limits = {o.id: o.capacity_limit for o in hs.list_owners()}
    try:
        conn.execute("UPDATE hs_owners SET capacity_limit=0")
        conn.execute("UPDATE hs_owners SET capacity_limit=2 WHERE id=?", (owner.id,))
        conn.execute("UPDATE hs_contacts SET hs_lead_status='IN_PROGRESS' WHERE hubspot_owner_id=? AND hs_lead_status IN ('NEW','OPEN')", (owner.id,))
        st.router().run()
        page = queue_page(st, owner.id)
        assert len(page["waiting"]) == 2 and not page["touched"] and owned > 100     # two waiting, not hundreds owned
        first, second = page["waiting"][0]["c"], page["waiting"][1]["c"]
        assert page["open"]["c"].id == first.id and page["open"]["seen"]
        assert (first.auxo_probability or 0) >= (second.auxo_probability or 0)
        hs.log_engagement(second.id, "EMAIL", "OUTGOING", clock(conn), owner.id, subject="Hello")
        from auxocase.routing.touches import detect_first_touches
        detect_first_touches(hs, conn, refill=False)
        page = queue_page(st, owner.id)
        assert [e["c"].id for e in page["touched"]] == [second.id] and second.id not in [e["c"].id for e in page["waiting"]]
        assert page["tab"] == "untouched" and page["open"]["c"].id == first.id                       # the untouched tab while it has a lead
        assert queue_page(st, owner.id, tab="touched")["open"]["c"].id == second.id                  # the touched tab opens its first lead
        assert queue_page(st, owner.id, lead=second.id)["tab"] == "touched"                          # a lead picks its tab
        html = TestClient(create_app(path)).get(f"/queue/{owner.id}?tab=touched").text
        assert 'aria-current="page">Touched · waiting to hear back' in html and "Also waiting · first touch due" not in html
        # On the touched tab the demo panel (what the lead does next) sits above "Reach out"; on the untouched tab it
        # stays at the end. Its own send is there only while no mailbox is set; the real send is "Send to <the lead>".
        assert html.index("Demo only · mock portal") < html.index("Reach out") and html.count("Demo only · mock portal") == 1
        assert f'value="/queue/{owner.id}?lead={second.id}"' in html and 'value="email">Send' in html
        untouched = TestClient(create_app(path)).get(f"/queue/{owner.id}").text
        assert untouched.index("Reach out") < untouched.index("Demo only · mock portal")
        # The "at a glance" tab: a pie a group, a slice or legend row opening the list of the leads in it.
        glance = queue_page(st, owner.id, tab="glance")
        assert glance["tab"] == "glance" and glance["open"] is None
        assert [(p["key"], p["n"]) for p in glance["glance"]["touched"]] == [("replied", 0), ("waiting", 1)]
        assert sum(p["n"] for p in glance["glance"]["untouched"]) == len(glance["waiting"]) == 1
        html = TestClient(create_app(path)).get(f"/queue/{owner.id}?tab=glance").text
        assert 'aria-current="page">At a glance' in html and html.count('class="slice ') == 2                    # one full slice a pie
        assert 'data-pie="list-touched-waiting"' in html and '<details id="list-touched-waiting" class="pie-list">' in html
        assert f'href="/queue/{owner.id}?lead={second.id}"' in html.split('id="list-touched-waiting"')[1].split("</details>")[0]
        assert "No one here right now." in html.split('id="list-touched-replied"')[1].split("</details>")[0]
        assert "Reach out" not in html and "Mark worked" not in html
        # A reply seen in HubSpot counts whether or not the triage switch is on (off here: no `replies` row is written).
        hs.log_engagement(second.id, "EMAIL", "INCOMING", clock(conn), owner.id, subject="Re: Hello", body="Yes, let's talk")
        glance = queue_page(st, owner.id, tab="glance")
        assert glance["touched"][0]["heard"] and glance["touched"][0]["reply"] is None
        assert [(p["key"], p["n"]) for p in glance["glance"]["touched"]] == [("replied", 1), ("waiting", 0)]
        html = TestClient(create_app(path)).get(f"/queue/{owner.id}?tab=glance").text
        assert "answer from HubSpot" in html.split('id="list-touched-replied"')[1].split("</details>")[0]
        monkeypatch.setenv("AUXO_SMTP_USER", "sdr@example.com"); monkeypatch.setenv("AUXO_SMTP_PASS", "app-password")
        with_mailbox = TestClient(create_app(path)).get(f"/queue/{owner.id}?tab=touched").text
        assert 'value="email">Send' not in with_mailbox and 'value="call">Log a call' in with_mailbox and "logs the call there" in with_mailbox
        monkeypatch.delenv("AUXO_SMTP_USER"); monkeypatch.delenv("AUXO_SMTP_PASS")
        r = TestClient(create_app(path)).post(f"/lead/{second.id}/hubspot", data={"kind": "call", "disposition": "no_answer",
                                                                                  "back": f"/queue/{owner.id}?lead={second.id}"}, follow_redirects=False)
        assert r.headers["location"] == f"/queue/{owner.id}?lead={second.id}"                    # a demo action made on the queue comes back to it
        r = TestClient(create_app(path)).post(f"/lead/{second.id}/hubspot", data={"kind": "call", "disposition": "no_answer", "back": "https://evil.example/"},
                                             follow_redirects=False)
        assert r.headers["location"] == f"/lead/{second.id}"
        TestClient(create_app(path)).post(f"/lead/{second.id}/action", data={
            "owner_id": owner.id, "action": "worked", "seen": _seen(conn, hs, second.id)})
        page = queue_page(st, owner.id)
        assert not page["touched"] and page["done"][0]["contact_id"] == second.id and page["done_total"] >= 1
        assert queue_page(st, owner.id, lead=first.id)["open"]["c"].id == first.id
    finally:
        for oid, limit in limits.items():
            conn.execute("UPDATE hs_owners SET capacity_limit=? WHERE id=?", (limit, oid))


def test_the_hit_rate_names_its_window_and_keeps_the_exploration_slice_apart(db):
    from auxocase.service.app import _hit_rate
    from auxocase.storage.db import clock
    from datetime import timedelta

    path, conn, hs = db
    hit = _hit_rate(conn)
    if hit:
        assert hit["days"] == 30 and hit["n"] == hit["ranked"]["n"] + hit["exploration"]["n"]
    since = (clock(conn) - timedelta(days=30)).isoformat(timespec="seconds")
    old = conn.execute("SELECT COUNT(*) n FROM routing_log WHERE decision IN ('assigned','holdout_assigned') AND created_at < ?", (since,)).fetchone()["n"]
    conn.execute("UPDATE routing_log SET created_at='2001-01-01T00:00:00' WHERE decision IN ('assigned','holdout_assigned')")
    assert _hit_rate(conn) is None                                               # nothing handed out in the window
    assert old >= 0


def test_reasons_read_as_sentences():
    from auxocase.scoring.explain import Explanation, driver_rows
    from auxocase.scoring.features import COUNT_LABELS, LABELS, reason_clause

    e = Explanation(contributions=[], positives=[reason_clause("channel=events", 0.4), reason_clause("seniority=c_level", 0.2),
                                                 reason_clause("in_target_size", 0.1)], negatives=[reason_clause("log_page_views", -0.3)])
    assert e.reason_text() == ("Ranked up because it came in through an event, the contact is C-level and the company is in the "
                               "50 to 500 employee band. Held back because it viewed few pages on the site.")
    for clause in list(LABELS.values()) + [c for pair in COUNT_LABELS.values() for c in pair]:
        assert clause.split()[0] in ("it", "the", "there"), clause                 # every clause has its own subject
    rows = driver_rows([("channel=website", -0.38), ("seniority=c_level", 0.14)])
    assert rows[0] == {"label": "Came in through the website", "better": False, "word": "worse", "effect": "−0.38", "bar": 136}
    assert rows[1]["word"] == "better" and rows[1]["bar"] == round(136 * 0.14 / 0.38)


def test_a_pie_is_cut_clockwise_from_the_top_and_a_single_part_is_a_full_ring():
    from auxocase.service.views.queue import pie
    parts = pie([{"key": "a", "label": "A", "leads": [1, 2, 3]}, {"key": "b", "label": "B", "leads": [4]}])
    assert [p["n"] for p in parts] == [3, 1] and [p["share"] for p in parts] == [0.75, 0.25]
    assert parts[0]["d"].startswith("M50,50 L50.00,4.00 A46,46 0 1 1 4.00,50.00 Z")      # three quarters, the long way round
    assert parts[1]["d"] == "M50,50 L4.00,50.00 A46,46 0 0 1 50.00,4.00 Z"               # the last quarter, back to the top
    whole, none = pie([{"key": "a", "label": "A", "leads": [1]}, {"key": "b", "label": "B", "leads": []}])
    assert whole["share"] == 1 and whole["d"].count("A46,46") == 2 and none["d"] == "" and none["share"] == 0
    assert all(p["d"] == "" and p["share"] == 0 for p in pie([{"key": "a", "label": "A", "leads": []}]))


def test_the_queue_page_says_how_many_wait_when_the_sdr_has_no_limit(db):
    path, conn, hs = db
    owner = hs.list_owners()[0]
    limit = conn.execute("SELECT capacity_limit FROM hs_owners WHERE id=?", (owner.id,)).fetchone()[0]
    try:
        conn.execute("UPDATE hs_owners SET capacity_limit=NULL WHERE id=?", (owner.id,))
        html = TestClient(create_app(path)).get(f"/queue/{owner.id}").text
        head = html.split('<p class="kicker">')[1].split("</p>")[0]
        assert f"{owner.name}'s queue · " in head and "waiting for a first touch" in head and "slot" not in head
        assert TestClient(create_app(path)).get("/").status_code == 200 and TestClient(create_app(path)).get("/held").status_code == 200
    finally:
        conn.execute("UPDATE hs_owners SET capacity_limit=? WHERE id=?", (limit, owner.id))
