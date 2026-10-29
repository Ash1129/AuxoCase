"""What keeps the service's pages and writes safe from other sites, and its edits from going stale.

The service is reached two ways: people's browsers (the pages, whose forms
approve models, release held leads and act on leads) and HubSpot (webhooks).
A browser will send a form to any site it is told to, so every write checks
where it came from:

- Host. Only names in `AUXO_ALLOWED_HOSTS` (default: this machine's loopback
  names, plus Starlette's test client) are served, so a hostile domain that
  re-points its DNS at 127.0.0.1 cannot reach the pages through the browser.
  A deployment adds its own public name.
- Origin. A write whose Origin (or, failing that, Referer) is another site is
  refused. Requests with neither are not from a browser page (the CLI, curl,
  HubSpot) and pass; the webhook is checked by its signature instead.
- Headers. Every response says nosniff, refuses framing, and carries a
  content security policy that allows scripts only from this service.
- Stale edits. Forms that act on a lead or a model carry a token for the state
  the person saw; if the lead or the model changed since (someone else acted,
  HubSpot moved the lead on), the write is refused and the page shows why,
  instead of two people's actions both landing.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
from typing import Optional

from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response

from ..hubspot.models import Contact

DEFAULT_HOSTS = ("127.0.0.1", "localhost", "[::1]", "testserver")
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
SIGNED_PATHS = ("/webhooks/hubspot",)          # server-to-server: verified by HubSpot's signature, not by origin
UNPOLICED_PATHS = ("/docs", "/redoc")          # FastAPI's API docs load their scripts from a CDN
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
       "font-src https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; form-action 'self'; base-uri 'none'; "
       "frame-ancestors 'none'")
HEADERS = {"X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Referrer-Policy": "same-origin"}

# The lead page's one script: show that a draft is on its way, since with Claude it takes a few seconds.
APP_JS = """document.addEventListener('click', function (e) {
  var a = e.target.closest && e.target.closest('a[data-busy]');
  if (a) { a.textContent = a.dataset.busy; a.style.pointerEvents = 'none'; }
});
"""


def allowed_hosts(env: Optional[dict] = None) -> set[str]:
    env = os.environ if env is None else env
    raw = (env.get("AUXO_ALLOWED_HOSTS") or "").strip()
    return {h.strip().lower() for h in raw.split(",") if h.strip()} if raw else set(DEFAULT_HOSTS)


def host_name(host_header: str) -> str:
    """The host without its port; IPv6 literals keep their brackets."""
    h = (host_header or "").strip().lower()
    if h.startswith("["):
        return h[:h.find("]") + 1] if "]" in h else h
    return h.rsplit(":", 1)[0] if ":" in h else h


def same_origin(request: Request) -> bool:
    expected = f"{request.url.scheme}://{request.headers.get('host', '')}".lower()
    origin = request.headers.get("origin")
    if origin is not None:
        return origin.lower() == expected               # "null" (sandboxed frames, files) never matches
    referer = request.headers.get("referer")
    if referer is not None:
        r = referer.lower()
        return r == expected or r.startswith(expected + "/")
    return True


async def guard(request: Request, call_next) -> Response:
    if host_name(request.headers.get("host", "")) not in allowed_hosts():
        return PlainTextResponse("Host not allowed. Add it to AUXO_ALLOWED_HOSTS if this deployment should answer to it.", status_code=400)
    if (request.method in UNSAFE_METHODS and not request.url.path.startswith(SIGNED_PATHS)
            and not same_origin(request)):
        return PlainTextResponse("Writes from another site are refused.", status_code=403)
    response = await call_next(request)
    for k, v in HEADERS.items():
        response.headers.setdefault(k, v)
    if not request.url.path.startswith(UNPOLICED_PATHS):
        response.headers.setdefault("Content-Security-Policy", CSP)
    return response


def _token(*parts) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:16]


def lead_token(conn: sqlite3.Connection, c: Contact) -> str:
    """Changes when the lead's owner or status changes or anyone records an action on it; not when it is rescored."""
    n = conn.execute("SELECT COUNT(*) n FROM overrides WHERE contact_id=?", (c.id,)).fetchone()["n"]
    return _token(c.id, c.hubspot_owner_id or "", c.hs_lead_status or "", n)


def approval_token(conn: sqlite3.Connection, version: str) -> str:
    """Changes when the version's status changes or another approval or send-back is recorded."""
    row = conn.execute("SELECT status FROM model_registry WHERE version=?", (version,)).fetchone()
    n = conn.execute("SELECT COUNT(*) n FROM approvals WHERE version=?", (version,)).fetchone()["n"]
    return _token(version, row["status"] if row else "", n)
