"""The web service: a HubSpot-shaped webhook receiver, a JSON API, and the pages people use.

Who sees what (each logs in; auth.py has the roles):
- SDRs: their own queue, best first, each lead with its probability, rank, the
  plain-English reason and the signals' quoted words; actions are bounded
  (worked / skipped with a reason / junk / promote or demote by a few places)
  and every action is logged with the explanation that was on screen (L12).
- Sales manager: everything: the overview, every queue, the held queue and
  the stale-assignment list (speed to lead), the decisions, the leadership
  page and its capacity inputs, the jobs, and the sign-off on a model for
  either team.
- Marketing: the model page (live vs candidate metrics, the calibration table,
  the channel p-chart), the marketing sign-off and the flagged leads.

In production HubSpot calls `/webhooks/hubspot` (signature checked, H04) and
the SDR pages would be embedded as a HubSpot CRM card; here the same pages
run standalone against the mock.
"""
from __future__ import annotations

import os
import re
import sqlite3
import threading
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from .. import auth, flags, verdicts
from ..feedback import loop as fb
from ..hubspot.client import HubSpotClient
from ..hubspot.mock import MockHubSpot
from ..hubspot.models import SKIP_STATUS, SKIP_STATUS_DEFAULT
from ..llm.client import make_llm
from ..routing.engine import Router
from ..routing.outreach import log_demo_activity, prepare_outreach
from ..routing.touches import detect_first_touches
from ..routing.tier2 import detect_replies, run_tier2
from ..scoring.pipeline import Scorer, load_live_model
from ..storage.db import DEFAULT_DB, clock, clock_frozen, connect, dumps, get_setting, loads, now_iso
from . import tz
from . import scheduler as sched
from . import security
from .email_html import BRAND, branded
from .mailer import EMAIL, Mailer, SignInRefused
from .security import SESSION_COOKIE, approval_token, clear_session_cookie, lead_token, safe_next, set_session_cookie
from .views.flagged import flagged_page
from .views.held import held_page
from .views.lead import lead_detail
from .views.leadership import leadership_page
from .views.model import model_page
from .views.overview import overview_page
from ..capacity import set_assumptions
from ..llm.replies import DEMO_REPLIES
from ..routing import callnotes, followups, replies
from .views.queue import TABS as QUEUE_TABS, queue_page
from .views.stats import hit_rate

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
TEMPLATES.env.globals["email_html"] = branded        # a draft as the lead will see it: the lead page's preview, and what is sent
MAX_RANK_MOVE = 5    # L12: a bounded override buys adoption without handing the queue back to gut feel


class State:
    def __init__(self, db_path=DEFAULT_DB):
        self.conn: sqlite3.Connection = connect(db_path)
        self.hs: HubSpotClient = _make_client(self.conn)
        if isinstance(self.hs, MockHubSpot):
            self.hs.ensure_properties()      # a demo database from before a new auxo_* property gets it; the real portal: go-live step 1
        self.llm = make_llm(conn=self.conn)
        self.model = load_live_model(self.conn)
        self.mailer = Mailer.from_env()          # "Send to <the lead>" on the lead page; nothing sends until AUXO_SMTP_* are set
        auth.bootstrap_from_env(self.conn)       # the first admin, from AUXO_ADMIN_USER and AUXO_ADMIN_PASSWORD, on an empty staff table

    def scorer(self) -> Scorer:
        self.model = load_live_model(self.conn)
        return Scorer(self.hs, self.conn, self.model, self.llm)

    def router(self) -> Router:
        return Router(self.hs, self.conn)


def _make_client(conn) -> HubSpotClient:
    token = os.environ.get("HUBSPOT_PRIVATE_APP_TOKEN")
    if token:
        from ..hubspot.real import RealHubSpot
        return RealHubSpot(token)
    return MockHubSpot(conn)


