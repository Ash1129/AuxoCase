"""Logging in, and each role seeing and doing only its own part (src/auxocase/auth.py, the login guard in security.py)."""
import json
import time

import pytest
from fastapi.testclient import TestClient

from auxocase import auth
from auxocase.hubspot.mock import MockHubSpot
from auxocase.hubspot.models import Contact, Owner
from auxocase.service.app import create_app
from auxocase.service.security import approval_token, lead_token
from auxocase.storage.db import connect, now_iso
from conftest import log_in

PW = "a-long-enough-password"


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.delenv("HUBSPOT_PRIVATE_APP_TOKEN", raising=False)
    path = tmp_path / "s.db"
    conn = connect(path)
    hs = MockHubSpot(conn, faithful_delivery=True)
    hs.ensure_properties()
    hs.add_owner(Owner(id="900", name="Alex Novak", email="sdr1@brightpath.example"))
    hs.add_owner(Owner(id="901", name="Bea Ortiz", email="sdr2@brightpath.example"))
    for cid, owner in (("1", "900"), ("2", "901")):
        hs.add_contact(Contact(id=cid, email=f"lead{cid}@granite.example", first_name=f"Lead{cid}", lifecycle_stage="salesqualifiedlead",
                               hs_lead_status="NEW", hubspot_owner_id=owner), emit_event=False)
        conn.execute("INSERT INTO routing_log(contact_id, owner_id, decision, reason, created_at) VALUES(?, ?, 'assigned', 'test', ?)", (cid, owner, now_iso()))
    metrics = json.dumps({"rows": 900, "positives": 300, "precision_at_k": 0.45, "by_channel": {}, "calibration_bins": []})
    conn.execute("INSERT INTO model_registry(version, family, trained_at, metrics, status) VALUES('demo-v9', 'lr', ?, ?, 'candidate')", (now_iso(), metrics))
    hs.add_contact(Contact(id="3", email="lead3@granite.example", first_name="Lead3", lifecycle_stage="salesqualifiedlead", hs_lead_status="NEW"), emit_event=False)
    conn.execute("INSERT INTO held_queue(contact_id, held_at, reason) VALUES('3', ?, 'capacity')", (now_iso(),))
    for username, role, name, owner in (("alex", "sdr", "Alex Novak", "900"), ("bea", "sdr", "Bea Ortiz", "901"), ("sam", "manager", "Sam Okafor", None),
                                        ("priya", "marketing", "Priya Natarajan", None), ("dana", "leadership", "Dana Whitfield", None)):
        auth.create_staff(conn, username, PW, role, name=name, owner_id=owner)
    return path, conn, hs


def test_passwords_lockout_and_sessions(tmp_path):
    conn = connect(tmp_path / "a.db")
    assert auth.verify_password("secret-enough", auth.hash_password("secret-enough")) and not auth.verify_password("x", auth.hash_password("secret-enough"))
    with pytest.raises(auth.AuthError):
        auth.create_staff(conn, "x", "short", "admin")
    with pytest.raises(auth.AuthError):
        auth.create_staff(conn, "x", PW, "sdr")                                 # an SDR needs an owner id
    with pytest.raises(auth.AuthError):
        auth.create_staff(conn, "x", PW, "king")
    auth.create_staff(conn, "Priya", PW, "marketing", name="Priya N")
    assert auth.authenticate(conn, "priya", PW).role == "marketing"            # usernames are case-insensitive
    assert auth.authenticate(conn, "priya", "wrong") is None and auth.authenticate(conn, "nobody", PW) is None
    for _ in range(4):
        auth.authenticate(conn, "priya", "wrong")
    assert auth.authenticate(conn, "priya", PW) is None                         # five wrong: locked
    conn.execute("UPDATE staff SET locked_until=? WHERE username='priya'", (time.time() - 1,))
    me = auth.authenticate(conn, "priya", PW)
    assert me is not None and me.can("approve_marketing") and not me.can("run_jobs") and me.home == "/model"
    token = auth.start_session(conn, me)
    assert auth.load_session(conn, token).username == "priya" and auth.load_session(conn, "no-such") is None
    assert conn.execute("SELECT COUNT(*) n FROM sessions WHERE token_hash=?", (token,)).fetchone()["n"] == 0   # only a hash is kept
    conn.execute("UPDATE sessions SET last_seen=?", (time.time() - auth.IDLE_SECONDS - 1,))
    assert auth.load_session(conn, token) is None                               # idle too long: gone
    token = auth.start_session(conn, me)
    auth.set_disabled(conn, "priya", True)
    assert auth.load_session(conn, token) is None and auth.authenticate(conn, "priya", PW) is None
    auth.set_disabled(conn, "priya", False)
    token = auth.start_session(conn, me)
    auth.set_password(conn, "priya", PW + "2")
    assert auth.load_session(conn, token) is None                               # a new password ends the sessions
    assert auth.bootstrap_from_env(conn, {"AUXO_ADMIN_USER": "root", "AUXO_ADMIN_PASSWORD": PW}) is False   # accounts exist already
    fresh = connect(tmp_path / "b.db")
    assert auth.bootstrap_from_env(fresh, {"AUXO_ADMIN_USER": "root", "AUXO_ADMIN_PASSWORD": PW}) and auth.authenticate(fresh, "root", PW).role == "admin"


