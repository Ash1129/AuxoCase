"""Host and origin checks on writes, security headers, signed webhooks with the real client, and stale-edit refusals."""
import hashlib
import json

import pytest
from fastapi.testclient import TestClient

from auxocase.hubspot.mock import MockHubSpot
from auxocase.hubspot.models import Contact, Owner
from auxocase.service.app import create_app
from auxocase.service.security import approval_token, host_name, lead_token
from auxocase.storage.db import connect, now_iso


@pytest.fixture
def world(tmp_path, monkeypatch):
    for var in ("HUBSPOT_PRIVATE_APP_TOKEN", "HUBSPOT_APP_SECRET", "AUXO_ALLOWED_HOSTS"):
        monkeypatch.delenv(var, raising=False)
    path = tmp_path / "s.db"
    conn = connect(path)
    hs = MockHubSpot(conn, faithful_delivery=True)
    hs.ensure_properties()
    hs.add_owner(Owner(id="900", name="Alex Novak", email="sdr1@brightpath.example", capacity_limit=1))
    hs.add_contact(Contact(id="1", email="laura@granite.example", first_name="Laura", lifecycle_stage="salesqualifiedlead",
                           hs_lead_status="NEW", hubspot_owner_id="900"), emit_event=False)
    metrics = json.dumps({"rows": 900, "positives": 300, "precision_at_k": 0.45, "by_channel": {}, "calibration_bins": []})
    conn.execute("INSERT INTO model_registry(version, family, trained_at, metrics, status) VALUES('demo-v9', 'lr', ?, ?, 'candidate')",
                 (now_iso(), metrics))
    return path, conn, hs


def _overrides(conn):
    return conn.execute("SELECT COUNT(*) n FROM overrides").fetchone()["n"]


def test_only_allowed_host_names_are_served(world, monkeypatch):
    path, _, _ = world
    app = create_app(path)
    assert TestClient(app, base_url="http://evil.example").get("/").status_code == 400     # DNS rebinding
    assert TestClient(app, base_url="http://localhost:8000").get("/").status_code == 200
    assert TestClient(app, base_url="http://127.0.0.1:8000").get("/").status_code == 200
    assert host_name("[::1]:8000") == "[::1]" and host_name("localhost:8000") == "localhost"
    monkeypatch.setenv("AUXO_ALLOWED_HOSTS", "auxo.brightpath.example")
    assert TestClient(app, base_url="https://auxo.brightpath.example").get("/").status_code == 200
    assert TestClient(app, base_url="http://localhost").get("/").status_code == 400         # the list replaces the defaults


def test_writes_from_another_site_are_refused(world):
    path, conn, hs = world
    client = TestClient(create_app(path))
    form = {"owner_id": "900", "action": "junk", "seen": lead_token(conn, hs.get_contact("1"))}
    for headers in ({"Origin": "http://evil.example"}, {"Origin": "null"}, {"Referer": "http://evil.example/page"}):
        assert client.post("/lead/1/action", data=form, headers=headers, follow_redirects=False).status_code == 403
    assert client.post("/model/demo-v9/approve", headers={"Origin": "http://evil.example"},
                       data={"team": "sales", "approver": "x", "decision": "approve", "seen": approval_token(conn, "demo-v9")}).status_code == 403
    assert client.post("/jobs/route", headers={"Origin": "http://evil.example"}).status_code == 403
    assert _overrides(conn) == 0 and conn.execute("SELECT COUNT(*) n FROM approvals").fetchone()["n"] == 0
    r = client.post("/lead/1/action", data=form, headers={"Origin": "http://testserver"}, follow_redirects=False)
    assert r.status_code == 303 and _overrides(conn) == 1                               # the service's own page
    # HubSpot calls from outside with no browser origin; its webhook is checked by signature instead.
    assert client.post("/webhooks/hubspot", json=[], headers={"Origin": "http://evil.example"}).status_code == 200


def test_every_reply_carries_the_security_headers_and_pages_have_no_inline_script(world):
    path, _, _ = world
    client = TestClient(create_app(path))
    for url in ("/", "/lead/1", "/model"):
        r = client.get(url)
        assert r.headers["X-Content-Type-Options"] == "nosniff" and r.headers["X-Frame-Options"] == "DENY"
        csp = r.headers["Content-Security-Policy"]
        assert "script-src 'self'" in csp and "frame-ancestors 'none'" in csp and "unsafe-inline" not in csp.split("script-src")[1].split(";")[0]
        assert "onclick=" not in r.text and "<script>" not in r.text
    js = client.get("/assets/app.js")
    assert js.status_code == 200 and js.headers["content-type"].startswith("application/javascript") and "dataset.busy" in js.text


