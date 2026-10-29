"""Phase 3: the automated sequence for leads below the capacity line. Off by default; an experiment with two arms
drawn from the same slice; emailed leads leave the pool, a reply hands the lead to the SDR it heard from, an
unanswered sequence returns it; the band is refused until labels mature; the training table leaves emailed leads
out; the cost report prices a demo by route, from metered calls when any ran."""
import json
import random
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from auxocase import costs
from auxocase.data.generate import generate
from auxocase.feedback import loop as fb
from auxocase.hubspot.mock import MockHubSpot
from auxocase.llm import usage
from auxocase.llm.client import Offline
from auxocase.routing import tier2
from auxocase.routing.engine import REPLY_REASON, Router, priority
from auxocase.scoring.pipeline import Scorer
from auxocase.service.app import create_app
from auxocase.storage.db import clock, connect, set_setting


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    path = tmp_path_factory.mktemp("base") / "base.db"
    conn = connect(path)
    hs = MockHubSpot(conn, faithful_delivery=True)
    hs.ensure_properties()
    generate(conn, months=8, seed=5)
    fb.sync_outcomes(hs, conn)
    Scorer(hs, conn, None, Offline()).rescore_pool()
    conn.execute("UPDATE hs_contacts SET hs_lead_status='IN_PROGRESS' WHERE hs_lead_status IN ('NEW','OPEN') AND hubspot_owner_id IS NOT NULL")
    Router(hs, conn).run()          # fills every slot and stores the capacity line
    conn.close()
    return path


@pytest.fixture
def world(base, tmp_path):
    """A copy of the base with every SDR full, so nothing below the line is reached by routing."""
    path = tmp_path / "w.db"
    src = connect(base)
    src.execute(f"VACUUM INTO '{path}'")
    src.close()
    conn = connect(path)
    hs = MockHubSpot(conn, faithful_delivery=True)
    return path, conn, hs


def _rows(conn, **where):
    cond = " AND ".join(f"{k}=?" for k in where) or "1=1"
    return [dict(r) for r in conn.execute(f"SELECT * FROM tier2 WHERE {cond}", tuple(where.values()))]


def test_off_by_default_nothing_is_drawn_and_eligibility_is_below_the_line(world):
    _, conn, hs = world
    assert tier2.send_share(conn) == 0
    assert tier2.run_tier2(hs, conn, Offline())["emailed"] == 0 and not _rows(conn)
    pool = Router(hs, conn).pool()
    elig = tier2.eligible(hs, conn)
    assert elig and len(elig) < len(pool)
    line = json.loads(conn.execute("SELECT value FROM settings WHERE key='capacity_line_probability'").fetchone()["value"])
    now = clock(conn)
    assert all(priority(conn, c.auxo_probability, c.createdate, now) < line for c in elig) and all(c.email for c in elig)
    assert all(now - c.createdate <= timedelta(days=tier2.DEFAULT_MAX_AGE_DAYS) for c in elig)
    assert [c.id for c in elig] == [c.id for c in pool if c.id in {e.id for e in elig}]     # best first, the pool's order


def test_explore_draws_both_arms_from_the_same_slice_emails_from_an_sdr_inbox_and_leaves_the_pool(world):
    _, conn, hs = world
    tier2.switch(conn, explore=0.5)
    set_setting(conn, tier2.DAILY_LIMIT, "10")
    before = len(Router(hs, conn).pool())
    out = tier2.run_tier2(hs, conn, Offline(), rng=random.Random(3))
    emailed, control = _rows(conn, arm="emailed"), _rows(conn, arm="control")
    assert out["emailed"] == len(emailed) <= 10 and control and out["deferred"] > 0
    # Both arms come from the top of the eligible slice, not the emailed arm from the top and the control from anywhere.
    drawn = {r["contact_id"] for r in emailed + control}
    assert drawn and all(r["probability"] is not None and r["rank"] is not None and r["line_probability"] is not None for r in emailed + control)
    now = clock(conn)
    value = lambda c: priority(conn, c.auxo_probability, c.createdate, now)
    lowest_drawn = min(value(hs.get_contact(cid)) for cid in drawn)
    assert all(value(c) <= lowest_drawn + 1e-9 for c in tier2.eligible(hs, conn))      # what is left is below every drawn lead
    for r in emailed:
        c = hs.get_contact(r["contact_id"])
        assert r["status"] == "sent" and r["owner_id"] and r["subject"] and c.auxo_route == "automated" and not c.hubspot_owner_id
        assert c.auxo_email_subject == r["subject"]
        sent = [e for e in hs.engagements_for_contact(c.id) if e.type == "EMAIL" and e.direction == "OUTGOING"]
        assert len(sent) == 1 and sent[0].owner_id == r["owner_id"]                       # the mock logged the send
    assert len({r["owner_id"] for r in emailed}) > 1                                       # senders rotate
    assert len(Router(hs, conn).pool()) == before - len(emailed)                           # emailed leads leave the pool
    assert all(c.id not in {r["contact_id"] for r in emailed} for c in tier2.eligible(hs, conn))   # drawn once
    tier2.run_tier2(hs, conn, Offline(), rng=random.Random(4))                             # a later pass the same day
    assert len(_rows(conn, arm="emailed")) <= 10                                            # never past the day's limit
    set_setting(conn, tier2.DAILY_LIMIT, str(len(_rows(conn, arm="emailed"))))
    assert tier2.run_tier2(hs, conn, Offline(), rng=random.Random(5))["emailed"] == 0      # the day's limit is spent
    assert not hs.sequence_enrollments(emailed[0]["contact_id"])                           # no sequence set: nothing enrolled
    assert tier2.readiness(conn)["sequence"] is None