def create_app(db_path=DEFAULT_DB, scheduler: Optional[bool] = None) -> FastAPI:
    """`scheduler`: run the jobs on timers in this process. None reads AUXO_SCHEDULER (off unless set; `run.py serve`
    turns it on by default)."""
    st = State(db_path)
    runner = sched.Scheduler(db_path, hs=st.hs) if (sched.enabled() if scheduler is None else scheduler) else None

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if runner:
            runner.start()
        try:
            yield
        finally:
            if runner:
                runner.stop()

    app = FastAPI(title="AuxoCase", lifespan=lifespan)
    app.middleware("http")(security.login_guard)  # the session cookie becomes request.state.me; no session, no page and no write
    app.middleware("http")(security.guard)       # host and origin checks on every request, security headers on every reply (runs first)
    app.state.st = st
    app.state.scheduler = runner

    @app.exception_handler(HTTPException)
    async def refused(request: Request, exc: HTTPException):
        """A page someone's role may not open comes back as a short page with the way to their own, not bare JSON."""
        me = getattr(request.state, "me", None)
        if exc.status_code == 403 and me is not None and "text/html" in request.headers.get("accept", ""):
            return TEMPLATES.TemplateResponse(request, "refused.html", {"request": request, "why": exc.detail, "home": me.home, "name": me.name,
                                                                        "role": me.role_word, "theme": request.cookies.get("auxo_theme", "system")},
                                              status_code=403)
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=getattr(exc, "headers", None))

    def ctx(request: Request, **kw):
        """What every page's shell shows: navigation counts, the system block and the theme."""
        live = fb.live_version(st.conn)
        owners = st.hs.list_owners()
        me: auth.Principal = request.state.me
        last = me.owner_id if me.role == "sdr" else request.cookies.get("auxo_owner")
        mock = isinstance(st.hs, MockHubSpot)
        frozen, now = clock_frozen(st.conn), clock(st.conn)
        base = {"request": request, "live": live, "llm": st.llm.name, "scheduler": "on" if runner and runner.running else "off",
                "line": loads(get_setting(st.conn, "capacity_line_probability")), "nav": "", "smtp": st.mailer.configured, "smtp_ask": st.mailer.asks,
                "test_to": request.cookies.get("auxo_test_to", "") if st.mailer.asks else "",
                "held_count": st.conn.execute("SELECT COUNT(*) n FROM held_queue WHERE released_at IS NULL").fetchone()["n"],
                "candidates": st.conn.execute("SELECT COUNT(*) n FROM model_registry WHERE status='candidate'").fetchone()["n"],
                "flags_open": st.conn.execute("SELECT COUNT(*) n FROM flags WHERE resolved_at IS NULL").fetchone()["n"],
                "queue_owner": last if any(o.id == last for o in owners) else (owners[0].id if owners else None),
                "env": "mock" if mock else "hubspot", "frozen": frozen, "tz": tz.ZONE, "tz_label": tz.LABEL,
                "clock_text": now.strftime("%d %b %Y %H:%M") if frozen else tz.clock_text(now), "clock_iso": tz.clock_iso(now),
                "theme": request.cookies.get("auxo_theme", "system"), "me": me, "can": {p: me.can(p) for p in auth.PERMISSIONS}}
        return {**base, **kw}

    def me_of(request: Request) -> auth.Principal:
        return request.state.me

    def allow(request: Request, permission: str) -> auth.Principal:
        """The person behind the request, if their role has the permission; 403 otherwise."""
        me = me_of(request)
        if not me.can(permission):
            raise HTTPException(403, f"your role ({me.role_word}) cannot do this")
        return me

    def lead_for(request: Request, contact_id: str):
        """The lead, if the person may see it: a manager any lead, an SDR only one on their own queue."""
        c = st.hs.get_contact(contact_id)
        if not c:
            raise HTTPException(404)
        if not me_of(request).owns(c.hubspot_owner_id):
            raise HTTPException(403, "that lead is not on your queue")
        return c

    # ---------------- logging in ----------------
    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request, next: str = "/"):
        """Logging in while logged in is fine: the new session replaces the old (the demo switches people this way)."""
        mock = isinstance(st.hs, MockHubSpot)
        return TEMPLATES.TemplateResponse(request, "login.html", {"request": request, "next": safe_next(next), "env": "mock" if mock else "hubspot",
                                                                  "demo": auth.demo_logins(st.conn) if mock else [], "demo_password": auth.DEMO_PASSWORD,
                                                                  "no_staff": not auth.staff_count(st.conn), "theme": request.cookies.get("auxo_theme", "system"),
                                                                  "error": ""})

    @app.post("/login")
    def login(request: Request, username: str = Form(""), password: str = Form(""), next: str = Form("/")):
        me = auth.authenticate(st.conn, username, password)
        if me is None:
            mock = isinstance(st.hs, MockHubSpot)
            return TEMPLATES.TemplateResponse(request, "login.html", {"request": request, "next": safe_next(next), "env": "mock" if mock else "hubspot",
                                                                      "demo": auth.demo_logins(st.conn) if mock else [], "demo_password": auth.DEMO_PASSWORD,
                                                                      "no_staff": not auth.staff_count(st.conn), "theme": request.cookies.get("auxo_theme", "system"),
                                                                      "error": "That username and password do not match an active account "
                                                                               "(after five wrong tries an account is locked for fifteen minutes)."},
                                              status_code=401)
        auth.end_session(st.conn, request.cookies.get(SESSION_COOKIE))
        target = safe_next(next)
        response = RedirectResponse(me.home if target == "/" else target, status_code=303)
        set_session_cookie(response, request, auth.start_session(st.conn, me))
        return response

    @app.post("/logout")
    def logout(request: Request):
        auth.end_session(st.conn, request.cookies.get(SESSION_COOKIE))
        response = RedirectResponse("/login", status_code=303)
        clear_session_cookie(response)
        return response

    # ---------------- pages ----------------
    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
        me = me_of(request)
        if not me.can("view_overview"):
            return RedirectResponse(me.home, status_code=303)       # an SDR's front page is their queue, marketing's the model page
        return TEMPLATES.TemplateResponse(request, "home.html", ctx(request, nav="overview", **overview_page(st)))

    @app.get("/queue/{owner_id}", response_class=HTMLResponse)
    def queue(request: Request, owner_id: str, stale: str = "", lead: str = "", draft: int = 0, call: int = 0, mail_sent: str = "",
              mail_failed: str = "", mail_for: str = "", mail_lead: str = "", tab: str = ""):
        me = me_of(request)
        if not me.owns(owner_id):
            if me.role == "sdr" and me.owner_id:
                return RedirectResponse(f"/queue/{me.owner_id}", status_code=303)    # another SDR's queue: back to their own
            raise HTTPException(403, "your role has no queue")
        page = queue_page(st, owner_id, lead or None, bool(draft), bool(call), tab=tab)
        if page is None:
            raise HTTPException(404)
        return TEMPLATES.TemplateResponse(request, "queue.html", ctx(request, nav="queues", **page, hit=_hit_rate(st.conn), stale=stale,
                                                                    mail_sent=mail_sent, mail_failed=mail_failed, mail_for=mail_for, mail_lead=mail_lead))

    @app.get("/lead/{contact_id}", response_class=HTMLResponse)
    def lead(request: Request, contact_id: str, draft: int = 0, call: int = 0, mail_sent: str = "", mail_failed: str = ""):
        lead_for(request, contact_id)
        detail = lead_detail(st, contact_id, bool(draft), bool(call))
        if detail is None:
            raise HTTPException(404)
        return TEMPLATES.TemplateResponse(request, "lead.html", ctx(request, nav="queues", **detail, mail_sent=mail_sent, mail_failed=mail_failed))

    @app.post("/lead/{contact_id}/preview", response_class=HTMLResponse)
    def lead_preview(request: Request, contact_id: str, body: str = Form("")):
        """The edited draft as the lead will see it, for the page's Preview button (email_html.py)."""
        c = lead_for(request, contact_id)
        owner = next((o for o in st.hs.list_owners(active_only=False) if o.id == c.hubspot_owner_id), None)
        return HTMLResponse(branded(body.strip(), owner.name if owner else BRAND))

    @app.post("/lead/{contact_id}/send")
    def lead_send(request: Request, contact_id: str, subject: str = Form(""), body: str = Form(""), back: str = Form(""),
                  test_to: str = Form("")):
        """Send a draft to the lead from here, branded (email_html.py), over SMTP (mailer.py), with the SDR's name on
        the From line. On the mock the send is logged in HubSpot as the SDR's, so the first-touch check sees it and
        refills the slot; on a real portal HubSpot logs it from the BCC address (AUXO_SMTP_BCC). A reply is not
        coming this minute, so a sent lead is left in the touched group and the SDR's queue opens on the next lead;
        a failure comes back to the page it came from. Either way with a notice."""
        allow(request, "act_on_leads")
        c = lead_for(request, contact_id)
        if not re.fullmatch(r"/(lead|queue)/[\w-]+(\?[\w=&-]*)?", back):
            back = f"/lead/{contact_id}"
        sep = "&" if "?" in back else "?"

        def failed(why: str):
            return RedirectResponse(f"{back}{sep}mail_failed={quote(why[:200])}#draft-{contact_id}", status_code=303)
        subject, body = subject.strip(), body.strip()
        if not c.email:
            return failed("the lead has no email address")
        if not subject or not body:
            return failed("the draft is empty")
        if not st.mailer.configured:
            return failed("sending is not set up: AUXO_SMTP_USER and AUXO_SMTP_PASS (a Gmail app password) in the server's environment")
        test_to = test_to.strip()
        if st.mailer.asks and not EMAIL.fullmatch(test_to):     # ask mode: the test goes to the address typed here
            return failed("give the address to send the test to")
        owner = next((o for o in st.hs.list_owners(active_only=False) if o.id == c.hubspot_owner_id), None)
        from_name = owner.name if owner else BRAND
        try:
            out = st.mailer.send(c.email, subject, body, branded(body, from_name), from_name, test_to)
        except SignInRefused as e:
            return failed(str(e))
        except Exception as e:                      # the mailbox could not be reached, or refused the message
            return failed(f"the mailbox refused it: {type(e).__name__}: {e}")
        if isinstance(st.hs, MockHubSpot):
            st.hs.log_engagement(contact_id, "EMAIL", "OUTGOING", clock(st.conn), c.hubspot_owner_id, subject=subject, body=body)
            detect_first_touches(st.hs, st.conn)           # the touch frees the slot and routing refills it at once
            detect_replies(st.hs, st.conn)
        name = f"{c.first_name} {c.last_name}".strip() or c.email
        sent = f"mail_sent={quote(out['to'])}&mail_for={quote(name)}&mail_lead={contact_id}"
        if c.hubspot_owner_id:
            done = RedirectResponse(f"/queue/{c.hubspot_owner_id}?{sent}", status_code=303)
        else:
            done = RedirectResponse(f"{back}{sep}{sent}#draft-{contact_id}", status_code=303)
        if st.mailer.asks:                          # the next send offers the same address
            done.set_cookie("auxo_test_to", test_to, max_age=60 * 60 * 24 * 30, httponly=True, samesite="lax",
                            secure=request.url.scheme == "https")
        return done

    @app.post("/lead/{contact_id}/hubspot")
    def hubspot_demo(request: Request, contact_id: str, kind: str = Form(...), subject: str = Form(""), disposition: str = Form(""),
                     reply: str = Form(""), notes: str = Form(""), back: str = Form("")):
        """Demo only: stand in for HubSpot, where the SDR sends the email or logs the call, then run the first-touch
        check at once (on a real portal the `touches` job polls HubSpot every minute)."""
        if not isinstance(st.hs, MockHubSpot):
            raise HTTPException(404)
        allow(request, "act_on_leads")
        lead_for(request, contact_id)
        try:
            log_demo_activity(st.hs, st.conn, contact_id, kind, subject, disposition,
                              body=DEMO_REPLIES[reply][1] if kind == "reply" and reply in DEMO_REPLIES else notes[:2000].strip())
        except KeyError:
            raise HTTPException(404)
        except ValueError:
            raise HTTPException(400, "unknown activity")
        detect_first_touches(st.hs, st.conn)
        detect_replies(st.hs, st.conn)
        replies.triage_new(st.hs, st.conn, st.llm)
        callnotes.write_up_new(st.hs, st.conn, st.llm)
        if not re.fullmatch(r"/queue/[\w-]+\?lead=[\w-]+", back):
            back = f"/lead/{contact_id}"                # from the queue, back to the queue with the lead open on its tab
        return RedirectResponse(back, status_code=303)

    @app.post("/lead/{contact_id}/followups")
    def lead_followups(request: Request, contact_id: str, what: str = Form(...)):
        """The SDR's say over the automatic emails: 'stop' ends them for this lead; 'now' (demo only) sends the next one
        without waiting for its day."""
        allow(request, "act_on_leads")
        lead_for(request, contact_id)
        if what == "stop":
            followups.stop(st.conn, contact_id)
        elif what == "now" and isinstance(st.hs, MockHubSpot):
            followups.send_due(st.hs, st.conn, st.llm, contact_id=contact_id)
        else:
            raise HTTPException(400, "unknown action")
        return RedirectResponse(f"/lead/{contact_id}#reach-{contact_id}", status_code=303)

    @app.post("/lead/{contact_id}/action")
    def lead_action(request: Request, contact_id: str, owner_id: str = Form(...), action: str = Form(...), note: str = Form(""),
                    move: int = Form(0), seen: str = Form(""), tab: str = Form("")):
        """The SDR's call on a lead, from the queue; `tab` is the queue tab it was made on, which the page comes back to.
        `owner_id` is the queue it was made on: an SDR's own, or any for a manager; the person is recorded as the actor."""
        me = allow(request, "act_on_leads")
        c = lead_for(request, contact_id)
        if not me.owns(owner_id):
            raise HTTPException(403, "that queue is not yours")
        if action not in ("worked", "skipped", "junk", "promote", "demote"):
            raise HTTPException(400, "unknown action")
        if not seen:
            raise HTTPException(400, "the form is missing the lead's version; reload the page")
        back = f"/queue/{owner_id}" + (f"?tab={tab}" if tab in QUEUE_TABS else "")
        sep = "&" if "?" in back else "?"
        if seen != lead_token(st.conn, c):
            # Someone else acted on it, or HubSpot moved it on, since this page was loaded: say so instead of acting twice.
            return RedirectResponse(f"{back}{sep}stale={contact_id}", status_code=303)
        move = max(-MAX_RANK_MOVE, min(MAX_RANK_MOVE, move))
        st.conn.execute("INSERT INTO overrides(contact_id, owner_id, action, note, score_at_time, rank_at_time, created_at, actor) VALUES(?,?,?,?,?,?,?,?)",
                        (contact_id, owner_id, action, note, c.auxo_score, c.auxo_rank, now_iso(), me.username))
        if action == "worked":
            st.hs.batch_update_contacts([(contact_id, {"hs_lead_status": "IN_PROGRESS"})])
            st.conn.execute("INSERT INTO outcomes(contact_id, worked, first_touch_at, updated_at) VALUES(?,?,?,?) ON CONFLICT(contact_id) DO UPDATE SET worked=1, first_touch_at=COALESCE(outcomes.first_touch_at, excluded.first_touch_at), updated_at=excluded.updated_at",
                            (contact_id, 1, now_iso(), now_iso()))
        elif action == "skipped":
            # Not worked, so no outcome row; the status frees the slot (and outcome sync leaves the lead unlabelled).
            st.hs.batch_update_contacts([(contact_id, {"hs_lead_status": SKIP_STATUS.get(note, SKIP_STATUS_DEFAULT)})])
        elif action == "junk":
            st.hs.batch_update_contacts([(contact_id, {"hs_lead_status": "UNQUALIFIED"})])
            st.conn.execute("INSERT INTO outcomes(contact_id, worked, first_touch_at, updated_at) VALUES(?,?,?,?) ON CONFLICT(contact_id) DO UPDATE SET worked=1, updated_at=excluded.updated_at",
                            (contact_id, 1, now_iso(), now_iso()))
        elif action in ("promote", "demote") and c.auxo_rank:
            new_rank = max(1, c.auxo_rank - move) if action == "promote" else c.auxo_rank + move
            st.hs.batch_update_contacts([(contact_id, {"auxo_rank": new_rank, "auxo_review_flag": f"rank moved by {owner_id}: {note}"[:200]})])
        if action in ("worked", "junk", "skipped"):
            # The lead no longer waits on the SDR, so the slot is free: refill it now rather than at the next scheduled pass.
            st.router().run()
        return RedirectResponse(back, status_code=303)

    @app.get("/held", response_class=HTMLResponse)
    def held(request: Request):
        allow(request, "view_held")
        return TEMPLATES.TemplateResponse(request, "held.html", ctx(request, nav="held", **held_page(st)))

    @app.post("/held/{contact_id}/release")
    def release(request: Request, contact_id: str, owner_id: str = Form(...), note: str = Form("")):
        me = allow(request, "release_held")
        if not any(o.id == owner_id for o in st.hs.list_owners()):
            raise HTTPException(400, "pick an active SDR")
        ok = st.router().release_held(contact_id, owner_id, me.name, note)
        if not ok:
            raise HTTPException(409, "lead already has an owner")
        return RedirectResponse("/held", status_code=303)

    @app.get("/model", response_class=HTMLResponse)
    def model(request: Request, stale: str = "", tab: str = ""):
        """Two tabs: what to work on (the sign-off, the channel monitor, the SDR overrides, the flags) and how it
        decides (the evidence behind the scores: what the model adds, live against the candidates, calibration,
        the first-day effect). The work tab is the default, so a decision comes back to its form."""
        allow(request, "view_model")
        tab = tab if tab in ("work", "decides") else "work"
        return TEMPLATES.TemplateResponse(request, "model.html", ctx(request, nav="model", **model_page(st), stale=stale, tab=tab))

    @app.get("/flagged", response_class=HTMLResponse)
    def flagged(request: Request, done: str = ""):
        """Marketing's second look: the flags the reader raised, each with the record's own words, to resolve."""
        allow(request, "view_flags")
        return TEMPLATES.TemplateResponse(request, "flagged.html", ctx(request, nav="flagged", **flagged_page(st), done=done[:3]))

    @app.post("/flagged/{flag_id}/resolve")
    def flag_resolve(request: Request, flag_id: int, resolution: str = Form(...), note: str = Form("")):
        me = allow(request, "resolve_flags")
        try:
            ok = flags.resolve(st.hs, st.conn, flag_id, resolution, me.name, note)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return RedirectResponse(f"/flagged?done={1 if ok else 0}#fl-open", status_code=303)

    @app.get("/leadership", response_class=HTMLResponse)
    def leadership(request: Request, set: str = "", refused: str = ""):
        """`set`: how many capacity inputs the last submit changed (0: none); `refused`: why one was not set."""
        allow(request, "view_leadership")
        notice = {"set": set[:3], "refused": refused[:300]}
        return TEMPLATES.TemplateResponse(request, "leadership.html", ctx(request, nav="leadership", notice=notice, **leadership_page(st)))

    @app.post("/leadership/capacity")
    def capacity_set(request: Request, minutes_touch: str = Form(""), minutes_followup: str = Form(""), minutes_reply: str = Form(""), hours: str = Form(""),
                     followups: str = Form(""), minutes_reply_triaged: str = Form(""), replies: str = Form(""),
                     minutes_call_log: str = Form(""), minutes_call_log_auto: str = Form(""), call_notes: str = Form(""),
                     minutes_skip: str = Form(""), skips: str = Form(""), minutes_booking: str = Form(""),
                     minutes_booking_auto: str = Form(""), scheduling: str = Form("")):
        """The capacity inputs: minutes a cold touch, a follow-up and a reply, hours an SDR gives leads a month, and who
        sends the follow-ups ('auto' or 'sdr'). Blank leaves one as it is; a value out of range is refused and nothing
        is set."""
        allow(request, "set_capacity")
        try:
            given = {k: (float(v) if v.strip() else None) for k, v in (("minutes_touch", minutes_touch), ("minutes_followup", minutes_followup),
                                                                      ("minutes_reply", minutes_reply), ("minutes_reply_triaged", minutes_reply_triaged),
                                                                      ("minutes_call_log", minutes_call_log), ("minutes_call_log_auto", minutes_call_log_auto),
                                                                      ("minutes_skip", minutes_skip), ("minutes_booking", minutes_booking),
                                                                      ("minutes_booking_auto", minutes_booking_auto), ("hours", hours))}
            flags = {"auto": True, "sdr": False}
            changed = set_assumptions(st.conn, auto=flags.get(followups), replies=flags.get(replies), call_notes=flags.get(call_notes),
                                      skips=flags.get(skips), scheduling=flags.get(scheduling), **given)
        except ValueError as e:
            return RedirectResponse(f"/leadership?refused={quote(str(e))}#ld-capacity", status_code=303)
        return RedirectResponse(f"/leadership?set={len(changed)}#ld-capacity", status_code=303)

    @app.get("/decisions", response_class=HTMLResponse)
    def decisions(request: Request, limit: int = 500):
        """One place for every human call: model approvals and send-backs, and leads SDRs worked or turned down."""
        allow(request, "view_decisions")
        limit = max(1, min(limit, 5000))
        models = [dict(r) for r in st.conn.execute(
            "SELECT a.*, m.status FROM approvals a LEFT JOIN model_registry m ON m.version = a.version ORDER BY a.created_at DESC, a.id DESC").fetchall()]
        leads = [dict(r) for r in st.conn.execute(
            "SELECT * FROM overrides WHERE action IN ('worked', 'junk', 'skipped') ORDER BY created_at DESC, id DESC LIMIT ?", (limit,)).fetchall()]
        counts = {"approve": 0, "send_back": 0, "worked": 0, "junk": 0, "skipped": 0}
        for r in st.conn.execute("SELECT decision k, COUNT(*) n FROM approvals GROUP BY decision UNION ALL "
                                 "SELECT action k, COUNT(*) n FROM overrides GROUP BY action").fetchall():
            if r["k"] in counts:
                counts[r["k"]] += r["n"]
        owners = {o.id: o.name for o in st.hs.list_owners(active_only=False)}
        staff = {s["username"]: s["name"] for s in auth.list_staff(st.conn)}
        titles = {}
        for cid in {o["contact_id"] for o in leads}:
            c = st.hs.get_contact(cid)
            titles[cid] = c.job_title if c else None
        return TEMPLATES.TemplateResponse(request, "decisions.html", ctx(request, nav="decisions", models=models, leads=leads, counts=counts,
                                                                          owners=owners, staff=staff, titles=titles, limit=limit))

    @app.post("/model/{version}/approve")
    def approve(request: Request, version: str, decision: str = Form(...), note: str = Form(""), seen: str = Form(""), team: str = Form("")):
        """A sign-off or a send-back, for the team the person's role speaks for (a role that signs for both picks the
        team, sales unless it says); the approver is the person logged in."""
        me = me_of(request)
        if me.can("approve_marketing") and me.can("approve_sales"):
            team = team if team in ("marketing", "sales") else "sales"
        elif me.can("approve_marketing"):
            team = "marketing"
        elif me.can("approve_sales"):
            team = "sales"
        else:
            raise HTTPException(403, f"your role ({me.role_word}) does not sign models off")
        approver = me.name
        if not seen:
            raise HTTPException(400, "the form is missing the model's version state; reload the page")
        if seen != approval_token(st.conn, version):
            return RedirectResponse(f"/model?stale={version}", status_code=303)
        try:
            res = fb.approve(st.conn, version, team, approver, decision, note)
        except ValueError as e:
            raise HTTPException(400, str(e))
        if res["status"] == "live" and not (runner and runner.running):
            # With the scheduler on, its rescore job picks this up within a minute; without it, rescore in the
            # background here, since with Claude reading notes the whole pool takes far longer than a request.
            app.state.rescore_thread = threading.Thread(target=_rescore_in_background, args=(db_path, st.hs), daemon=True)
            app.state.rescore_thread.start()
        return RedirectResponse("/model", status_code=303)

    # ---------------- jobs (the three Blueprint jobs, callable from the UI, the CLI or a scheduler) ----------------
    @app.post("/jobs/score")
    def job_score(request: Request, mode: str = "events"):
        allow(request, "run_jobs")
        sc = st.scorer()
        res = sc.process_events() if mode == "events" else sc.rescore_pool()
        return {"scored": len(res), "model_version": sc.model_version}

    @app.post("/jobs/route")
    def job_route(request: Request):
        allow(request, "run_jobs")
        d = st.router().run()
        out = {"decisions": {k: sum(1 for x in d if x.decision == k) for k in {x.decision for x in d}}}
        t2 = run_tier2(st.hs, st.conn, st.llm)
        if t2["emailed"] or t2["control"] or t2["expired"]:
            out["automated"] = t2
        return out

    @app.post("/jobs/sync")
    def job_sync(request: Request):
        allow(request, "run_jobs")
        return fb.sync_outcomes(st.hs, st.conn)

    @app.post("/jobs/monitor")
    def job_monitor(request: Request):
        allow(request, "run_jobs")
        pts = fb.channel_monitor(st.hs, st.conn)
        return {"points": [p.__dict__ for p in pts]}

    @app.post("/jobs/train")
    def job_train(request: Request, notes: str = ""):
        allow(request, "run_jobs")
        try:
            model, m = fb.train_candidate(st.hs, st.conn, notes=notes)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"version": model.version, "family": model.family, "metrics": m.to_dict()}

    @app.post("/jobs/touches")
    def job_touches(request: Request):
        allow(request, "run_jobs")
        return detect_first_touches(st.hs, st.conn)

    @app.post("/jobs/outreach")
    def job_outreach(request: Request, limit: int = 10):
        allow(request, "run_jobs")
        return prepare_outreach(st.hs, st.conn, st.llm, limit=limit)

    @app.post("/jobs/flags")
    def job_flags(request: Request, limit: int = 25):
        allow(request, "run_jobs")
        out = flags.run(st.hs, st.conn, st.llm, limit=limit)
        out["verdicts"] = verdicts.run(st.conn, st.llm)
        if out["verdicts"].get("went_live") and not (runner and runner.running):
            app.state.rescore_thread = threading.Thread(target=_rescore_in_background, args=(db_path, st.hs), daemon=True)
            app.state.rescore_thread.start()
        return out

    @app.post("/model/auto")
    def model_auto(request: Request, switch: str = Form(...)):
        """Automatic sign-off on or off, recorded with who threw it."""
        who = allow(request, "throw_switches").name
        on = {"on": True, "off": False}.get(switch.strip().lower())
        if on is None:
            raise HTTPException(400, "switch must be on or off")
        verdicts.set_auto(st.conn, on, who)
        return RedirectResponse("/model#sign-off", status_code=303)

    @app.post("/jobs/realert")
    def job_realert(request: Request):
        allow(request, "run_jobs")
        return {"reminders": st.router().re_alert_stale()}

    @app.post("/theme")
    def set_theme(theme: str = Form("system"), next: str = Form("/")):
        """Light, dark or the system's, kept in a cookie so the server writes it into the page: no flash, no script."""
        target = next if next.startswith("/") and not next.startswith("//") else "/"
        r = RedirectResponse(target, status_code=303)
        r.set_cookie("auxo_theme", theme if theme in ("light", "dark") else "system", max_age=365 * 24 * 3600, samesite="lax")
        return r

    @app.get("/assets/app.js")
    def app_js():
        return Response(security.APP_JS, media_type="application/javascript")

    # ---------------- HubSpot webhook (production path) ----------------
    @app.post("/webhooks/hubspot")
    async def webhook(request: Request):
        body = await request.body()
        secret = os.environ.get("HUBSPOT_APP_SECRET")
        if not secret and not isinstance(st.hs, MockHubSpot):
            # With the real portal anyone who finds the URL could feed it events; only signed calls are taken.
            raise HTTPException(503, "HUBSPOT_APP_SECRET is not set: the real HubSpot client takes signed webhooks only")
        if secret:
            from ..hubspot.real import RealHubSpot
            if not RealHubSpot.verify_signature(secret, body, request.headers.get("X-HubSpot-Signature", "")):
                raise HTTPException(401, "bad signature")
        payload = await request.json()
        if hasattr(st.hs, "enqueue_webhook_payload"):
            st.hs.enqueue_webhook_payload(payload)
        # Respond within HubSpot's 5-second window (H04); scoring runs on the next job tick.
        return JSONResponse({"accepted": len(payload)})

    return app


def _rescore_in_background(db_path, hs: HubSpotClient) -> None:
    """Its own connection (and its own mock over it), like the scheduler's, so it never writes inside a request."""
    conn = connect(db_path)
    try:
        client = MockHubSpot(conn) if isinstance(hs, MockHubSpot) else hs
        Scorer(client, conn, load_live_model(conn), make_llm(conn=conn)).rescore_stale()
    finally:
        conn.close()


_hit_rate = hit_rate          # the name the queue and older callers use


app = create_app()
