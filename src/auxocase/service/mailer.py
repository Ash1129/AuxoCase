"""Sending a draft to the lead from the service, over SMTP.

A mailto link carries plain text only, so an email app opened from the page cannot show the branding. "Send to
<the lead>" on the lead page sends the draft from here instead: the plain text and the branded HTML
(email_html.py) as one multipart message, from the configured mailbox with the SDR's name on it, the way the SEG
Proposal Engine sent its proposals.

Settings, read from the environment:
  AUXO_SMTP_USER, AUXO_SMTP_PASS   the mailbox and its password (for Gmail, an app password); unset, nothing sends
  AUXO_SMTP_HOST, AUXO_SMTP_PORT   default smtp.gmail.com, 587 (STARTTLS; 465 is SSL)
  AUXO_SMTP_FROM                   the address on the From line, default the user; the SDR's name is put in front
  AUXO_SMTP_BCC                    an address copied on every send: HubSpot's logging address, so it logs the email
  AUXO_SMTP_TEST_TO                when set, every email goes here instead of to the lead, the subject saying
                                   who it was for: for trying the demo without emailing the synthetic leads.
                                   `ask`: the person sending types the address on the page at each send (the page
                                   remembers the last one), so whoever runs the demo gets the email themselves
"""
from __future__ import annotations

import os
import re
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr
from typing import Mapping, Optional

TIMEOUT_S = 20
ASK = "ask"                  # AUXO_SMTP_TEST_TO=ask: the sender gives the test address on the page
EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


@dataclass
class Mailer:
    user: str = ""
    password: str = ""
    host: str = "smtp.gmail.com"
    port: int = 587
    sender: str = ""
    bcc: str = ""
    test_to: str = ""

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "Mailer":
        env = os.environ if env is None else env
        user = env.get("AUXO_SMTP_USER", "").strip()
        return cls(user=user, password=env.get("AUXO_SMTP_PASS", ""), host=env.get("AUXO_SMTP_HOST", "").strip() or "smtp.gmail.com",
                   port=int(env.get("AUXO_SMTP_PORT") or 587), sender=env.get("AUXO_SMTP_FROM", "").strip() or user,
                   bcc=env.get("AUXO_SMTP_BCC", "").strip(), test_to=env.get("AUXO_SMTP_TEST_TO", "").strip())

    @property
    def configured(self) -> bool:
        return bool(self.user and self.password)

    @property
    def asks(self) -> bool:
        """The test address comes from the page at each send, not from the environment."""
        return self.test_to.lower() == ASK

    def message(self, to: str, subject: str, text: str, html: str, from_name: str = "", test_to: str = "") -> EmailMessage:
        """The email as it goes: text and HTML alternatives, the sender's name on the From line. In ask mode
        `test_to` is the address the sender typed, and nothing goes without one."""
        if self.asks and not test_to:
            raise RuntimeError("no address to send the test to")
        test_to = test_to if self.asks else self.test_to
        m = EmailMessage()
        if test_to:
            m["To"], m["Subject"] = test_to, f"[for {to}] {subject}"
        else:
            m["To"], m["Subject"] = to, subject
        m["From"] = formataddr((from_name, self.sender)) if from_name else self.sender
        if self.bcc:
            m["Bcc"] = self.bcc
        m.set_content(text)
        m.add_alternative(html, subtype="html")
        return m

    def send(self, to: str, subject: str, text: str, html: str, from_name: str = "", test_to: str = "") -> dict:
        """Send it; returns {to, from}: where it went (the test address when one is set) and the From line.
        Raises what smtplib raises when the mailbox refuses."""
        if not self.configured:
            raise RuntimeError("sending is not set up: AUXO_SMTP_USER and AUXO_SMTP_PASS are not in the environment")
        m = self.message(to, subject, text, html, from_name, test_to)
        if self.port == 465:
            with smtplib.SMTP_SSL(self.host, self.port, timeout=TIMEOUT_S) as s:
                self._login(s)
                s.send_message(m)
        else:
            with smtplib.SMTP(self.host, self.port, timeout=TIMEOUT_S) as s:
                s.starttls()
                self._login(s)
                s.send_message(m)
        return {"to": m["To"], "from": m["From"]}

    def _login(self, s) -> None:
        """Sign in, and say why not in words the SDR can act on. Gmail answers an account password with a 534 (an app
        password is required) and drops the line, which smtplib reports as a disconnection."""
        try:
            s.login(self.user, self.password)
        except smtplib.SMTPAuthenticationError as e:
            raise SignInRefused(f"{self.host} refused the sign-in for {self.user}: {e.smtp_error.decode(errors='replace').splitlines()[0]}") from e
        except smtplib.SMTPServerDisconnected as e:
            hint = "; for Gmail, AUXO_SMTP_PASS must be an app password (Google account › Security › 2-Step Verification › App passwords), not the account password" \
                if "gmail" in self.host else ""
            raise SignInRefused(f"{self.host} closed the connection at sign-in for {self.user}{hint}") from e


class SignInRefused(Exception):
    """The mailbox would not sign the service in; the message says what to change."""