def test_a_reply_hands_the_lead_to_the_sender_and_an_unanswered_sequence_returns_it(world):
    _, conn, hs = world
    tier2.switch(conn, explore=1.0)
    set_setting(conn, tier2.DAILY_LIMIT, "3")
    tier2.run_tier2(hs, conn, Offline(), rng=random.Random(1))
    a, b, c3 = _rows(conn, status="sent")
    owners = hs.list_owners()
    conn.execute("UPDATE hs_owners SET capacity_limit=0")
    sender = next(o for o in owners if o.id == a["owner_id"])
    conn.execute("UPDATE hs_owners SET capacity_limit=99 WHERE id=?", (sender.id,))
    conn.execute("UPDATE hs_contacts SET hs_lead_status='IN_PROGRESS' WHERE hubspot_owner_id=? AND hs_lead_status IN ('NEW','OPEN')", (sender.id,))
    now = clock(conn)
    # Someone else's outgoing mail, and the lead's own reply to a different lead, are not this lead's reply.
    hs.log_engagement(a["contact_id"], "EMAIL", "OUTGOING", now, sender.id, subject="x")
    assert tier2.detect_replies(hs, conn, now=now)["replied"] == 0
    hs.log_engagement(a["contact_id"], "EMAIL", "INCOMING", now, None, subject="Re: " + a["subject"])
    out = tier2.detect_replies(hs, conn, now=now)
    assert out["replied"] == 1 and out["assigned"] == 1
    c = hs.get_contact(a["contact_id"])
    assert c.hubspot_owner_id == sender.id and c.hs_lead_status == "NEW" and c.lifecycle_stage == "salesqualifiedlead"
    row = _rows(conn, contact_id=a["contact_id"])[0]
    assert row["status"] == "replied" and row["replied_at"] and row["assigned_at"]
    log = conn.execute("SELECT * FROM routing_log WHERE contact_id=? ORDER BY id DESC LIMIT 1", (a["contact_id"],)).fetchone()
    assert log["decision"] == "assigned" and log["reason"] == REPLY_REASON and log["owner_id"] == sender.id
    task = [t for t in hs.list_tasks(owner_id=sender.id) if t.contact_id == a["contact_id"]]
    assert task and task[0].subject.startswith("Replied to your automated email")
    assert tier2.detect_replies(hs, conn, now=now)["replied"] == 0                        # a reply counts once
    # The sender is full now: the next reply goes to the held queue rather than nowhere.
    conn.execute("UPDATE hs_owners SET capacity_limit=0")
    hs.log_engagement(b["contact_id"], "EMAIL", "INCOMING", now + timedelta(minutes=1), None, subject="Re: hi")
    out = tier2.detect_replies(hs, conn, now=now + timedelta(minutes=1))
    assert out["replied"] == 1 and out["held"] == 1
    held = conn.execute("SELECT reason FROM held_queue WHERE contact_id=? AND released_at IS NULL", (b["contact_id"],)).fetchone()
    assert held and REPLY_REASON in held["reason"]
    # Warm beats ranked: the replied lead is first in the pool, so the next slot that frees is its, held or not.
    assert Router(hs, conn).pool()[0].id == b["contact_id"]
    conn.execute("UPDATE hs_owners SET capacity_limit=1 WHERE id=?", (owners[1].id,))
    conn.execute("UPDATE hs_contacts SET hs_lead_status='IN_PROGRESS' WHERE hubspot_owner_id=? AND hs_lead_status IN ('NEW','OPEN')", (owners[1].id,))
    d = [x for x in Router(hs, conn, exploration_share=0.0).run(now=now + timedelta(minutes=2)) if x.decision == "assigned"]
    assert [x.contact_id for x in d] == [b["contact_id"]] and d[0].owner_id == owners[1].id
    assert conn.execute("SELECT released_by FROM held_queue WHERE contact_id=?", (b["contact_id"],)).fetchone()["released_by"] == "routing"
    # The third runs its course: back to the pool, status no_reply.
    later = now + timedelta(days=tier2.DEFAULT_SEQUENCE_DAYS + 1)
    assert tier2.expire(conn, later) == 1
    assert _rows(conn, contact_id=c3["contact_id"])[0]["status"] == "no_reply"
    assert c3["contact_id"] not in {r["contact_id"] for r in _rows(conn, status="sent")}
    # Outcome sync labels the replied, SDR-worked lead like any other; the emailed arm stays out of the training table.
    hs.simulate_sdr_activity(a["contact_id"], sender.id, "demo_booked", later)
    fb.sync_outcomes(hs, conn)
    assert conn.execute("SELECT demo_booked FROM outcomes WHERE contact_id=?", (a["contact_id"],)).fetchone()["demo_booked"] == 1
    table = fb.build_training_table(hs, conn)
    assert not table.contact_ids.isin({a["contact_id"], b["contact_id"], c3["contact_id"]}).any()


