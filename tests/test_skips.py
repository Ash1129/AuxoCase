"""Likely skips: counted from the SDRs' own skips by trait, held by routing with the reason when switched on, and
the minutes they would have cost given back as capacity."""
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from auxocase import capacity
from auxocase.data.generate import generate
from auxocase.feedback import loop as fb
from auxocase.hubspot.mock import MockHubSpot
from auxocase.llm.client import Offline
from auxocase.routing import skips
from auxocase.routing.engine import Router
from auxocase.scoring.pipeline import Scorer
from auxocase.service.app import create_app
from auxocase.storage.db import clock, connect


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    path = tmp_path_factory.mktemp("base") / "base.db"
    conn = connect(path)
    hs = MockHubSpot(conn, faithful_delivery=True)
    hs.ensure_properties()
    generate(conn, months=8, seed=7)
    fb.sync_outcomes(hs, conn)
    Scorer(hs, conn, None, Offline()).rescore_pool()
    conn.close()
    return path


@pytest.fixture
def world(base, tmp_path, monkeypatch):
    """The SDRs have skipped twenty individual contributors and worked twenty managers; no other decision."""
    for var in ("HUBSPOT_PRIVATE_APP_TOKEN", "HUBSPOT_APP_SECRET", "AUXO_ALLOWED_HOSTS"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("AUXO_LLM", "offline")
    path = tmp_path / "w.db"
    src = connect(base)
    src.execute(f"VACUUM INTO '{path}'")
    src.close()
    conn = connect(path)
    hs = MockHubSpot(conn, faithful_delivery=True)
    conn.execute("DELETE FROM overrides")
    owned = "hubspot_owner_id IS NOT NULL AND hubspot_owner_id != ''"
    ics = [r["id"] for r in conn.execute(f"SELECT id FROM hs_contacts WHERE seniority='individual' AND {owned} LIMIT 20")]
    mgrs = [r["id"] for r in conn.execute(f"SELECT id FROM hs_contacts WHERE seniority='manager' AND {owned} LIMIT 20")]
    assert len(ics) == 20 and len(mgrs) == 20
    for i, cid in enumerate(ics + mgrs):
        conn.execute("INSERT INTO overrides(contact_id, owner_id, action, note, created_at) VALUES(?,?,?,?,?)",
                     (cid, "o1", "skipped" if i < 20 else "worked", "wrong fit" if i < 20 else "", f"2026-10-{(i % 28) + 1:02d}T09:00:00"))
    conn.commit()
    return path, conn, hs


def test_the_counts_come_from_the_sdrs_decisions_and_name_the_trait(world):
    path, conn, hs = world
    model = skips.refresh(hs, conn)
    assert model["traits"]["seniority:individual"] == [20, 20] and model["traits"]["seniority:manager"] == [0, 20]
    assert model["replay"] == {"skips": 20, "worked": 20, "caught": 20, "held_worked": 0} and model["skip_share"] == 1.0 and model["caught_share"] == 1.0
    assert skips.stored(conn) == model
    ic = next(c for c in hs.list_contacts() if c.seniority == "individual" and not c.hubspot_owner_id)
    mgr = next(c for c in hs.list_contacts() if c.seniority == "manager" and not c.hubspot_owner_id)
    assert model["base"] == 0.5 and skips.likely(model, ic, None) == "likely skipped: an individual contributor, 20 of 20 skipped"
    assert skips.likely(model, mgr, None) is None                       # no notes on every lead, half of them skipped: says nothing
    # A trait with too few decisions behind it says nothing either.
    assert skips.likelihood({"seniority:individual": [19, 19]}, ic, None) is None


def test_routing_holds_a_likely_skip_with_the_reason_only_when_switched_on(world):
    path, conn, hs = world
    conn.execute("UPDATE hs_owners SET capacity_limit=0")
    owner = hs.list_owners()[0]
    router = Router(hs, conn, seed=1, exploration_share=0.0)
    pool = router.pool(clock(conn))
    place = next(i for i, c in enumerate(pool) if c.seniority == "individual")
    ic = pool[place]
    conn.execute("UPDATE hs_owners SET capacity_limit=? WHERE id=?", (place + 1, owner.id))
    conn.commit()
    now = datetime(2026, 11, 2, 9, 0)
    # Off: handed out like any other.
    router.run(now=now)
    assert conn.execute("SELECT COUNT(*) FROM held_queue WHERE reason LIKE 'likely skipped%'").fetchone()[0] == 0
    assert hs.get_contact(ic.id).hubspot_owner_id == owner.id
    conn.execute("UPDATE hs_contacts SET hubspot_owner_id=NULL, auxo_route=NULL, lifecycle_stage='marketingqualifiedlead' WHERE hubspot_owner_id=?", (owner.id,))
    conn.commit()
    # On: held with the reason, the manager's to place; a manager's release is final.
    capacity.set_assumptions(conn, skips=True)
    router = Router(hs, conn, seed=1, exploration_share=0.0)
    decisions = router.run(now=now)
    mine = [d for d in decisions if d.contact_id == ic.id]
    assert mine and mine[0].decision == "held" and mine[0].reason == "likely skipped: an individual contributor, 20 of 20 skipped"
    held = conn.execute("SELECT reason FROM held_queue WHERE contact_id=? AND released_at IS NULL", (ic.id,)).fetchone()
    assert held["reason"] == mine[0].reason and hs.get_contact(ic.id).auxo_route == "held"
    assert all(d.decision == "held" for d in decisions if hs.get_contact(d.contact_id).seniority == "individual" and d.decision != "already_owned")
    assert any(d.decision == "assigned" for d in decisions)
    client = TestClient(create_app(path, scheduler=False))
    t = client.get("/held").text
    assert "likely skipped: an individual contributor, 20 of 20 skipped" in t and "or one like the leads the SDRs skip" in t
    assert router.release_held(ic.id, owner.id, "the manager", "worth a look")
    task = [t for t in hs.list_tasks(owner_id=owner.id) if t.contact_id == ic.id][-1]      # the SDR is always told who assigned it
    assert task.subject == "Assigned by the manager from the held queue"
    assert task.body == ("the manager assigned this lead to you by hand from the held queue. It was held: likely skipped: an individual "
                         "contributor, 20 of 20 skipped. the manager's reason: worth a look")
    conn.execute("UPDATE hs_contacts SET hubspot_owner_id=NULL, auxo_route=NULL, lifecycle_stage='marketingqualifiedlead' WHERE id=?", (ic.id,))
    conn.commit()
    conn.execute("UPDATE hs_owners SET capacity_limit=capacity_limit+1 WHERE id=?", (owner.id,))     # the pass filled every slot
    conn.commit()
    again = Router(hs, conn, seed=1, exploration_share=0.0)
    again._skip_model = skips.stored(conn)
    counts, owners, _ = again.free_slots()
    assert again._assign_one(ic, owners, counts, False, now).decision == "assigned"


def test_the_skips_held_come_back_as_capacity_on_the_leadership_page(world):
    path, conn, hs = world
    assert capacity.minutes_per_lead(conn) == 30.0 and capacity.skip_figures(conn) == {"share": 0.2, "caught": 0.0, "saving": 0.0}
    skips.refresh(hs, conn)
    assert capacity.skip_figures(conn) == {"share": 1.0, "caught": 1.0, "saving": 0.0}          # counted, but off
    assert capacity.set_assumptions(conn, skips=True) == [capacity.SKIPS_AUTO]
    assert capacity.skip_saving(conn) == 3.0 and capacity.minutes_per_lead(conn) == 27.0 and round(capacity.touches_per_month(conn, 12)) == 556
    client = TestClient(create_app(path, scheduler=False))
    t = client.get("/leadership").text
    assert ">556<" in t and "less 3.0 saved on skips held" in t and 'value="auto" selected>Held for the manager' in t
    assert "routing would have held 20 of the skips and 0 of the worked: an individual contributor (20 of 20 skipped)." in t
    assert "Likely skips handed out instead, 500." in t
    r = client.post("/leadership/capacity", data={"skips": "sdr", "minutes_skip": "6"}, follow_redirects=False)
    assert r.headers["location"] == "/leadership?set=2#ld-capacity"
    t = client.get("/leadership").text
    assert ">500<" in t and "Likely skips held, 625." in t and "100 skips per 100 worked, 6 minutes each" in t
