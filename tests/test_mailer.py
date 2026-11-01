"""Sending a draft from the page: the message that goes, and the lead page's send form."""
import smtplib
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from auxocase.data.generate import generate
from auxocase.hubspot.mock import MockHubSpot
from auxocase.service.app import create_app
from auxocase.service.mailer import Mailer, SignInRefused
from auxocase.storage.db import connect


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    path = tmp_path_factory.mktemp("db") / "s.db"
    conn = connect(path)
    hs = MockHubSpot(conn, faithful_delivery=True)
    hs.ensure_properties()
    generate(conn, months=3, seed=5)
    return path, conn, hs


class FakeSMTP:
    sent: list = []
    logins: list = []

    def __init__(self, host, port, timeout=None):
        self.host, self.port = host, port

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self):
        pass

    def login(self, user, password):
        FakeSMTP.logins.append((user, password))

    def send_message(self, m):
        FakeSMTP.sent.append(m)


@pytest.fixture
def smtp(monkeypatch):
    FakeSMTP.sent, FakeSMTP.logins = [], []
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setenv("AUXO_SMTP_USER", "sdr@example.com")
    monkeypatch.setenv("AUXO_SMTP_PASS", "app-password")
    monkeypatch.delenv("AUXO_SMTP_TEST_TO", raising=False)
    return FakeSMTP


def test_message_carries_text_and_html_with_the_senders_name(smtp):
    m = Mailer.from_env()
    assert m.configured and m.host == "smtp.gmail.com" and m.port == 587 and m.sender == "sdr@example.com"
    out = m.send("laura@granite.io", "Payroll at Granite", "Hi Laura,\n\nBest,\nSam", "<table><tr><td>Hi Laura</td></tr></table>", "Sam Ortiz")
    assert out == {"to": "laura@granite.io", "from": "Sam Ortiz <sdr@example.com>"} and smtp.logins == [("sdr@example.com", "app-password")]
    sent = smtp.sent[0]
    assert sent["To"] == "laura@granite.io" and sent["Subject"] == "Payroll at Granite"
    parts = {p.get_content_type(): p.get_content() for p in sent.iter_parts()}
    assert sent.get_content_type() == "multipart/alternative" and parts["text/plain"].startswith("Hi Laura,") and "<table>" in parts["text/html"]


def test_test_address_takes_every_send(smtp, monkeypatch):
    monkeypatch.setenv("AUXO_SMTP_TEST_TO", "me@example.com")
    monkeypatch.setenv("AUXO_SMTP_BCC", "123@bcc.hubspot.com")
    out = Mailer.from_env().send("laura@granite.io", "Payroll", "Hi", "<p>Hi</p>")
    assert out["to"] == "me@example.com" and smtp.sent[0]["Subject"] == "[for laura@granite.io] Payroll" and smtp.sent[0]["Bcc"] == "123@bcc.hubspot.com"


def test_unset_mailbox_sends_nothing(monkeypatch):
    monkeypatch.delenv("AUXO_SMTP_USER", raising=False)
    monkeypatch.delenv("AUXO_SMTP_PASS", raising=False)
    m = Mailer.from_env()
    assert not m.configured
    with pytest.raises(RuntimeError):
        m.send("laura@granite.io", "Payroll", "Hi", "<p>Hi</p>")


def test_lead_page_sends_the_branded_draft_and_logs_it(db, smtp):
    path, conn, hs = db
    client = TestClient(create_app(path))
    c = next(c for c in hs.list_contacts() if c.email and c.first_name and c.hubspot_owner_id)
    page = client.get(f"/lead/{c.id}?draft=1").text
    assert f"Send to {c.email}" in page and 'type="submit" data-busy="Sending…"' in page
    before = len(hs.engagements_for_contact(c.id))
    r = client.post(f"/lead/{c.id}/send", data={"subject": "Payroll at Granite", "body": f"Hi {c.first_name},\n\nA short call?\n\nBest,\nSam",
                                              "back": f"/lead/{c.id}"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/lead/{c.id}?mail_sent={quote(c.email)}#draft-{c.id}"
    sent = smtp.sent[0]
    owner = next(o for o in hs.list_owners(active_only=False) if o.id == c.hubspot_owner_id)
    assert sent["To"] == c.email and sent["From"].startswith(owner.name)
    html = next(p.get_content() for p in sent.iter_parts() if p.get_content_type() == "text/html")
    assert "BrightPath Payroll</span>" in html and f"Hi {c.first_name}," in html
    after = hs.engagements_for_contact(c.id)
    assert len(after) == before + 1 and after[-1].type == "EMAIL" and after[-1].direction == "OUTGOING" and after[-1].subject == "Payroll at Granite"
    page = client.get(f"/lead/{c.id}?mail_sent={c.email}").text
    assert "<b>Sent.</b>" in page and f"went to {c.email}" in page


def test_send_refuses_an_empty_draft_and_a_strange_back_link(db, smtp):
    path, conn, hs = db
    client = TestClient(create_app(path))
    c = next(c for c in hs.list_contacts() if c.email)
    r = client.post(f"/lead/{c.id}/send", data={"subject": "", "body": "x", "back": "https://evil.example/"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith(f"/lead/{c.id}?mail_failed=") and not smtp.sent
    page = client.get(f"/lead/{c.id}?mail_failed=the+draft+is+empty").text
    assert "<b>Not sent.</b> The draft is empty." in page


def test_gmail_dropping_the_line_at_sign_in_names_the_app_password(smtp, monkeypatch):
    def drop(self, user, password):
        raise smtplib.SMTPServerDisconnected("Connection unexpectedly closed")
    monkeypatch.setattr(FakeSMTP, "login", drop)
    with pytest.raises(SignInRefused, match="app password"):
        Mailer.from_env().send("laura@granite.io", "Payroll", "Hi", "<p>Hi</p>")
    assert not smtp.sent
