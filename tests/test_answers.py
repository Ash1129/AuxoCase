"""What BrightPath's follow-up answers added: loss-reason reading, enrichment, speed measured from lead creation."""
import json
from datetime import datetime, timedelta

import pytest

from auxocase.data.generate import generate
from auxocase.feedback import loop as fb
from auxocase.hubspot.mock import MockHubSpot
from auxocase.llm.client import Offline
from auxocase.llm.loss_reasons import check, read_loss_reason
from auxocase.pool import pool_contacts
from auxocase.scoring.enrich import NoEnricher, SimulatedEnricher, enrich_lead
from auxocase.scoring.pipeline import Scorer
from auxocase.storage.db import connect


# ---------------------------------------------------------------- loss reasons (answer 2)
def test_loss_reason_keeps_a_category_only_with_a_verbatim_quote():
    text = "Went with ADP -- AE"
    assert check(json.dumps({"category": "competitor", "quote": "went with ADP"}), text).category == "competitor"
    assert check(json.dumps({"category": "competitor", "quote": "they preferred Gusto"}), text).source == "unclassified"
    assert check(json.dumps({"category": "made_up", "quote": "went with ADP"}), text).source == "unclassified"
    assert check("not json at all", text).source == "unclassified"
    assert check(json.dumps({"category": "price", "quote": "x"}), "  -  ").source == "empty"


@pytest.mark.parametrize("text,category", [
    ("needs multi-entity (per AE)", "missing_feature"),
    ("$$$ too high vs current", "price"),
    ("stayed with Paychex", "competitor"),
    ("ghosted", "no_decision"),
    ("circle back next yr", "timing"),
    ("only 8 employees", "poor_fit"),
    ("see notes", "other"),
])
def test_offline_reader_reads_the_messy_reasons(text, category):
    assert read_loss_reason(Offline(), text).category == category


# ---------------------------------------------------------------- the world
@pytest.fixture(scope="module")
def world(tmp_path_factory):
    conn = connect(tmp_path_factory.mktemp("db") / "a.db")
    hs = MockHubSpot(conn, faithful_delivery=True)
    hs.ensure_properties()
    generate(conn, months=6, seed=11)
    fb.sync_outcomes(hs, conn)
    return conn, hs


def test_enrichment_asks_once_fills_what_it_finds_and_writes_it_back(world):
    conn, hs = world
    lead = next(c for c in pool_contacts(hs, conn) if not c.job_title)
    co = hs.get_company(lead.company_id)
    c1, co1, filled = enrich_lead(hs, conn, SimulatedEnricher(conn), lead, co)
    truth = conn.execute("SELECT * FROM sim_truth WHERE contact_id=?", (lead.id,)).fetchone()
    if "job_title" in filled:
        assert c1.job_title == truth["job_title"] and hs.get_contact(lead.id).job_title == truth["job_title"]
    else:
        assert not c1.job_title and not hs.get_contact(lead.id).job_title       # not found stays blank
    c2, _, filled2 = enrich_lead(hs, conn, SimulatedEnricher(conn), lead, co)  # the stale object again
    assert filled2 == filled and c2.job_title == c1.job_title
    assert conn.execute("SELECT COUNT(*) FROM enrichment_log WHERE contact_id=?", (lead.id,)).fetchone()[0] == 1
    assert enrich_lead(hs, conn, NoEnricher(), lead, co)[2] == {}


def test_scored_reason_says_what_came_from_enrichment(world):
    conn, hs = world
    pool = [c for c in pool_contacts(hs, conn) if not c.job_title][:40]
    res = Scorer(hs, conn, None, Offline(), enricher=SimulatedEnricher(conn)).score_batch(pool)
    enriched = [r for r in res if r.enriched]
    assert enriched and all("from the enrichment tool" in r.reason for r in enriched)


def test_classify_losses_is_idempotent_and_reports_by_size(world):
    conn, hs = world
    first = fb.classify_losses(hs, conn, Offline())
    assert sum(first.values()) > 20
    assert first.get("unclassified", 0) < sum(first.values()) * 0.2
    assert fb.classify_losses(hs, conn, Offline()) == {}
    report = fb.loss_report(hs, conn)
    assert sum(sum(row.values()) for row in report.values()) == sum(first.values())


def test_speed_is_measured_from_lead_creation(world):
    conn, hs = world
    s = fb.speed_to_lead(conn)
    b = s["baseline"]                                   # the history: no lead was routed by the service yet
    assert s["n"] == 0 and b["n"] > 1000
    assert 48 <= b["median_hours"] <= 96, b             # answer 4: a median of about three days
    months = fb.success_by_month(conn)
    assert months and all(m["routed_share"] == 0 for m in months)


def test_mql_gate_report_shows_referral_and_webinar_held_back(world):
    conn, hs = world
    g = {r["channel"]: r for r in fb.mql_gate_report(hs, conn)}
    assert g["referral"]["no_title"] > g["website"]["no_title"]
    assert g["webinar"]["mql_share"] < g["website"]["mql_share"]
