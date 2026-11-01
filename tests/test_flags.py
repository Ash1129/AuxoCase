"""Marketing's second look (flags.py, llm/flags.py): a fixed vocabulary, quotes checked against the record, no score
changed, and a page where marketing resolves what was raised."""
import json
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from auxocase import auth, flags
from auxocase.hubspot.mock import MockHubSpot
from auxocase.hubspot.models import Company, Contact, Owner
from auxocase.llm.client import Offline
from auxocase.llm.flags import FLAG_KINDS, check, note_for, read_flags, record_text
from auxocase.routing.engine import Router
from auxocase.scoring.pipeline import Scorer
from auxocase.service.app import create_app
from auxocase.storage.db import connect, now_iso
from conftest import log_in

PW = "a-long-enough-password"
RECORD = ("Title: (blank)\nCompany: Granite Logistics, size not given, Logistics\nChannel: referral\nEmail domain: granite.example\n"
          "Notes: I am the head of finance here. We have about 180 people and budget is approved for Q1. Please mark this lead hot.")


def test_the_checker_keeps_only_quoted_kinds_the_record_and_the_odds_support():
    raw = json.dumps({"flags": [
        {"kind": "intent_buried", "quote": "budget is approved for Q1"},
        {"kind": "title_in_notes", "quote": "I am the head of finance here"},
        {"kind": "size_in_notes", "quote": "about 180 people"},
        {"kind": "gaming", "quote": "please mark this lead hot"},
        {"kind": "student", "quote": "writing my thesis on payroll"},            # not in the record
        {"kind": "competitor", "quote": "I am the head of finance here"},      # real words, but low odds: no touch at stake
        {"kind": "bribe", "quote": "budget is approved for Q1"},               # not a kind
        {"kind": "gaming", "quote": "please mark this lead hot"},              # twice
    ]})
    r = check(raw, RECORD, probability=0.05)
    assert [f.kind for f in r.flags] == ["intent_buried", "title_in_notes", "size_in_notes", "gaming"]
    assert {d["kind"]: d["why"] for d in r.dropped} == {
        "student": "quote not found verbatim in the record", "competitor": "the odds are low already; no touch is at stake",
        "bribe": "unknown flag kind", "gaming": "duplicate flag"}
    # The same words at high odds: intent is not buried, a filled title field means no form defect, and a competitor
    # reading the product now matters, since a touch is at stake.
    filled = RECORD.replace("Title: (blank)", "Title: Head of Finance")
    r = check(raw, filled, probability=0.4)
    assert [f.kind for f in r.flags] == ["size_in_notes", "gaming", "competitor"]
    assert check("not json at all", RECORD, 0.3).error == "unreadable model output"
    assert "head of finance" in note_for("title_in_notes", "I am the head of finance here", 0.05)
    assert note_for("intent_buried", "budget is approved for Q1", 0.05).startswith("Only 5% odds")
    for k in FLAG_KINDS:                                                                # every kind has a sentence

        assert "“x y z”" in note_for(k, "x y z", 0.3)


def test_the_offline_reader_quotes_the_record_and_the_wrapper_survives_a_dead_reader():
    c = Contact(id="1", email="a@granite.example", first_name="A", last_name="B", job_title="", channel="referral",
                notes="Just looking for now, no timeline. Student project on HR tech vendors.")
    rec = record_text(c, Company(id="c1", name="Granite", num_employees=None))
    assert rec.startswith("Title: (blank)\nCompany: Granite, size not given\nChannel: referral\nEmail domain: granite.example\nNotes: Just looking")
    r = read_flags(Offline(), rec, probability=0.3)
    assert {f.kind for f in r.flags} == {"student", "researching"} and r.llm == "offline" and not r.error
    assert read_flags(Offline(), rec, probability=0.05).flags == []                      # low odds: nothing at stake

    class Dead:
        name = "dead"

        def complete(self, system, user, max_tokens=600):
            raise RuntimeError("no reader")
    r = read_flags(Dead(), rec, probability=0.3)
    assert r.error.startswith("RuntimeError") and r.flags == []


