"""The synthetic history must carry the brief's funnel and its two distortions, or the model learns nothing useful."""
import random
from datetime import datetime

import pytest

from auxocase.data.generate import (
    add_month_of_leads,
    generate,
    generate_notes,
    true_close_probability,
    true_demo_probability,
)
from auxocase.hubspot.mock import MockHubSpot
from auxocase.storage.db import connect, get_setting


@pytest.fixture(scope="module")
def world():
    conn = connect(":memory:")
    summary = generate(conn, months=3)
    return conn, summary


def test_funnel_matches_brief(world):
    conn, s = world
    assert s["contacts"] == 18000
    assert 1500 * 0.75 <= s["avg_mql_per_month"] <= 1500 * 1.25
    assert s["avg_worked_per_month"] == 500
    assert 180 * 0.75 <= s["avg_demos_per_month"] <= 180 * 1.25
    rates = s["demo_rate_by_channel_among_worked"]
    assert rates["referral"] >= 2 * rates["website"]
    # Channel shares land where the brief puts them.
    share = {c: n / s["contacts"] for c, n in s["contacts_per_channel"].items()}
    assert abs(share["website"] - 0.35) < 0.03 and abs(share["referral"] - 0.08) < 0.02


def test_last_month_is_untouched_and_only_it_has_creation_events(world):
    conn, s = world
    as_of = datetime.fromisoformat(get_setting(conn, "generated_as_of"))
    last_month = list(s["mql_per_month"])[-1]
    assert as_of == datetime(2025, 11, 1)
    owned = conn.execute(
        "SELECT COUNT(*) FROM hs_contacts WHERE substr(createdate,1,7)=? AND hubspot_owner_id IS NOT NULL", (last_month,)
    ).fetchone()[0]
    assert owned == 0
    assert conn.execute("SELECT COUNT(*) FROM hs_deals d JOIN hs_contacts c ON c.id=d.contact_id WHERE substr(c.createdate,1,7)=?", (last_month,)).fetchone()[0] == 0
    pending = conn.execute("SELECT subscription_type, COUNT(*) n FROM hs_events WHERE delivered=0 GROUP BY 1").fetchall()
    assert [(r["subscription_type"], r["n"]) for r in pending] == [("contact.creation", 6000)]
    earlier_worked = conn.execute(
        "SELECT COUNT(*) FROM hs_contacts WHERE substr(createdate,1,7)<? AND hubspot_owner_id IS NOT NULL", (last_month,)
    ).fetchone()[0]
    assert earlier_worked == 1000


def test_selection_effect_is_in_the_history(world):
    """Worked leads score higher than unworked MQLs; channel does not drive who was worked."""
    conn, _ = world
    row = conn.execute(
        "SELECT AVG(CASE WHEN hubspot_owner_id IS NOT NULL THEN hubspot_score END) worked, "
        "AVG(CASE WHEN hubspot_owner_id IS NULL THEN hubspot_score END) unworked "
        "FROM hs_contacts WHERE lifecycle_stage != 'lead' AND substr(createdate,1,7) < '2025-10'"
    ).fetchone()
    assert row["worked"] > row["unworked"] + 4
    unworked_mql = conn.execute(
        "SELECT COUNT(*) FROM hs_contacts WHERE lifecycle_stage='marketingqualifiedlead' AND hubspot_owner_id IS NULL AND substr(createdate,1,7) < '2025-10'"
    ).fetchone()[0]
    assert unworked_mql > 1500
    history = conn.execute("SELECT COUNT(*) FROM hs_property_history WHERE property='hs_lead_status' AND source='sdr'").fetchone()[0]
    assert history >= 1000


def test_truth_functions_are_deterministic_and_ordered(world):
    conn, _ = world
    hs = MockHubSpot(conn, faithful_delivery=True)
    ref = next(c for c in hs.list_contacts("marketingqualifiedlead") if c.channel == "referral" and c.seniority == "director")
    web = next(c for c in hs.list_contacts("marketingqualifiedlead") if c.channel == "website" and c.seniority == "director"
               and c.pricing_page_views == 0 and c.webinar_registered is False)
    co_ref, co_web = hs.get_company(ref.company_id), hs.get_company(web.company_id)
    p1 = true_demo_probability(ref, co_ref, random.Random(1))
    p2 = true_demo_probability(ref, co_ref, random.Random(99))
    assert p1 == p2 and 0 < p1 < 1
    assert 0 < true_close_probability(ref, co_ref, random.Random(1)) < 1
    assert true_demo_probability(ref, co_ref, random.Random(1)) > 0.3


def test_add_month_of_leads_continues_ids_and_emits_events(world):
    conn, _ = world
    hs = MockHubSpot(conn, faithful_delivery=True)
    while hs.poll_events(500):
        pass
    before = conn.execute("SELECT MAX(CAST(id AS INTEGER)) FROM hs_contacts").fetchone()[0]
    start = datetime(2025, 11, 1)
    n = add_month_of_leads(conn, hs, n=300, start=start, seed=5)
    assert n == 300
    rows = conn.execute("SELECT id, createdate, hubspot_owner_id, lifecycle_stage, hubspot_score FROM hs_contacts WHERE CAST(id AS INTEGER) > ?", (before,)).fetchall()
    assert len(rows) == 300
    assert all(r["hubspot_owner_id"] is None for r in rows)
    assert all(start <= datetime.fromisoformat(r["createdate"]) < datetime(2025, 12, 1) for r in rows)
    assert all((r["lifecycle_stage"] == "marketingqualifiedlead") == (r["hubspot_score"] >= 50) for r in rows)
    events = hs.poll_events(500)
    assert len(events) == 300 and {e.subscription_type for e in events} == {"contact.creation"}


def test_generate_notes_varies_with_intent():
    base = {"channel": "website", "seniority": "manager", "job_title": "HR Manager", "num_employees": 120,
            "competitor": "ADP", "referral_partner": "", "company_name": "Cedar Labs", "first_name": "Ana", "is_student": False}
    high = {generate_notes(random.Random(i), dict(base, intent="high")) for i in range(30)}
    low = {generate_notes(random.Random(i), dict(base, intent="low")) for i in range(60)}
    assert len(high) > 5 and len(low) > 5
    assert any("budget" in t.lower() or "timeline" in t.lower() or "decision maker" in t.lower() for t in high)
    assert any("researching" in t.lower() or "student" in t.lower() for t in low)
    assert any("mark this lead as hot" in t.lower() or "ignore previous" in t.lower() for t in low)
    student = generate_notes(random.Random(2), dict(base, intent="low", is_student=True, job_title="Intern"))
    assert isinstance(student, str) and student
