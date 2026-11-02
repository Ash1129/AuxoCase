"""The verdict under a candidate's sign-off (verdicts.py, llm/verdict.py): the rules decide, the reader only words it,
and a sentence with a number the facts do not hold is dropped."""
import json

from fastapi.testclient import TestClient

from auxocase import auth, verdicts
from auxocase.hubspot.mock import MockHubSpot
from auxocase.hubspot.models import Owner
from auxocase.llm.client import Offline
from auxocase.llm.verdict import Facts, check, facts_for, read_verdict, rules, sentence_for
from auxocase.service.app import create_app
from auxocase.storage.db import connect, dumps
from conftest import log_in

LIVE = {"rows": 5404, "positives": 1880, "base_rate": 0.358, "precision_at_k": 0.4482, "hubspot_precision_at_k": 0.3746,
        "points_precision_at_k": 0.4448, "lift_at_k": 1.196, "lift_over_points": 1.008, "auc_pr": 0.4456, "ece": 0.0068, "k": 299,
        "calibration_bins": [{"lo": 0.3, "hi": 0.4, "n": 501, "predicted": 0.351, "observed": 0.349}]}
GOOD = {"rows": 5595, "positives": 2550, "base_rate": 0.543, "precision_at_k": 0.6902, "hubspot_precision_at_k": 0.5593,
        "points_precision_at_k": 0.6985, "lift_at_k": 1.234, "lift_over_points": 0.988, "auc_pr": 0.6775, "ece": 0.014, "k": 481,
        "test_rows": 1652, "test_months": ["2026-07", "2026-08"],
        "calibration_bins": [{"lo": 0.5, "hi": 0.6, "n": 595, "predicted": 0.551, "observed": 0.536}]}
OFF = dict(GOOD, lift_at_k=1.26, ece=0.1267, rows=6248,                 # ranks best, numbers off by 12.7 points
           calibration_bins=[{"lo": 0.4, "hi": 0.5, "n": 571, "predicted": 0.445, "observed": 0.585}])
WASH = dict(GOOD, lift_at_k=1.198, ece=0.009)                          # a tie on lift


def test_the_rules_approve_better_send_back_untrusted_numbers_and_send_back_a_wash():
    f = facts_for("demo-v6", GOOD, "demo-v1", LIVE)
    assert (f.more_demos, f.live_more_demos, f.says, f.books, f.off, f.live_off, f.leads, f.live_leads) == (23, 20, 55, 54, 1, 1, 5595, 5404)
    assert "lift" not in f.plain() and f.lift == 1.234                            # the reader never sees the raw figures
    v, reasons = rules(f)
    assert v == "approve" and len(reasons) == 3
    assert sentence_for(v, reasons) == ("Approve: its top picks book 23 percent more demos than HubSpot's list would, where demo-v1's 20 percent; "
                                        "when it says a lead is 55 percent, about 54 percent of those leads book, so the number on a lead can be believed; "
                                        "and it learned from more worked leads, 5,595 against 5,404.")
    v, reasons = rules(facts_for("demo-v7", OFF, "demo-v1", LIVE))
    assert v == "send_back" and reasons[0] == ("when it says a lead is 45 percent, about 59 percent of those leads actually book, so the number on every "
                                               "lead would be off by about 13 points where demo-v1's is off by 1")
    assert reasons[1].startswith("that outweighs its top picks book 26 percent more demos")
    v, reasons = rules(facts_for("demo-v2", WASH, "demo-v1", LIVE))
    assert v == "send_back" and reasons[0].startswith("its top picks book no more demos than demo-v1's") and "reshuffle every queue for nothing" in reasons[0]
    v, reasons = rules(facts_for("demo-v9", {}, "demo-v1", LIVE))
    assert v == "send_back" and "not recorded" in reasons[0]


