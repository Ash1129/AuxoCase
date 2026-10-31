"""The call brief: built from the record, checked by code, read on Opus; HubSpot's call outcome on the engagement."""
import json
from datetime import datetime

from auxocase.hubspot.models import CALL_DISPOSITIONS, Engagement
from auxocase.llm.calls import MAX_VOICEMAIL_WORDS, OBJECTIONS, check, draft_call, objection_for, template_brief
from auxocase.llm.client import ClaudeCLI, Offline
from auxocase.llm.emails import history_lines

NOTES = "Payroll takes two days a month by hand. We need this live before our Q3 board meeting."
SIGNALS = [{"type": "problem", "quote": "payroll takes two days a month by hand"}, {"type": "timeline", "quote": "live before our Q3 board meeting"}]
DRIVERS = [("has_competitor", -0.46), ("channel=website", -0.38), ("pricing_page_views", 0.23), ("num_form_submissions", 0.18), ("seniority=vp", 0.17)]
LOSSES = {"missing_feature": {"300_500": 3, "over_500": 9}, "price": {"under_50": 5, "50_149": 2}, "timing": {"150_299": 1}}
KW = dict(first_name="Jordan", job_title="VP Finance", company="Atlas Labs", channel="website", referral_partner="", reason="Ranked up because it visited the pricing page.",
          signals=SIGNALS, notes=NOTES, sender="Alex", drivers=DRIVERS, size_band="300_500", loss_mix=LOSSES, booking_link="https://meetings.example/alex")


class Fake:
    name = "fake"

    def __init__(self, answer):
        self.answer, self.prompts = answer, []

    def complete(self, system, user, max_tokens=600):
        self.prompts.append(user)
        return self.answer


def good():
    return {"opener": "Hi Jordan, you looked at our pricing page this week, so I wanted to ask one thing about payroll at Atlas Labs.",
            "questions": ["Which provider runs payroll today, and when does that contract renew?", "What prompted the look at pricing?"],
            "objection": {"say": "You don't integrate with our systems.", "answer": "Ask which systems payroll has to talk to, and take the list to the demo."},
            "voicemail": "Hi Jordan, this is Alex from BrightPath Payroll. You looked at our pricing page this week and I had one question about payroll at Atlas Labs. I'll follow up by email. Thanks, Jordan."}


def test_template_brief_asks_about_what_moved_the_score_and_the_losses_of_its_size_band():
    b = template_brief("Jordan", "Atlas Labs", "website", "", SIGNALS, "Alex", DRIVERS, "300_500", LOSSES)
    assert b.opener.startswith("Hi Jordan, this is Alex") and '"payroll takes two days a month by hand"' in b.opener
    assert b.topics == ["has_competitor", "pricing_page_views", "num_form_submissions"]      # biggest effects with a question, in order
    assert len(b.questions) == 3 and "renewal" in b.questions[0]
    assert b.objection_category == "missing_feature" and (b.objection, b.answer) == OBJECTIONS["missing_feature"]
    assert len(b.voicemail.split()) <= MAX_VOICEMAIL_WORDS and "Jordan" in b.voicemail and "Alex" in b.voicemail
    assert b.source == "template"


def test_objection_falls_back_from_the_band_to_overall_to_the_default():
    assert objection_for("300_500", LOSSES) == "missing_feature"
    assert objection_for("unknown", LOSSES) == "missing_feature"       # no losses in that band: the biggest overall
    assert objection_for("unknown", {}) == "competitor" and objection_for("under_50", None) == "competitor"


def test_too_few_drivers_fill_from_the_fallback_questions():
    b = template_brief("Sam", "", "paid_ads", "", [], "Alex", [("channel=paid_ads", -0.1)], "unknown", None)
    assert len(b.questions) == 2 and b.questions[0].startswith("How is payroll run today")


def test_offline_brief_carries_the_booking_link_in_the_ask():
    b = draft_call(Offline(), **KW)
    assert b.source == "template" and b.ask.endswith("https://meetings.example/alex") and b.llm == "offline"