def test_the_band_waits_for_matured_labels_and_readiness_compares_the_arms(world):
    _, conn, hs = world
    with pytest.raises(ValueError, match="matured labels"):
        tier2.switch(conn, band=True)
    with pytest.raises(ValueError, match="between 0 and 1"):
        tier2.switch(conn, explore=1.5)
    tier2.switch(conn, explore=0.5)
    set_setting(conn, tier2.DAILY_LIMIT, "40")
    tier2.run_tier2(hs, conn, Offline(), rng=random.Random(2))
    r = tier2.readiness(conn)
    assert r["arms"]["emailed"]["leads"] and r["arms"]["control"]["leads"] and r["arms"]["emailed"]["matured"] == 0 and not r["ready"]
    # Thirty days on, one emailed lead booked (reply, SDR, demo); the control arm's labels matured with nothing booked.
    em = _rows(conn, arm="emailed")[0]
    owner = hs.list_owners()[0]
    hs.simulate_sdr_activity(em["contact_id"], owner.id, "demo_booked", clock(conn))
    fb.sync_outcomes(hs, conn)
    later = clock(conn) + timedelta(days=31)
    r = tier2.readiness(conn, now=later)
    e, c = r["arms"]["emailed"], r["arms"]["control"]
    assert e["matured"] == e["leads"] and e["booked"] == 1 and e["observed_rate"] == round(1 / e["leads"], 3)
    assert e["predicted_rate"] is not None and c["observed_rate"] == 0 and r["lift"] == e["observed_rate"]
    assert r["ready"] == (e["matured"] >= tier2.TIER2_READY_MATURED)
    status = tier2.switch(conn, band=True, force=True)
    assert status["band"] and tier2.send_share(conn) == 1 - tier2.DEFAULT_HOLDBACK
    tier2.switch(conn, band=False)
    assert tier2.send_share(conn) == 0.5