def test_without_a_session_pages_go_to_the_login_page_and_writes_are_refused(world):
    path, conn, hs = world
    client = TestClient(create_app(path))
    client.post("/logout", follow_redirects=False)
    r = client.get("/queue/900?tab=touched", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login?next=%2Fqueue%2F900%3Ftab%3Dtouched"
    assert client.post("/lead/1/action", data={"owner_id": "900", "action": "junk", "seen": "x"}).status_code == 401
    assert client.post("/jobs/route").status_code == 401
    assert client.get("/assets/app.js").status_code == 200 and client.post("/webhooks/hubspot", json=[]).status_code == 200   # no session needed
    page = client.get("/login").text
    assert 'name="password"' in page and "Demo logins" not in page               # not a seeded demo portal
    r = client.post("/login", data={"username": "alex", "password": "wrong"})
    assert r.status_code == 401 and "do not match" in r.text
    r = client.post("/login", data={"username": "alex", "password": PW, "next": "/queue/900?tab=touched"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/queue/900?tab=touched" and "auxo_session" in r.headers["set-cookie"]
    assert "HttpOnly" in r.headers["set-cookie"]
    assert client.get("/queue/900").status_code == 200
    r = client.post("/login", data={"username": "sam", "password": PW}, follow_redirects=False)
    assert r.headers["location"] == "/"                                          # the manager's home is the overview
    r = client.post("/logout", follow_redirects=False)
    assert r.headers["location"] == "/login" and client.get("/", follow_redirects=False).status_code == 303


def test_an_sdr_sees_their_own_queue_and_nothing_else(world):
    path, conn, hs = world
    client = TestClient(create_app(path))
    log_in(client, "alex", PW)
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/queue/900"
    page = client.get("/queue/900").text
    assert "My queue" in page and "Alex Novak" in page and "Log out" in page
    assert 'href="/held"' not in page and 'href="/model"' not in page and ">Overview<" not in page and ">Leadership<" not in page
    r = client.get("/queue/901", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/queue/900"       # Bea's queue: back to their own
    assert client.get("/lead/1").status_code == 200 and client.get("/lead/2").status_code == 403
    for url in ("/held", "/model", "/leadership", "/phases", "/decisions"):
        assert client.get(url).status_code == 403, url
    assert client.post("/jobs/route").status_code == 403
    # Acting on Bea's lead, or from Bea's queue, is refused; on their own it lands with them as the actor.
    assert client.post("/lead/2/action", data={"owner_id": "901", "action": "junk", "seen": lead_token(conn, hs.get_contact("2"))}).status_code == 403
    assert client.post("/lead/1/action", data={"owner_id": "901", "action": "junk", "seen": lead_token(conn, hs.get_contact("1"))}).status_code == 403
    r = client.post("/lead/1/action", data={"owner_id": "900", "action": "skipped", "note": "timing", "seen": lead_token(conn, hs.get_contact("1"))}, follow_redirects=False)
    assert r.status_code == 303
    row = conn.execute("SELECT owner_id, actor FROM overrides").fetchone()
    assert (row["owner_id"], row["actor"]) == ("900", "alex")


def test_the_manager_sees_every_queue_and_signs_for_sales(world):
    path, conn, hs = world
    client = TestClient(create_app(path))
    log_in(client, "sam", PW)
    assert client.get("/").status_code == 200 and client.get("/queue/901").status_code == 200 and client.get("/held").status_code == 200
    page = client.get("/model").text
    assert "Signing for" in page and "sales · Sam Okafor" in page and 'name="approver"' not in page
    # The team comes from the role, whatever the form says; the approver is the person logged in.
    r = client.post("/model/demo-v9/approve", data={"team": "marketing", "decision": "approve", "seen": approval_token(conn, "demo-v9")}, follow_redirects=False)
    assert r.status_code == 303
    assert [tuple(a) for a in conn.execute("SELECT team, approver FROM approvals")] == [("sales", "Sam Okafor")]
    # Releasing a held lead records the manager by name, from the session.
    r = client.post("/held/3/release", data={"owner_id": "901", "note": "take it"}, follow_redirects=False)
    assert r.status_code == 303 and conn.execute("SELECT released_by FROM held_queue WHERE contact_id='3'").fetchone()["released_by"] == "Sam Okafor"
    assert client.post("/phases/gate", data={"explore": "0.5"}).status_code == 403    # the switches are leadership's and marketing's
    # A manager's call from Bea's queue is logged on Bea's queue with the manager as the actor, and Decisions says so.
    r = client.post("/lead/2/action", data={"owner_id": "901", "action": "junk", "seen": lead_token(conn, hs.get_contact("2"))}, follow_redirects=False)
    assert r.status_code == 303
    assert "by Sam Okafor" in client.get("/decisions").text


def test_marketing_and_leadership_see_the_model_and_results_only(world):
    path, conn, hs = world
    client = TestClient(create_app(path))
    log_in(client, "priya", PW)
    r = client.get("/", follow_redirects=False)
    assert r.headers["location"] == "/model"
    page = client.get("/model").text
    assert "marketing · Priya Natarajan" in page and ">Queues<" not in page and ">Held" not in page and ">Overview<" not in page
    assert client.get("/queue/900").status_code == 403 and client.get("/held").status_code == 403 and client.get("/lead/1").status_code == 403
    assert client.get("/leadership").status_code == 200 and client.get("/phases").status_code == 200 and client.get("/decisions").status_code == 200
    assert client.post("/jobs/route").status_code == 403
    r = client.post("/model/demo-v9/approve", data={"team": "sales", "decision": "approve", "seen": approval_token(conn, "demo-v9")}, follow_redirects=False)
    assert r.status_code == 303 and conn.execute("SELECT team, approver FROM approvals").fetchone()["team"] == "marketing"
    assert client.post("/leadership/capacity", data={"hours": "30"}).status_code == 403   # capacity is leadership's
    assert client.post("/phases/gate", data={"explore": "0.5"}, follow_redirects=False).status_code == 303
    assert conn.execute("SELECT who FROM phase_decisions").fetchone()["who"] == "Priya Natarajan"
    log_in(client, "dana", PW)
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 200                                                   # leadership reads the overview
    page = client.get("/model").text
    assert "your role reads" in page and 'action="/model/demo-v9/approve"' not in page
    assert client.post("/model/demo-v9/approve", data={"decision": "approve", "seen": approval_token(conn, "demo-v9")}).status_code == 403
    assert client.post("/leadership/capacity", data={"hours": "30"}, follow_redirects=False).status_code == 303


def test_the_demo_portal_lists_its_logins(world):
    path, conn, hs = world
    made = auth.seed_demo_accounts(conn, hs.list_owners())
    assert set(made) == {"sdr1", "sdr2", "manager", "marketing", "leadership", "admin"}
    assert auth.seed_demo_accounts(conn, hs.list_owners()) == []                  # a second time adds nothing
    client = TestClient(create_app(path))
    client.post("/logout", follow_redirects=False)
    page = client.get("/login").text
    assert "Demo logins" in page and "<code>sdr1</code>" in page and auth.DEMO_PASSWORD in page and "their own queue" in page
    r = client.post("/login", data={"username": "sdr2", "password": auth.DEMO_PASSWORD}, follow_redirects=False)
    assert r.headers["location"] == "/queue/901"