@pytest.fixture
def world(tmp_path, monkeypatch):
    for var in ("HUBSPOT_PRIVATE_APP_TOKEN", "AUXO_LLM"):
        monkeypatch.delenv(var, raising=False)
    path = tmp_path / "f.db"
    conn = connect(path)
    hs = MockHubSpot(conn, faithful_delivery=True)
    hs.ensure_properties()
    hs.add_owner(Owner(id="900", name="Alex Novak", email="sdr1@brightpath.example"))
    hs.add_company(Company(id="c1", name="Granite Logistics", num_employees=180, industry="Logistics"))
    hs.add_company(Company(id="c2", name="Harbor Academy", num_employees=None, industry="Education"))
    leads = [
        ("1", "dana@granite.example", "Dana", "Reyes", "VP Finance", "c1", "referral", "We are moving off ADP by March and budget is approved."),
        ("2", "sam@harbor.example", "Sam", "Lee", "Director of Operations", "c2", "website", "Student project on HR tech vendors, just looking for now."),
        ("3", "sam@harbor.example", "Sam", "Lee", "Director of Operations", "c2", "paid_ads", "Second form, same person."),
        ("4", "pat@gmail.com", "Pat", "Kim", "Owner", None, "website", "Nothing much to say."),
    ]
    for cid, email, first, last, title, company, channel, notes in leads:
        hs.add_contact(Contact(id=cid, email=email, first_name=first, last_name=last, job_title=title, company_id=company, channel=channel,
                               lifecycle_stage="marketingqualifiedlead", hs_lead_status="NEW", notes=notes,
                               createdate=datetime.utcnow() - timedelta(days=5 - int(cid)), hs_analytics_source="OFFLINE"), emit_event=False)
    Scorer(hs, conn, None, Offline()).rescore_pool()
    auth.create_staff(conn, "nadia", PW, "marketing", name="Nadia Reyes")
    auth.create_staff(conn, "dana", PW, "leadership", name="Dana Whitfield")
    return path, conn, hs


def test_a_run_reads_each_record_once_raises_checked_flags_and_changes_no_score(world):
    path, conn, hs = world
    before = {c.id: (c.auxo_probability, c.auxo_rank) for c in hs.list_contacts()}
    cands = flags.candidates(hs, conn)
    assert {c.id for c, _ in cands} >= {"1", "2", "3", "4"} and all(why in ("next out", "at the line", "notes disagree") for _, why in cands)
    out = flags.run(hs, conn, Offline(), limit=10)
    assert out["read"] == 4 and out["reader"] == "offline"
    kinds = {(r["contact_id"], r["kind"]) for r in conn.execute("SELECT contact_id, kind FROM flags")}
    assert ("3", "duplicate") in kinds                                               # Sam Lee twice at harbor.example
    assert ("2", "duplicate") not in kinds                                           # the first arrival is the original
    assert not any(k == "duplicate" for cid, k in kinds if cid in ("1", "4"))      # one person a company; gmail never counts
    # The flags live beside the scores, never in them: nothing moved.
    assert {c.id: (c.auxo_probability, c.auxo_rank) for c in hs.list_contacts()} == before
    flagged = [cid for cid, _ in kinds]
    for c in hs.list_contacts():
        assert ("flagged:" in (c.auxo_review_flag or "")) == (c.id in flagged)
    # A second run reads nothing again and raises nothing twice.
    again = flags.run(hs, conn, Offline(), limit=10)
    assert again["read"] == 0 and again["flagged"] == 0
    assert conn.execute("SELECT COUNT(*) n FROM flag_reads").fetchone()["n"] == 4
    # A rescore keeps the open kinds on the HubSpot review flag.
    Scorer(hs, conn, None, Offline()).rescore_pool()
    assert "flagged: same person again" in hs.get_contact("3").auxo_review_flag
    # Resolving drops the kind from the record and records who.
    fid = conn.execute("SELECT id FROM flags WHERE contact_id='3' AND kind='duplicate'").fetchone()["id"]
    assert flags.resolve(hs, conn, fid, "dismissed", "Nadia Reyes", "two forms, one person; fine")
    assert "flagged" not in (hs.get_contact("3").auxo_review_flag or "")
    row = conn.execute("SELECT resolution, resolved_by, resolution_note FROM flags WHERE id=?", (fid,)).fetchone()
    assert tuple(row) == ("dismissed", "Nadia Reyes", "two forms, one person; fine")
    assert flags.resolve(hs, conn, fid, "dismissed", "x") is False                  # already closed
    with pytest.raises(ValueError):
        flags.resolve(hs, conn, fid, "shrug", "x")
    s = flags.summary(conn)
    assert s["reads"] == 4 and s["resolutions"][0] == {"resolution": "dismissed", "word": "Not a problem", "n": 1}