def test_cost_report_prices_each_route_and_takes_metered_drafts_over_the_assumption(world):
    _, conn, hs = world
    tier2.switch(conn, explore=1.0)
    set_setting(conn, tier2.DAILY_LIMIT, "4")
    tier2.run_tier2(hs, conn, Offline(), rng=random.Random(5))
    emailed = _rows(conn, arm="emailed")
    owner = hs.list_owners()[0]
    conn.execute("UPDATE hs_owners SET capacity_limit=99 WHERE id=?", (owner.id,))
    now = clock(conn)
    hs.log_engagement(emailed[0]["contact_id"], "EMAIL", "INCOMING", now, None, subject="Re: x")
    tier2.detect_replies(hs, conn, now=now)
    hs.simulate_sdr_activity(emailed[0]["contact_id"], owner.id, "demo_booked", now)
    # An SDR works two leads routing gave them: one books. These are the ranked route's numbers.
    routed = conn.execute("SELECT contact_id, owner_id FROM routing_log WHERE decision='assigned' AND reason != ? ORDER BY id LIMIT 2",
                          (REPLY_REASON,)).fetchall()
    hs.simulate_sdr_activity(routed[0]["contact_id"], routed[0]["owner_id"], "demo_booked", now)
    hs.simulate_sdr_activity(routed[1]["contact_id"], routed[1]["owner_id"], "junk", now)
    fb.sync_outcomes(hs, conn)
    c = costs.cost_report(conn)
    u = c["units"]
    au, sd = c["automated"], c["sdr"]
    assert au["emailed"] == 4 and au["replied"] == 1 and au["demos"] == 1
    # No call has been metered yet: every draft (the template's stand in for Claude's) is priced at the assumed unit cost.
    assert au["sdr_hours"] == round(u["sdr_minutes_reply"] / 60, 1) and not au["draft_cost_metered"] and au["draft_pricing"] == "assumed"
    assert au["draft_cost"] == round(4 * u["cost_draft"], 2) and au["drafts_metered"] == 0
    assert au["cost"] == round(4 * u["cost_draft"] + u["sdr_minutes_reply"] / 60 * u["cost_sdr_hour"]) and au["cost_per_demo"] == au["cost"]
    assert c["since"] == min(r["created_at"] for r in _rows(conn))
    assert sd["worked"] == 2 and sd["demos"] == 1 and sd["demo_rate"] == 0.5 and sd["cost"] == round(2 * u["sdr_minutes_touch"] / 60 * u["cost_sdr_hour"])
    assert sd["sdr_hours"] == round(2 * u["sdr_minutes_touch"] / 60, 1) and sd["cost_per_demo"] == sd["cost"] and sd["value"] == 4800
    assert c["value_per_demo"] == 4800 and au["value"] == 4800 and c["ai"]["source"].startswith("assumed")
    # The replied lead was worked by an SDR, but through the sequence: it is the sequence's demo, not a ranked touch's.
    assert emailed[0]["contact_id"] not in {r["contact_id"] for r in conn.execute(
        "SELECT contact_id FROM routing_log WHERE decision='assigned' AND COALESCE(reason, '') != ?", (REPLY_REASON,))}
    # Metered drafts: two priced calls for emailed leads, and the report prices drafting from them.
    for cid, cost in ((emailed[0]["contact_id"], 0.004), (emailed[1]["contact_id"], 0.006)):
        with usage.reference(cid):
            usage.record(conn, "claude-cli", "claude-haiku-5-5", "outreach_email", {"input_tokens": 1500, "output_tokens": 200}, cost, 900)
    usage.record(conn, "claude-cli", "claude-haiku-5-5", "signals", {"input_tokens": 900, "output_tokens": 80}, 0.002, 500)
    c = costs.cost_report(conn)
    assert c["ai"]["calls"] == 3 and c["ai"]["cost_usd"] == 0.012 and c["ai"]["draft_mean_cost"] == 0.005 and c["ai"]["drafts_priced"] == 2
    # Two drafts at their metered cost (0.004 + 0.006), the other two at the metered mean (0.005 each).
    assert c["automated"]["draft_cost_metered"] and c["automated"]["drafts_metered"] == 2 and c["automated"]["draft_pricing"] == "metered mean"
    assert c["automated"]["draft_cost"] == 0.02 and c["automated"]["draft_unit_cost"] == 0.005
    assert [t["task"] for t in c["ai"]["by_task"]][0] == "outreach_email"
    set_setting(conn, "cost_sdr_hour", "60")
    assert costs.cost_report(conn)["units"]["cost_sdr_hour"] == 60.0