def test_the_real_client_takes_only_signed_webhooks(world, monkeypatch):
    path, _, _ = world
    monkeypatch.setenv("HUBSPOT_PRIVATE_APP_TOKEN", "test-token")          # selects the real client; nothing calls HubSpot here
    client = TestClient(create_app(path))
    body = json.dumps([{"objectId": "1", "subscriptionType": "contact.propertyChange", "propertyName": "notes",
                        "occurredAt": 0, "eventId": 7, "attemptNumber": 0}]).encode()
    headers = {"Content-Type": "application/json"}
    assert client.post("/webhooks/hubspot", content=body, headers=headers).status_code == 503        # no secret configured
    monkeypatch.setenv("HUBSPOT_APP_SECRET", "s3cret")
    assert client.post("/webhooks/hubspot", content=body, headers=headers).status_code == 401        # unsigned
    bad = dict(headers, **{"X-HubSpot-Signature": "0" * 64})
    assert client.post("/webhooks/hubspot", content=body, headers=bad).status_code == 401
    good = dict(headers, **{"X-HubSpot-Signature": hashlib.sha256(b"s3cret" + body).hexdigest()})
    r = client.post("/webhooks/hubspot", content=body, headers=good)
    assert r.status_code == 200 and r.json()["accepted"] == 1


def test_a_stale_lead_action_is_refused_not_applied(world):
    path, conn, hs = world
    client = TestClient(create_app(path))
    seen = lead_token(conn, hs.get_contact("1"))
    queue = client.get("/queue/900").text
    assert f'name="seen" value="{seen}"' in queue                        # the page carries the version it showed
    assert client.post("/lead/1/action", data={"owner_id": "900", "action": "skipped", "note": "timing"}).status_code == 400   # no version
    r = client.post("/lead/1/action", data={"owner_id": "900", "action": "skipped", "note": "timing", "seen": seen}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/queue/900" and _overrides(conn) == 1
    # A second tab still showing the old page tries to junk the same lead.
    r = client.post("/lead/1/action", data={"owner_id": "900", "action": "junk", "seen": seen}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/queue/900?stale=1"
    assert _overrides(conn) == 1 and hs.get_contact("1").hs_lead_status == "NEW"
    assert "not saved" in client.get("/queue/900?stale=1").text
    # HubSpot moving the lead on (first touch seen there) also makes an open page stale.
    seen = lead_token(conn, hs.get_contact("1"))
    hs.batch_update_contacts([("1", {"hs_lead_status": "ATTEMPTED_TO_CONTACT"})])
    r = client.post("/lead/1/action", data={"owner_id": "900", "action": "worked", "seen": seen}, follow_redirects=False)
    assert r.headers["location"] == "/queue/900?stale=1" and _overrides(conn) == 1
    # A rescore alone does not: rank and score are not part of the version.
    seen = lead_token(conn, hs.get_contact("1"))
    hs.batch_update_contacts([("1", {"auxo_rank": 3, "auxo_score": 61.0})])
    assert seen == lead_token(conn, hs.get_contact("1"))


def test_a_stale_approval_is_refused_not_applied(world):
    path, conn, _ = world
    client = TestClient(create_app(path))
    seen = approval_token(conn, "demo-v9")
    assert f'name="seen" value="{seen}"' in client.get("/model").text
    form = {"team": "marketing", "approver": "Priya", "decision": "approve", "seen": seen}
    assert client.post("/model/demo-v9/approve", data=form, follow_redirects=False).status_code == 303
    # A colleague with the same page open sends it back, not knowing it was just approved.
    r = client.post("/model/demo-v9/approve", data=dict(form, approver="Sam", decision="send_back"), follow_redirects=False)
    assert r.headers["location"] == "/model?stale=demo-v9"
    assert [(a["approver"], a["decision"]) for a in conn.execute("SELECT approver, decision FROM approvals")] == [("Priya", "approve")]
    assert conn.execute("SELECT status FROM model_registry WHERE version='demo-v9'").fetchone()["status"] == "candidate"
    assert "not saved" in client.get("/model?stale=demo-v9").text
    assert client.post("/model/demo-v9/approve", data={k: v for k, v in form.items() if k != "seen"}).status_code == 400
