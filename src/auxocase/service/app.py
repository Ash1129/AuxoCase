"""The web service: a HubSpot-shaped webhook receiver, a JSON API, and the pages people use.

Who sees what:
- SDRs: their queue, best first, each lead with its probability, rank, the
  plain-English reason and the signals' quoted words; actions are bounded
  (worked / skipped with a reason / junk / promote or demote by a few places)
  and every action is logged with the explanation that was on screen (L12).
- Sales manager: the held queue and the stale-assignment list (speed to lead).
- Marketing and sales leads: the model page (live vs candidate metrics, the
  calibration table, the channel p-chart) and the two-team approval.

In production HubSpot calls `/webhooks/hubspot` (signature checked, H04) and
the SDR pages would be embedded as a HubSpot CRM card; here the same pages
run standalone against the mock.
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ..feedback import loop as fb
from ..hubspot.client import HubSpotClient
from ..hubspot.mock import MockHubSpot
from ..llm.client import make_llm
from ..routing.engine import Router
from ..scoring.pipeline import Scorer, load_live_model
from ..storage.db import DEFAULT_DB, connect, dumps, get_setting, loads, now_iso
from . import scheduler as sched

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
MAX_RANK_MOVE = 5    # L12: a bounded override buys adoption without handing the queue back to gut feel


class State:
    def __init__(self, db_path=DEFAULT_DB):
        self.conn: sqlite3.Connection = connect(db_path)
        self.hs: HubSpotClient = _make_client(self.conn)
        self.llm = make_llm()
        self.model = load_live_model(self.conn)

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
    app.state.st = st
    app.state.scheduler = runner

    def ctx(request: Request, **kw):
        live = fb.live_version(st.conn)
        return {"request": request, "live": live, "llm": st.llm.name, "scheduler": "on" if runner and runner.running else "off",
                "line": loads(get_setting(st.conn, "capacity_line_probability")), **kw}

    # ---------------- pages ----------------
    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
        owners = st.hs.list_owners()
        counts = st.hs.waiting_lead_counts()
        pool = st.router().pool()
        held = st.conn.execute("SELECT COUNT(*) n FROM held_queue WHERE released_at IS NULL").fetchone()["n"]
        runs = [dict(r) for r in st.conn.execute("SELECT * FROM run_log ORDER BY id DESC LIMIT 8").fetchall()]
        return TEMPLATES.TemplateResponse(request, "home.html", ctx(request, owners=owners, counts=counts, pool=pool[:20], pool_size=len(pool), held=held, runs=runs))

    @app.get("/queue/{owner_id}", response_class=HTMLResponse)
    def queue(request: Request, owner_id: str):
        owner = next((o for o in st.hs.list_owners() if o.id == owner_id), None)
        if not owner:
            raise HTTPException(404)
        leads = [c for c in st.hs.list_contacts() if c.hubspot_owner_id == owner_id and c.hs_lead_status in ("", "NEW", "OPEN", "IN_PROGRESS", "ATTEMPTED_TO_CONTACT", "CONNECTED")]
        acted = {r["contact_id"] for r in st.conn.execute("SELECT contact_id FROM overrides WHERE owner_id=?", (owner_id,)).fetchall()}
        leads = [c for c in leads if c.id not in acted]
        leads.sort(key=lambda c: -(c.auxo_probability or 0))
        hit = _hit_rate(st.conn)
        return TEMPLATES.TemplateResponse(request, "queue.html", ctx(request, owner=owner, leads=leads, signals=_signals_for(st.conn, [c.id for c in leads]), hit=hit))

    @app.get("/lead/{contact_id}", response_class=HTMLResponse)
    def lead(request: Request, contact_id: str):
        c = st.hs.get_contact(contact_id)
        if not c:
            raise HTTPException(404)
        co = st.hs.get_company(c.company_id) if c.company_id else None
        score = st.conn.execute("SELECT * FROM scores WHERE contact_id=? ORDER BY id DESC LIMIT 1", (contact_id,)).fetchone()
        score = dict(score) if score else None
        if score:
            score["contributions"] = loads(score["contributions"]) or []
            score["signals"] = loads(score["signals"]) or {}
        history = [dict(r) for r in st.conn.execute("SELECT * FROM overrides WHERE contact_id=? ORDER BY id", (contact_id,)).fetchall()]
        routing = [dict(r) for r in st.conn.execute("SELECT * FROM routing_log WHERE contact_id=? ORDER BY id", (contact_id,)).fetchall()]
        return TEMPLATES.TemplateResponse(request, "lead.html", ctx(request, c=c, co=co, score=score, history=history, routing=routing))

    @app.post("/lead/{contact_id}/action")
    def lead_action(contact_id: str, owner_id: str = Form(...), action: str = Form(...), note: str = Form(""), move: int = Form(0)):
        c = st.hs.get_contact(contact_id)
        if not c:
            raise HTTPException(404)
        if action not in ("worked", "skipped", "junk", "promote", "demote"):
            raise HTTPException(400, "unknown action")
        move = max(-MAX_RANK_MOVE, min(MAX_RANK_MOVE, move))
        st.conn.execute("INSERT INTO overrides(contact_id, owner_id, action, note, score_at_time, rank_at_time, created_at) VALUES(?,?,?,?,?,?,?)",
                        (contact_id, owner_id, action, note, c.auxo_score, c.auxo_rank, now_iso()))
        if action == "worked":
            st.hs.batch_update_contacts([(contact_id, {"hs_lead_status": "IN_PROGRESS"})])
            st.conn.execute("INSERT INTO outcomes(contact_id, worked, first_touch_at, updated_at) VALUES(?,?,?,?) ON CONFLICT(contact_id) DO UPDATE SET worked=1, first_touch_at=COALESCE(outcomes.first_touch_at, excluded.first_touch_at), updated_at=excluded.updated_at",
                            (contact_id, 1, now_iso(), now_iso()))
        elif action == "junk":
            st.hs.batch_update_contacts([(contact_id, {"hs_lead_status": "UNQUALIFIED"})])
            st.conn.execute("INSERT INTO outcomes(contact_id, worked, first_touch_at, updated_at) VALUES(?,?,?,?) ON CONFLICT(contact_id) DO UPDATE SET worked=1, updated_at=excluded.updated_at",
                            (contact_id, 1, now_iso(), now_iso()))
        elif action in ("promote", "demote") and c.auxo_rank:
            new_rank = max(1, c.auxo_rank - move) if action == "promote" else c.auxo_rank + move
            st.hs.batch_update_contacts([(contact_id, {"auxo_rank": new_rank, "auxo_review_flag": f"rank moved by {owner_id}: {note}"[:200]})])
        if action in ("worked", "junk"):
            # The lead has had its first touch, so the SDR's slot is free: refill it now rather than at the next scheduled pass.
            st.router().run()
        return RedirectResponse(f"/queue/{owner_id}", status_code=303)

    @app.get("/held", response_class=HTMLResponse)
    def held(request: Request):
        rows = [dict(r) for r in st.conn.execute("SELECT * FROM held_queue WHERE released_at IS NULL ORDER BY held_at").fetchall()]
        leads = {r["contact_id"]: st.hs.get_contact(r["contact_id"]) for r in rows}
        owners = st.hs.list_owners()
        counts = st.hs.waiting_lead_counts()
        stale = st.router().stale_assignments()
        return TEMPLATES.TemplateResponse(request, "held.html", ctx(request, rows=rows, leads=leads, owners=owners, counts=counts, stale=stale))

    @app.post("/held/{contact_id}/release")
    def release(contact_id: str, owner_id: str = Form(...), released_by: str = Form(...), note: str = Form("")):
        ok = st.router().release_held(contact_id, owner_id, released_by, note)
        if not ok:
            raise HTTPException(409, "lead already has an owner")
        return RedirectResponse("/held", status_code=303)

    @app.get("/model", response_class=HTMLResponse)
    def model_page(request: Request):
        regs = [dict(r) for r in st.conn.execute("SELECT * FROM model_registry ORDER BY trained_at DESC").fetchall()]
        for r in regs:
            r["metrics"] = loads(r["metrics"]) or {}
            r["approvals"] = [dict(a) for a in st.conn.execute("SELECT * FROM approvals WHERE version=? ORDER BY id", (r["version"],)).fetchall()]
        monitor = [dict(r) for r in st.conn.execute("SELECT * FROM channel_monitor WHERE created_at = (SELECT MAX(created_at) FROM channel_monitor) ORDER BY period, channel").fetchall()]
        overrides = st.conn.execute("SELECT action, COUNT(*) n FROM overrides GROUP BY action").fetchall()
        shadow = fb.shadow_comparison(st.hs, st.conn) if any(r["status"] == "shadow" for r in regs) else None
        speed = fb.speed_to_lead(st.conn)
        return TEMPLATES.TemplateResponse(request, "model.html", ctx(request, regs=regs, monitor=monitor, overrides=[dict(o) for o in overrides], shadow=shadow, speed=speed, hit=_hit_rate(st.conn)))

    @app.get("/decisions", response_class=HTMLResponse)
    def decisions(request: Request, limit: int = 500):
        """One place for every human call: model approvals and send-backs, and leads SDRs worked or turned down."""
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
        titles = {}
        for cid in {o["contact_id"] for o in leads}:
            c = st.hs.get_contact(cid)
            titles[cid] = c.job_title if c else None
        return TEMPLATES.TemplateResponse(request, "decisions.html", ctx(request, models=models, leads=leads, counts=counts,
                                                                          owners=owners, titles=titles, limit=limit))

    @app.post("/model/{version}/approve")
    def approve(version: str, team: str = Form(...), approver: str = Form(...), decision: str = Form(...), note: str = Form("")):
        try:
            fb.approve(st.conn, version, team, approver, decision, note)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return RedirectResponse("/model", status_code=303)

    # ---------------- jobs (the three Blueprint jobs, callable from the UI, the CLI or a scheduler) ----------------
    @app.post("/jobs/score")
    def job_score(mode: str = "events"):
        sc = st.scorer()
        res = sc.process_events() if mode == "events" else sc.rescore_pool()
        return {"scored": len(res), "model_version": sc.model_version}

    @app.post("/jobs/route")
    def job_route():
        d = st.router().run()
        return {"decisions": {k: sum(1 for x in d if x.decision == k) for k in {x.decision for x in d}}}

    @app.post("/jobs/sync")
    def job_sync():
        return fb.sync_outcomes(st.hs, st.conn)

    @app.post("/jobs/monitor")
    def job_monitor():
        pts = fb.channel_monitor(st.hs, st.conn)
        return {"points": [p.__dict__ for p in pts]}

    @app.post("/jobs/train")
    def job_train(notes: str = ""):
        try:
            model, m = fb.train_candidate(st.hs, st.conn, notes=notes)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"version": model.version, "family": model.family, "metrics": m.to_dict()}

    @app.post("/jobs/realert")
    def job_realert():
        return {"reminders": st.router().re_alert_stale()}

    # ---------------- HubSpot webhook (production path) ----------------
    @app.post("/webhooks/hubspot")
    async def webhook(request: Request):
        body = await request.body()
        secret = os.environ.get("HUBSPOT_APP_SECRET")
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


def _signals_for(conn, ids: list[str]) -> dict[str, dict]:
    out = {}
    for cid in ids:
        r = conn.execute("SELECT signals FROM scores WHERE contact_id=? ORDER BY id DESC LIMIT 1", (cid,)).fetchone()
        out[cid] = (loads(r["signals"]) or {}) if r else {}
    return out


def _hit_rate(conn) -> Optional[dict]:
    """The model's record this period: of assigned leads that were worked, how many booked (L12: show the hit rate beside any miss)."""
    r = conn.execute("SELECT COUNT(*) n, SUM(demo_booked) d FROM outcomes o JOIN routing_log r ON r.contact_id=o.contact_id WHERE o.worked=1 AND r.decision IN ('assigned','holdout_assigned')").fetchone()
    if not r or not r["n"]:
        return None
    return {"n": r["n"], "booked": r["d"] or 0, "rate": round((r["d"] or 0) / r["n"], 3)}


app = create_app()