def test_the_cli_reader_tags_calls_for_revenium_and_records_their_metered_cost(world, monkeypatch):
    _, conn, hs = world
    import subprocess
    from types import SimpleNamespace
    from auxocase.llm import client
    seen = {}

    def fake_run(cmd, **kw):
        seen.update(kw, cmd=cmd)
        return SimpleNamespace(stdout=json.dumps({"result": "ok", "is_error": False, "total_cost_usd": 0.00092, "duration_api_ms": 1078,
                                                  "usage": {"input_tokens": 620, "output_tokens": 60, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
                                                  "modelUsage": {"claude-opus-5-5": {"costUSD": 0.00092}}}), stderr="", returncode=0)

    monkeypatch.setattr(client.shutil, "which", lambda _: "/usr/bin/claude")
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setenv("OTEL_RESOURCE_ATTRIBUTES", "revenium.middleware.source=revenium-cli,user.email=a@b.c,revenium.task.type=stale")
    llm = client.make_llm("cli", conn=conn)
    with usage.reference("333"):
        assert llm.complete("sys", "TASK: outreach_email\nFACTS: ...") == "ok"
    attrs = dict(kv.split("=", 1) for kv in seen["env"]["OTEL_RESOURCE_ATTRIBUTES"].split(","))
    assert attrs == {"revenium.middleware.source": "revenium-cli", "user.email": "a@b.c", "revenium.product.name": "AuxoCase",
                     "revenium.task.type": "outreach_email", "revenium.agent.name": "auxocase", "product.name": "AuxoCase"}
    row = conn.execute("SELECT * FROM llm_usage ORDER BY id DESC LIMIT 1").fetchone()
    assert row["task"] == "outreach_email" and row["reference"] == "333" and row["cost_usd"] == 0.00092 and row["input_tokens"] == 620
    assert row["model"] == "claude-opus-5-5" and row["reader"] == "claude-cli"
    assert seen["cmd"][seen["cmd"].index("--model") + 1] == "opus"        # a lead reads the draft: Opus
    assert usage.task_of("no marker") == "unknown" and usage.current_reference() == ""
    # Without a connection nothing is recorded, and an offline reader never is.
    client.make_llm("cli").complete("sys", "TASK: reason\n")
    assert conn.execute("SELECT COUNT(*) n FROM llm_usage").fetchone()["n"] == 1
    assert seen["cmd"][seen["cmd"].index("--model") + 1] == "sonnet"      # a short checked answer: Sonnet
    # The CLI runs on the owner's plan, Sonnet or Opus only.
    with pytest.raises(ValueError):
        client.ClaudeCLI(model="haiku")
    with pytest.raises(ValueError):
        client.ClaudeCLI(draft_model="claude-haiku-4-5")
    assert client.ClaudeCLI(model="claude-sonnet-5-5", draft_model="opus").model_for("signals") == "claude-sonnet-5-5"


def test_revenium_read_back_needs_a_read_key_and_never_raises(monkeypatch):
    from datetime import datetime
    monkeypatch.delenv("REVENIUM_API_KEY", raising=False)
    out = costs.revenium_cost(datetime(2030, 1, 1), datetime(2030, 1, 2))
    assert "REVENIUM_API_KEY" in out["error"]
    assert "only send" in costs.revenium_cost(datetime(2030, 1, 1), datetime(2030, 1, 2), api_key="rev_mk_x_y")["error"]
    import urllib.request

    def fake_open(req, timeout=0):
        class R:
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
            def read(self):
                name = req.full_url.split("/")[-1].split("?")[0]
                group = "AuxoCase" if "product" in name else "outreach_email"
                return json.dumps({"_embedded": {"items": [{"groupName": group, "metrics": [
                    {"metricType": "COST_METRIC", "metricResult": 1.25}, {"metricType": "runs", "metricResult": 40}]}]}}).encode()
        assert req.get_header("Authorization") == "Bearer rev_rk_t_s" and "startDate=2030-01-01T00%3A00%3A00Z" in req.full_url
        return R()

    monkeypatch.setattr(urllib.request, "urlopen", fake_open)
    out = costs.revenium_cost(datetime(2030, 1, 1), datetime(2030, 1, 2), api_key="rev_rk_t_s")
    assert out["product_cost_usd"] == 1.25 and out["by_task"]["outreach_email"] == {"cost_usd": 1.25, "runs": 40}

    def broken(req, timeout=0):
        raise OSError("no network")

    monkeypatch.setattr(urllib.request, "urlopen", broken)
    assert "OSError" in costs.revenium_cost(datetime(2030, 1, 1), datetime(2030, 1, 2), api_key="rev_rk_t_s")["error"]


def test_the_pages_show_where_a_lead_stands_and_the_route_job_runs_the_band(world, monkeypatch):
    path, conn, hs = world
    monkeypatch.setenv("AUXO_LLM", "offline")
    monkeypatch.setenv("AUXO_SCHEDULER", "off")
    tier2.switch(conn, explore=1.0)
    set_setting(conn, tier2.DAILY_LIMIT, "2")
    conn.close()
    with TestClient(create_app(path)) as client:
        out = client.post("/jobs/route").json()
        assert out["automated"]["emailed"] == 2
        c2 = connect(path)
        sent = _rows(c2, status="sent")
        page = client.get(f"/lead/{sent[0]['contact_id']}").text
        assert "In the automated sequence since" in page and "phase 3" in page
        # The mock's "lead replies" button is the reply: the lead lands with the sender.
        c2.execute("UPDATE hs_owners SET capacity_limit=99")
        resp = client.post(f"/lead/{sent[0]['contact_id']}/hubspot", data={"kind": "reply"}, follow_redirects=False)
        assert resp.status_code == 303
        page = client.get(f"/lead/{sent[0]['contact_id']}").text
        assert "Replied to the automated email" in page and _rows(c2, contact_id=sent[0]["contact_id"])[0]["status"] == "replied"
        lead = client.get("/leadership").text
        assert "phase 3" in lead
