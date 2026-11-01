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

# The pages' one script. First, show that a draft is on its way, since with Claude it takes a few seconds. Then the
# outline: a column of dashes at the right edge, one per section heading, that unfolds its labels when the pointer
# comes near, with the section in view marked. Built from the headings, so a page with fewer than two has none.
APP_JS = """document.addEventListener('click', function (e) {
  var a = e.target.closest && e.target.closest('a[data-busy]');
  if (a) { a.textContent = a.dataset.busy; a.style.pointerEvents = 'none'; }
  var b = e.target.closest && e.target.closest('button[type=submit][data-busy]');
  if (b && b.form) {
    // one send per click: the form goes with this click, then the button is out of play
    setTimeout(function () { b.textContent = b.dataset.busy; b.disabled = true; }, 0);
  }
});
(function () {
  var main = document.querySelector('main');
  if (!main) return;
  var heads = Array.prototype.filter.call(main.querySelectorAll('h2.kicker[id]'), function (h) {
    return h.offsetParent !== null;
  });
  if (heads.length < 2) return;
  function label(h) {
    var c = h.cloneNode(true);
    Array.prototype.forEach.call(c.querySelectorAll('.info-wrap, .fn, sup'), function (x) { x.parentNode.removeChild(x); });
    var t = c.textContent.replace(/\s+/g, ' ').trim().split(' \u00b7 ')[0];
    return t.charAt(0).toUpperCase() + t.slice(1);
  }
  var nav = document.createElement('nav');
  nav.className = 'outline';
  nav.setAttribute('aria-label', 'On this page');
  var ul = document.createElement('ul');
  var items = [{ id: 'top', text: 'Top', el: main }].concat(heads.map(function (h) { return { id: h.id, text: label(h), el: h }; }));
  var links = items.map(function (it) {
    var li = document.createElement('li'), a = document.createElement('a'), l = document.createElement('span'), d = document.createElement('span');
    a.href = '#' + it.id;
    l.className = 'outline__label'; l.textContent = it.text;
    d.className = 'outline__dash'; d.setAttribute('aria-hidden', 'true');
    a.appendChild(l); a.appendChild(d); li.appendChild(a); ul.appendChild(li);
    return a;
  });
  nav.appendChild(ul);
  document.body.appendChild(nav);
  if (!main.id) main.id = 'top';
  var current = -1;
  function mark() {
    var line = window.innerHeight * 0.35, at = 0;
    for (var i = 1; i < items.length; i++) {
      if (items[i].el.getBoundingClientRect().top <= line) at = i;
    }
    if (window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 2) at = items.length - 1;
    if (at === current) return;
    current = at;
    links.forEach(function (a, i) {
      a.classList.toggle('is-active', i === at);
      if (i === at) a.setAttribute('aria-current', 'location'); else a.removeAttribute('aria-current');
    });
  }
  var queued = false;
  function onScroll() {
    if (queued) return;
    queued = true;
    requestAnimationFrame(function () { queued = false; mark(); });
  }
  window.addEventListener('scroll', onScroll, { passive: true });
  window.addEventListener('resize', onScroll);
  document.addEventListener('mousemove', function (e) {
    var r = nav.getBoundingClientRect();
    var near = e.clientX >= r.left - 120 && e.clientY >= r.top - 120 && e.clientY <= r.bottom + 120;
    nav.classList.toggle('is-near', near);
  }, { passive: true });
  mark();
})();

(function () {
  var clocks = document.querySelectorAll('time[data-clock]');
  if (!clocks.length || !window.Intl || !Intl.DateTimeFormat.prototype.formatToParts) return;
  var loaded = Date.now();
  function fmt(ms, tz, weekday) {
    var parts = {};
    new Intl.DateTimeFormat('en-GB', { timeZone: tz, weekday: 'short', day: '2-digit', month: 'short', year: 'numeric',
      hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' }).formatToParts(new Date(ms))
      .forEach(function (p) { parts[p.type] = p.value; });
    var date = parts.day + ' ' + parts.month + ' ' + parts.year, time = parts.hour + ':' + parts.minute + ':' + parts.second;
    return weekday ? parts.weekday + ' ' + date + ', ' + time : date + ' ' + time;
  }
  function tick() {
    Array.prototype.forEach.call(clocks, function (el) {
      var base = Date.parse(el.getAttribute('datetime'));
      if (isNaN(base)) { return; }
      el.textContent = fmt(base + (Date.now() - loaded), el.getAttribute('data-tz') || 'UTC', el.hasAttribute('data-weekday'));
    });
  }
  tick();
  setInterval(tick, 1000);
})();
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
