"""Fill a missing job title or company size before a lead is scored.

BrightPath answer 2: about 40% of leads miss one or the other, and an
enrichment tool is licensed but used inconsistently. Referral and webinar
forms capture least, which is half of why those leads score low (answer 1).
The service asks the tool once per lead, for every lead in the pool that is
missing either field, and records what was asked and what came back
(`enrichment_log`), so "used inconsistently" becomes "used on every lead that
needs it, with a trail".

What the tool returns is written to HubSpot as the tool's values; what it
cannot find stays blank. The model treats blank as its own value
(`seniority=unknown`, `size_band=unknown`), so a lead is never scored as if a
missing title meant a junior one, which is what HubSpot's points model does.

The licensed tool is not named yet. `Enricher` is the one interface it plugs
into; `SimulatedEnricher` stands in on the mock, finding a share of what the
form missed (coverage below is an assumption, to be replaced by the tool's
own match report).
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
from dataclasses import replace
from typing import Optional, Protocol

from ..hubspot.client import HubSpotClient
from ..hubspot.models import Company, Contact
from ..storage.db import dumps, get_setting, loads, now_iso


class Enricher(Protocol):
    name: str

    def lookup(self, contact: Contact, company: Optional[Company], need: list[str]) -> dict:
        """Return any of job_title, seniority, num_employees that the tool found; nothing it did not."""


class NoEnricher:
    name = "none"

    def lookup(self, contact: Contact, company: Optional[Company], need: list[str]) -> dict:
        return {}


class SimulatedEnricher:
    """Mock only: answers from the generator's truth for a fixed share of leads."""
    name = "simulated"
    TITLE_COVERAGE = 0.6
    SIZE_COVERAGE = 0.75

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def lookup(self, contact: Contact, company: Optional[Company], need: list[str]) -> dict:
        r = self.conn.execute("SELECT seniority, job_title, num_employees FROM sim_truth WHERE contact_id=?", (contact.id,)).fetchone()
        if r is None:
            return {}
        found: dict = {}
        if "job_title" in need and _draw(contact.id, "title") < self.TITLE_COVERAGE and r["job_title"]:
            found["job_title"], found["seniority"] = r["job_title"], r["seniority"]
        if "num_employees" in need and _draw(contact.id, "size") < self.SIZE_COVERAGE:
            found["num_employees"] = r["num_employees"]
        return found


def _draw(contact_id: str, what: str) -> float:
    return int(hashlib.sha256(f"{what}:{contact_id}".encode()).hexdigest()[:8], 16) / 0xFFFFFFFF


def make_enricher(conn: sqlite3.Connection, kind: Optional[str] = None, llm=None) -> Enricher:
    """`AUXO_ENRICH`: none | simulated | web, or a comma-separated chain asked in order (`simulated,web`: the tool
    first, the web for what it misses). Unset: the stand-in on generated data, else none. `web` needs a reader
    (`llm`, else the configured one) and keeps only what `web_enrich.verify` finds on the cited page."""
    kinds = [k.strip() for k in (kind or os.environ.get("AUXO_ENRICH", "")).lower().split(",") if k.strip()]
    if not kinds:
        kinds = ["simulated"] if get_setting(conn, "generated_seed") else ["none"]
    built: list = []
    for k in kinds:
        if k == "none":
            continue
        if k == "simulated":
            built.append(SimulatedEnricher(conn))
        elif k == "web":
            from ..llm.client import make_llm
            from .web_enrich import WebEnricher
            built.append(WebEnricher(llm or make_llm(conn=conn)))
        else:
            raise ValueError(f"AUXO_ENRICH: unknown enricher {k!r} (none, simulated, web)")
    if not built:
        return NoEnricher()
    if len(built) == 1:
        return built[0]
    from .web_enrich import ChainEnricher
    return ChainEnricher(built)


def enrich_lead(hs: HubSpotClient, conn: sqlite3.Connection, enricher: Enricher, contact: Contact,
                company: Optional[Company]) -> tuple[Contact, Optional[Company], dict]:
    """The lead as the scorer should see it, plus what was filled. One lookup per lead, ever."""
    need = []
    if not contact.job_title:
        need.append("job_title")
    if company is not None and not company.num_employees:
        need.append("num_employees")
    if not need or enricher.name == "none":
        return contact, company, {}
    row = conn.execute("SELECT filled FROM enrichment_log WHERE contact_id=? ORDER BY id DESC LIMIT 1", (contact.id,)).fetchone()
    if row is not None:
        filled = loads(row["filled"]) or {}        # asked before: apply what came back then, do not ask again
    else:
        filled = enricher.lookup(contact, company, need)
        conn.execute("INSERT INTO enrichment_log(contact_id, asked, filled, provider, created_at) VALUES(?,?,?,?,?)",
                     (contact.id, dumps(need), dumps(filled), enricher.name, now_iso()))
        if "job_title" in filled:
            hs.batch_update_contacts([(contact.id, {"job_title": filled["job_title"], "seniority": filled.get("seniority") or "unknown"})])
        if "num_employees" in filled and company is not None:
            hs.update_company(company.id, {"num_employees": filled["num_employees"]})
    if "job_title" in filled and not contact.job_title:
        contact = replace(contact, job_title=filled["job_title"], seniority=filled.get("seniority") or contact.seniority)
    if "num_employees" in filled and company is not None and not company.num_employees:
        company = replace(company, num_employees=filled["num_employees"])
    return contact, company, filled