def test_llm_brief_is_kept_when_it_passes_the_checks_and_runs_as_the_call_brief_task():
    llm = Fake(json.dumps(good()))
    b = draft_call(llm, **KW)
    assert b.source == "llm" and b.rejected == "" and len(b.questions) == 2
    assert b.ask.endswith("https://meetings.example/alex")           # the link is added by code, never by the model
    assert llm.prompts[0].startswith("TASK: call_brief\n")
    assert "Internal driver, Against: the company already uses a competing payroll vendor" in llm.prompts[0]
    assert "missing feature" in llm.prompts[0]


def test_llm_brief_is_replaced_by_the_template_when_it_breaks_a_rule():
    cases = {
        "link": (lambda d: d.update(voicemail=d["voicemail"] + " Book at https://x.example"), "link"),
        "phone": (lambda d: d.update(voicemail=d["voicemail"] + " Call me on 415 555 0100."), "phone number"),
        "quote": (lambda d: d.update(opener='Hi Jordan, you said "we are leaving ADP next week".'), "quotes words not in the notes"),
        "score": (lambda d: d.update(questions=["Your score is high; why?", "What runs payroll today?"]), "score"),
        "driver": (lambda d: d.update(questions=["Our drivers say you use a competitor?", "What runs payroll today?"]), "score"),
        "name": (lambda d: d.update(voicemail="This is Alex from BrightPath Payroll. I'll email you."), "by name"),
        "spoke": (lambda d: d.update(opener="Hi Jordan, as we discussed, payroll at Atlas Labs."), "conversation"),
        "count": (lambda d: d.update(questions=["Only one?"]), "questions"),
        "long": (lambda d: d.update(voicemail="Jordan " * 70), "voicemail too long"),
        "number": (lambda d: d.update(opener="Hi Jordan, you have 240 staff on payroll at Atlas Labs."), "number not in the facts"),
    }
    for name, (mutate, why) in cases.items():
        d = good()
        mutate(d)
        b = draft_call(Fake(json.dumps(d)), **KW)
        assert b.source == "template" and why in b.rejected, (name, b.rejected)


def test_unreadable_answer_falls_back_to_the_template():
    b = draft_call(Fake("Sure! Here is the brief: ..."), **KW)
    assert b.source == "template" and b.rejected.startswith("no usable answer")


def test_check_accepts_a_prior_connected_call_as_a_conversation():
    d = good()
    d["opener"] = "Hi Jordan, following our call last week about payroll at Atlas Labs."
    ok, why = check(d, "Jordan", NOTES, "Lead first name: Jordan\nAtlas Labs", connected=True)
    assert ok, why


def test_the_call_brief_runs_on_the_draft_model():
    cli = ClaudeCLI(model="sonnet", draft_model="opus")
    assert cli.model_for("call_brief") == "opus" and cli.model_for("outreach_email") == "opus" and cli.model_for("reason") == "sonnet"


def test_history_tells_the_follow_up_drafter_what_the_call_left_behind():
    now = datetime(2028, 10, 30)
    e = Engagement(id="e1", contact_id="c", type="CALL", direction="OUTGOING", timestamp=datetime(2028, 10, 29), disposition="left_voicemail")
    assert history_lines([e], now) == ["Call yesterday: no answer, the rep left a voicemail"]
    assert set(CALL_DISPOSITIONS) == {"connected", "no_answer", "left_voicemail", "left_live_message", "busy", "wrong_number"}


def test_cli_records_the_model_that_carried_the_cost_not_the_cli_side_call(tmp_path, monkeypatch):
    import json as _json
    import subprocess
    from types import SimpleNamespace
    from auxocase.llm import client as cl
    from auxocase.storage.db import connect
    out = {"result": "{}", "total_cost_usd": 0.0271, "duration_api_ms": 10180, "usage": {"input_tokens": 2, "output_tokens": 799},
           "modelUsage": {"claude-haiku-4-5-20251001": {"costUSD": 0.0004}, "claude-opus-5-5": {"costUSD": 0.0267}}}
    monkeypatch.setattr(cl.shutil, "which", lambda name: "/usr/bin/claude")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=_json.dumps(out), stderr="", returncode=0))
    conn = connect(tmp_path / "u.db")
    cl.ClaudeCLI(conn=conn).complete("system", "TASK: call_brief\nFACTS:\nx")
    row = conn.execute("SELECT model, task, cost_usd FROM llm_usage").fetchone()
    assert (row["model"], row["task"], row["cost_usd"]) == ("claude-opus-5-5", "call_brief", 0.0271)
