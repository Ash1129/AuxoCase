"""Score a batch of leads end to end and write the result back to HubSpot.

The order is Blueprint job 1's: completeness check, sort by channel (a
feature), the deterministic part of the score (the trained model, or the
points table before one is approved), the bounded reading of free text,
a deterministic merge, a code checker, the review decision, the write-back
keyed by contact and version, and a log row per lead. A re-run on unchanged
input is a no-op thanks to `input_hash`, and the notes are read again only
when they changed (or the reader did): the reading does not depend on the model.

When a new model goes live, the pool still carries the old model's scores.
`rescore_stale` works through it from the top rank down, so the leads about
to be routed get the new score first; the scheduler runs it every minute for
as long as it has time, and `run.py approve` runs it to the end.

The score is 0-100 for HubSpot views and equals the calibrated probability
x 100, nudged by at most +/-10 by the free-text reading (and then clipped),
so "62" always means "about a 62% chance of booking a demo if worked" (L04).
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

from ..hubspot.client import HubSpotClient
from ..hubspot.models import Contact
from ..llm.client import LLM, make_llm
from ..llm.reason import write_reason
from ..llm.signals import SignalReading, notes_hash, read_signals
from ..storage.db import dumps, get_setting, loads, now_iso, set_setting
from ..pool import pool_contacts, stages
from .enrich import Enricher, enrich_lead, make_enricher
from .explain import REASON_WORDING, Explanation, explain
from .features import feature_row, input_hash
from .model import DemoModel
from .points import POINTS_VERSION, points_breakdown, points_score

REVIEW_BAND = 0.04            # leads within this probability of the capacity line go to a person (Blueprint job 1 step 9)
PARTNER_DEAL_SIZE = 60000     # partner referrals from companies likely above this ACV go to a person
REQUIRED_FIELDS = ("email", "channel")
RESCORE_PENDING = "rescore_pending"     # set to the model version when it goes live; cleared once the pool carries it


@dataclass
class ScoredLead:
    contact_id: str
    probability: float
    score: float
    p_close: float
    expected_value: float
    reason: str
    reason_source: str
    explanation: Optional[Explanation]
    signals: SignalReading
    base_probability: float
    model_version: str
    review_flag: str = ""
    incomplete: list[str] = field(default_factory=list)
    input_hash: str = ""
    rank: Optional[int] = None
    pool_size: Optional[int] = None
    enriched: dict = field(default_factory=dict)


class Scorer:
    def __init__(self, hs: HubSpotClient, conn: sqlite3.Connection, model: Optional[DemoModel] = None,
                 llm: Optional[LLM] = None, average_acv: float = 24000.0, enricher: Optional[Enricher] = None):
        self.hs, self.conn = hs, conn
        self.model = model
        self.llm = llm or make_llm(conn=conn)
        self.enricher = enricher or make_enricher(conn, llm=self.llm)
        self.average_acv = average_acv

    @property
    def model_version(self) -> str:
        return self.model.version if self.model else POINTS_VERSION

    # ---- one lead ---------------------------------------------------------------
    def score_one(self, c: Contact, cutoff: Optional[float] = None) -> ScoredLead:
        company = self.hs.get_company(c.company_id) if c.company_id else None
        c, company, enriched = enrich_lead(self.hs, self.conn, self.enricher, c, company)
        missing = [f for f in REQUIRED_FIELDS if not getattr(c, f)]
        row = feature_row(c, company)
        h = input_hash({**row, "notes": c.notes or "", "v": self.model_version, "w": REASON_WORDING})

        if self.model is not None:
            from .features import frame
            X = frame([row])
            base_p = float(self.model.predict_proba(X)[0])
            p_close = float(self.model.predict_close_proba(X)[0])
            expl = explain(self.model, row, base_p, cutoff)
            template = expl.reason_text()
        else:
            pts = points_score(c, company)
            base_p = _points_to_probability(pts)
            p_close = 0.2
            expl = None
            bd = points_breakdown(c, company)
            top = sorted(bd.items(), key=lambda kv: -abs(kv[1]))[:3]
            template = "Points model (no trained model yet): " + ", ".join(f"{k} {v:+d}" for k, v in top) + "."

        signals = self._earlier_reading(c) or read_signals(self.llm, c.notes or "")
        p = min(0.99, max(0.01, base_p + signals.adjustment / 100.0))
        if signals.signals:
            template += " Notes mention " + ", ".join(s.type.replace("_", " ") for s in signals.signals) + "."
        if enriched:
            template += " " + " and ".join({"job_title": "Job title", "num_employees": "Company size"}[k]
                                           for k in ("job_title", "num_employees") if k in enriched) + " from the enrichment tool."
        reason, source = write_reason(self.llm, template)
        ev = p * p_close * (company.annual_revenue * 0.004 if company and company.annual_revenue else self.average_acv)

        flag = self._review_flag(c, company, p, cutoff, signals)
        return ScoredLead(contact_id=c.id, probability=round(p, 4), score=round(100 * p, 1), p_close=round(p_close, 4),
                          expected_value=round(ev, 0), reason=reason, reason_source=source, explanation=expl, signals=signals,
                          base_probability=round(base_p, 4), model_version=self.model_version, review_flag=flag,
                          incomplete=missing, input_hash=h, enriched=enriched)

    def _earlier_reading(self, c: Contact) -> Optional[SignalReading]:
        """The lead's last reading, if it was of these same notes by this same reader and it worked."""
        r = self.conn.execute("SELECT signals FROM scores WHERE contact_id=? ORDER BY created_at DESC, id DESC LIMIT 1", (c.id,)).fetchone()
        d = loads(r["signals"]) if r else None
        if not d or d.get("error") or d.get("llm") != self.llm.name or d.get("notes_hash") != notes_hash(c.notes or ""):
            return None
        return SignalReading.from_dict(d)

    def _review_flag(self, c: Contact, company, p: float, cutoff: Optional[float], signals: SignalReading) -> str:
        flags = []
        if signals.unsure or signals.error:
            flags.append("reader unsure" if signals.unsure else "reader unavailable")
        if any(s.type == "gaming" for s in signals.signals):
            flags.append("notes try to influence the score")
        if cutoff is not None and abs(p - cutoff) <= REVIEW_BAND:
            flags.append("near the capacity line")
        if c.channel == "referral" and company and company.num_employees and company.num_employees >= 300:
            flags.append("large partner referral")
        kinds = [r["kind"] for r in self.conn.execute("SELECT kind FROM flags WHERE contact_id=? AND resolved_at IS NULL", (c.id,))]
        if kinds:                                        # marketing's open flags (flags.py) stay on the record through a rescore
            from ..llm.flags import FLAG_KINDS
            flags.append("flagged: " + ", ".join(FLAG_KINDS[k][0].lower() for k in kinds if k in FLAG_KINDS))
        return "; ".join(flags)

    # ---- a batch -----------------------------------------------------------------
    def score_batch(self, contacts: Iterable[Contact], write: bool = True) -> list[ScoredLead]:
        contacts = list(contacts)
        cutoff = _loads_float(get_setting(self.conn, "capacity_line_probability"))
        results = [self.score_one(c) if cutoff is None else self.score_one(c, cutoff) for c in contacts]
        # Rank within the current pool (pool.py), not within the batch.
        pool = {c.id: c for c in pool_contacts(self.hs, self.conn)}
        probs = {cid: (c.auxo_probability or 0.0) for cid, c in pool.items()}
        for r in results:
            if r.contact_id in pool:
                probs[r.contact_id] = r.probability
        ordered = sorted(probs.items(), key=lambda kv: -kv[1])
        rank_of = {cid: i + 1 for i, (cid, _) in enumerate(ordered)}
        for r in results:
            r.rank = rank_of.get(r.contact_id)
            r.pool_size = len(ordered)
        if write:
            self._write_back(results)
        return results

    def _write_back(self, results: list[ScoredLead]) -> None:
        updates, recorded = [], {}
        for r in results:
            # Idempotent against the lead's LATEST score only: if a different model wrote in between (a fallback to
            # the points table, say), the trained model's values must go back to HubSpot even though an older row matches.
            last = self.conn.execute("SELECT id, model_version, input_hash, rank FROM scores WHERE contact_id=? ORDER BY created_at DESC, id DESC LIMIT 1",
                                     (r.contact_id,)).fetchone()
            if last and last["model_version"] == r.model_version and last["input_hash"] == r.input_hash:
                if last["rank"] != r.rank:          # same score, new place in the pool: refresh the rank only
                    self.conn.execute("UPDATE scores SET rank=?, pool_size=? WHERE id=?", (r.rank, r.pool_size, last["id"]))
                    updates.append((r.contact_id, {"auxo_rank": r.rank}))
                continue
            route = "incomplete" if r.incomplete else ("review" if r.review_flag else "")
            props = {
                "auxo_score": r.score, "auxo_probability": r.probability, "auxo_rank": r.rank, "auxo_reason": r.reason,
                "auxo_model_version": r.model_version, "auxo_scored_at": now_iso(), "auxo_review_flag": r.review_flag,
            }
            if route:
                props["auxo_route"] = route
            if r.incomplete:
                props["auxo_review_flag"] = "missing: " + ", ".join(r.incomplete)
            updates.append((r.contact_id, props))
            recorded[r.contact_id] = (r.model_version, r.input_hash)
            self.conn.execute(
                "INSERT OR IGNORE INTO scores(contact_id, model_version, probability, score, rank, pool_size, reason, contributions, signals, llm_adjustment, checker, review_flag, route, holdout, input_hash, created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (r.contact_id, r.model_version, r.probability, r.score, r.rank, r.pool_size, r.reason,
                 dumps(r.explanation.contributions if r.explanation else []), dumps(r.signals.to_dict()), r.signals.adjustment,
                 dumps({"dropped": len(r.signals.dropped), "reason_source": r.reason_source}), r.review_flag, route, 0, r.input_hash, now_iso()))
            if self.conn.execute("SELECT changes()").fetchone()[0] == 0:
                # the same model saw the same input before; make that row the latest again so the next run is a no-op
                self.conn.execute("UPDATE scores SET created_at=?, rank=?, pool_size=? WHERE contact_id=? AND model_version=? AND input_hash=?",
                                  (now_iso(), r.rank, r.pool_size, r.contact_id, r.model_version, r.input_hash))
        if updates:
            res = self.hs.batch_update_contacts(updates)
            if res.failed:
                self.conn.execute("INSERT INTO run_log(job, started_at, finished_at, status, detail) VALUES(?,?,?,?,?)",
                                  ("score.write_back", now_iso(), now_iso(), "partial", dumps(res.failed)))
                # HubSpot does not have these scores, so the log must not say it does: without the row, the next run
                # sees the input as new and writes it again instead of skipping it as already written.
                for cid in res.failed:
                    if cid in recorded:
                        self.conn.execute("DELETE FROM scores WHERE contact_id=? AND model_version=? AND input_hash=?", (cid, *recorded[cid]))

    # ---- the continuous loop -------------------------------------------------------
    def process_events(self, max_events: int = 100) -> list[ScoredLead]:
        """Webhook-style: re-read each changed contact and score it; duplicates are harmless (input hash)."""
        events = self.hs.poll_events(max_events)
        ids = []
        for e in events:
            if e.subscription_type.startswith("contact.") and e.object_id not in ids:
                if e.property_name.startswith("auxo_"):
                    continue
                ids.append(e.object_id)
        in_pool = stages(self.conn)
        contacts = [c for c in (self.hs.get_contact(i) for i in ids) if c is not None and not c.hubspot_owner_id and c.lifecycle_stage in in_pool]
        return self.score_batch(contacts) if contacts else []

    def refresh_ranks(self) -> int:
        """Rewrite auxo_rank for the pool as it stands; ranks move as leads arrive and leave without a re-score."""
        pool = sorted(pool_contacts(self.hs, self.conn), key=lambda c: (-(c.auxo_probability or 0), c.createdate))
        updates = [(c.id, {"auxo_rank": i + 1}) for i, c in enumerate(pool) if c.auxo_probability is not None and c.auxo_rank != i + 1]
        if updates:
            self.hs.batch_update_contacts(updates)
        return len(updates)

    def rescore_pool(self) -> list[ScoredLead]:
        out = self.score_batch(pool_contacts(self.hs, self.conn))
        if get_setting(self.conn, RESCORE_PENDING) == self.model_version:
            self.conn.execute("DELETE FROM settings WHERE key=?", (RESCORE_PENDING,))   # the whole pool is on it now
        return out

    def stale_pool(self) -> list[Contact]:
        """Pool leads scored by another model than this one (or not scored at all), best-ranked first, then the
        unranked newest first: a lead just let into the pool is worth more while it is fresh."""
        stale = [c for c in pool_contacts(self.hs, self.conn) if c.auxo_model_version != self.model_version]
        return sorted(stale, key=lambda c: (c.auxo_rank is None, c.auxo_rank or 0, -c.createdate.timestamp()))

    def rescore_stale(self, budget_s: Optional[float] = None, chunk: int = 10) -> dict:
        """Rescore leads still on an older model, a chunk at a time, until done or `budget_s` seconds have gone."""
        if get_setting(self.conn, RESCORE_PENDING) is None:
            return {"pending": False}
        stale, done, started = self.stale_pool(), 0, time.monotonic()
        for i in range(0, len(stale), chunk):
            if budget_s is not None and time.monotonic() - started >= budget_s:
                break
            done += len(self.score_batch(stale[i:i + chunk]))
        left = len(stale) - done
        if left == 0:
            self.conn.execute("DELETE FROM settings WHERE key=?", (RESCORE_PENDING,))
        return {"pending": left > 0, "model": self.model_version, "rescored": done, "left": left}


def _points_to_probability(points: int) -> float:
    """Cold-start map from the points table to a probability, anchored on the brief: ~36% of worked MQLs book."""
    import math
    return 1 / (1 + math.exp(-(points - 55) / 12))


def _loads_float(s: Optional[str]) -> Optional[float]:
    if not s or s == "null":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def load_live_model(conn: sqlite3.Connection) -> Optional[DemoModel]:
    row = conn.execute("SELECT artifact_path FROM model_registry WHERE status='live' ORDER BY promoted_at DESC LIMIT 1").fetchone()
    if not row:
        return None
    try:
        return DemoModel.load(Path(row["artifact_path"]))
    except Exception:
        conn.execute("INSERT INTO run_log(job, started_at, finished_at, status, detail) VALUES(?,?,?,?,?)",
                     ("model.load", now_iso(), now_iso(), "failed", row["artifact_path"]))
        return None