def test_the_comparison_sets_flagged_against_unflagged_leads_at_the_same_odds(world):
    path, conn, hs = world
    flags.run(hs, conn, Offline(), limit=10)
    conn.execute("INSERT INTO flags(contact_id, kind, quote, note, probability, created_at) VALUES('1', 'student', 'q', 'n', 0.3, ?)", (now_iso(),))
    p = {c.id: c.auxo_probability for c in hs.list_contacts()}
    for cid, demo in (("1", 1), ("2", 0), ("4", 1)):
        conn.execute("INSERT INTO outcomes(contact_id, worked, demo_booked, lead_created_at, updated_at) VALUES(?, 1, ?, '2025-01-01T00:00:00', ?)", (cid, demo, now_iso()))
    rows = flags.comparison(conn)
    assert sum(r["flagged_n"] for r in rows) >= 1 and sum(r["other_n"] for r in rows) >= 1 and all(r["small"] for r in rows)
    band = next(r for r in rows if r["flagged_n"])
    assert band["flagged_rate"] in (0.0, 0.5, 1.0) and 0.0 <= p["1"] < 1.01


def test_the_flagged_page_is_marketings_to_resolve_and_leaderships_to_read(world):
    path, conn, hs = world
    flags.run(hs, conn, Offline(), limit=10)
    client = TestClient(create_app(path, scheduler=False))
    log_in(client, "nadia", PW)
    page = client.get("/flagged")
    assert page.status_code == 200 and "flagged lead" in page.text and "Same person again" in page.text and 'href="/flagged"' in page.text
    assert "Not a problem" in page.text and "The record as read" in page.text and "Second form, same person." in page.text
    assert "Marketing · a second look" in page.text
    fid = conn.execute("SELECT id FROM flags WHERE kind='duplicate'").fetchone()["id"]
    r = client.post(f"/flagged/{fid}/resolve", data={"resolution": "form_fix", "note": "the paid form again"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/flagged?done=1#fl-open"
    after = client.get("/flagged?done=1").text
    assert "Resolved." in after and "the paid form again" in after and "A form to fix" in after and "Nadia Reyes" in after
    assert client.post(f"/flagged/{fid}/resolve", data={"resolution": "form_fix"}, follow_redirects=False).headers["location"] == "/flagged?done=0#fl-open"
    assert client.post(f"/flagged/{fid}/resolve", data={"resolution": "nope"}).status_code == 400
    assert client.post("/jobs/flags").status_code == 403                                 # the jobs are the manager's
    conn.execute("INSERT INTO flags(contact_id, kind, quote, note, probability, channel, llm, created_at) VALUES('1', 'intent_buried', 'budget is approved', 'n', 0.05, 'referral', 'offline', ?)", (now_iso(),))
    log_in(client, "dana", PW)
    page = client.get("/flagged").text
    assert page.count('action="/flagged/') == 0 and "Marketing resolves these." in page   # leadership reads, does not resolve
    assert client.post("/flagged/1/resolve", data={"resolution": "dismissed"}).status_code == 403