def test_the_checker_keeps_one_short_plain_sentence_with_only_the_facts_numbers():
    f = facts_for("demo-v6", GOOD, "demo-v1", LIVE)
    ok = "Approve demo-v6: its top picks book 23 percent more demos than HubSpot's list would, where demo-v1's book 20 percent more, and it learned from 5,595 worked leads"
    assert check(ok, "approve", f) == ok + "."
    assert check("Send back: the number on a lead is off by 1 point.", "approve", f) is None               # wrong verdict word
    assert check("Approve: it books 3.2 percent more demos than demo-v1.", "approve", f) is None          # 3.2 is not a fact
    assert check("Approve: its lift is 1.234 against 1.196.", "approve", f) is None                        # a raw figure, and jargon
    assert check("Approve: the model is well calibrated.", "approve", f) is None                           # jargon
    assert check("Approve " + "very " * 70 + "good.", "approve", f) is None                               # too long
    assert check("", "approve", f) is None
    v, reasons = rules(f)
    s, err = read_verdict(Offline(), v, reasons, f)
    assert s.startswith("Approve: its top picks book 23 percent more demos") and not err

    class Dead:
        name = "dead"

        def complete(self, system, user, max_tokens=600):
            raise RuntimeError("no reader")
    s, err = read_verdict(Dead(), v, reasons, f)
    assert s is None and err.startswith("RuntimeError")


def _registry(conn, rows):
    for version, status, metrics, trained in rows:
        conn.execute("INSERT INTO model_registry(version, family, trained_at, metrics, status) VALUES(?,?,?,?,?)",
                     (version, "lr", trained, dumps(metrics), status))


def test_a_run_reads_each_candidate_once_and_the_page_words_the_rules_until_then(tmp_path, monkeypatch):
    for var in ("HUBSPOT_PRIVATE_APP_TOKEN", "AUXO_LLM"):
        monkeypatch.delenv(var, raising=False)
    path = tmp_path / "v.db"
    conn = connect(path)
    hs = MockHubSpot(conn)
    hs.ensure_properties()
    hs.add_owner(Owner(id="900", name="Alex Novak", email="sdr1@brightpath.example"))
    _registry(conn, [("demo-v1", "live", LIVE, "2026-05-03T00:00:00"), ("demo-v6", "candidate", GOOD, "2026-10-02T00:00:00"),
                     ("demo-v7", "candidate", OFF, "2026-11-01T00:00:00")])
    conn.commit()
    live = {"version": "demo-v1", "metrics": LIVE}
    waiting = [{"version": "demo-v7", "metrics": OFF}, {"version": "demo-v6", "metrics": GOOD}]
    before = verdicts.for_page(conn, waiting, live)
    assert before["demo-v6"]["verdict"] == "approve" and before["demo-v6"]["source"] == "rules"
    assert before["demo-v7"]["verdict"] == "send_back" and before["demo-v7"]["word"] == "Send back"
    out = verdicts.run(conn, Offline())
    assert out["read"] == 2 and out["reader_sentences"] == 2 and out["errors"] == []
    assert verdicts.run(conn, Offline())["read"] == 0
    after = verdicts.for_page(conn, waiting, live)
    assert after["demo-v6"]["source"] == "reader" and after["demo-v6"]["llm"] == "offline"
    # A retrain that changes a candidate's metrics makes it pending again; the old sentence is not shown for it.
    conn.execute("UPDATE model_registry SET metrics=? WHERE version='demo-v6'", (dumps(dict(GOOD, ece=0.02)),))
    conn.commit()
    assert [f.version for f, _ in verdicts.pending(conn)] == ["demo-v6"]
    assert verdicts.for_page(conn, [{"version": "demo-v6", "metrics": dict(GOOD, ece=0.02)}], live)["demo-v6"]["source"] == "rules"

    conn.execute("UPDATE model_registry SET metrics=? WHERE version='demo-v6'", (dumps(GOOD),))      # back to the read facts
    conn.commit()
    auth.create_staff(conn, "nadia", "a-long-enough-password", "marketing", name="Nadia Reyes")
    client = TestClient(create_app(path, scheduler=False))
    log_in(client, "nadia", "a-long-enough-password")
    page = client.get("/model").text
    assert "Send back</b>: when it says a lead is 45 percent, about 59 percent of those leads actually book" in page
    assert "Approve</b>: its top picks book 23 percent more demos" in page and "How the verdict is decided" in page
    assert "The rules' own wording" not in page and page.count("Worded by offline from the rules' facts") == 2
