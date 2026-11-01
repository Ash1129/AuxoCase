"""Flagged leads: a second look at scored leads, for marketing (llm/flags.py has the reader and the vocabulary).

Which leads get the look: not every arrival, but the ones where a wrong number
costs something: the leads routing is about to hand out (the top of the pool
down to the team's reach), the band either side of the capacity line, and any
lead whose notes reading already disagrees with its odds. A record is read
once per input (the scorer's input hash), so a lead is not re-read until its
notes or fields change. Duplicates are found by code, from the email domain,
with no reader at all.

A flag never changes a score or a route. It waits on the Flagged page for a
person: dismissed (not a problem), a form to fix (marketing's own), or agreed
(the SDR should know). Open flags also go to HubSpot's auxo_review_flag, so a
rep sees them on the record. Whether the flags were worth anything is a
question for the outcome loop: `comparison` sets flagged against unflagged
leads at the same odds, among the leads that were touched and have matured.
"""
from __future__ import annotations

import sqlite3
from collections import Counter
from datetime import timedelta
from typing import Optional

from .hubspot.client import HubSpotClient
from .hubspot.models import Contact
from .llm.client import LLM
from .llm.flags import FLAG_KINDS, HIGH_ODDS, LOW_ODDS, FlagReading, note_for, read_flags, record_text
from .pool import pool_contacts
from .storage.db import clock, dumps, get_setting, loads, now_iso

RESOLUTIONS = {"dismissed": "Not a problem", "form_fix": "A form to fix", "agreed": "Agreed; the SDR should know"}
BAND = 0.04                       # either side of the capacity line, as the scorer's review band
GENERIC_DOMAINS = {"gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "icloud.com", "aol.com", "proton.me", "protonmail.com", "live.com", "me.com"}


def _line(conn: sqlite3.Connection) -> Optional[float]:
    v = get_setting(conn, "capacity_line_probability")
    return loads(v) if v else None


def _reach(hs: HubSpotClient, conn: sqlite3.Connection) -> int:
    from .routing.engine import Router
    return Router(hs, conn).reach()


def _last_score(conn: sqlite3.Connection, contact_id: str) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT input_hash, signals, model_version, probability FROM scores WHERE contact_id=? ORDER BY created_at DESC, id DESC LIMIT 1",
                        (contact_id,)).fetchone()


def candidates(hs: HubSpotClient, conn: sqlite3.Connection) -> list[tuple[Contact, str]]:
    """The scored, unowned leads worth a look, best first, each with why: 'next out', 'at the line' or 'notes disagree'."""
    pool = [c for c in pool_contacts(hs, conn) if c.auxo_probability is not None]
    pool.sort(key=lambda c: (c.auxo_rank or 10 ** 9, -(c.auxo_probability or 0)))
    reach, line = _reach(hs, conn), _line(conn)
    out: list[tuple[Contact, str]] = []
    for i, c in enumerate(pool):
        p = c.auxo_probability or 0.0
        if i < reach:
            out.append((c, "next out"))
        elif line is not None and abs(p - line) <= BAND:
            out.append((c, "at the line"))
        else:
            last = _last_score(conn, c.id)
            d = loads(last["signals"]) if last and last["signals"] else None
            adj = int(d.get("adjustment", 0)) if d else 0
            if (adj < 0 and p >= HIGH_ODDS) or (adj > 0 and p < LOW_ODDS):
                out.append((c, "notes disagree"))
    return out


def _domain(c: Contact) -> str:
    return c.email.split("@")[-1].lower() if c.email and "@" in c.email else ""


def duplicates(pool: list[Contact]) -> dict[str, str]:
    """contact id -> the email, or the name at a company domain, another pool lead already arrived with. Several
    people at one company are several leads; the same person twice (two campaigns, two forms) is a repeat."""
    by_key: dict[str, list[Contact]] = {}
    for c in pool:
        d = _domain(c)
        keys = [c.email.lower()] if c.email else []
        name = f"{c.first_name or ''} {c.last_name or ''}".strip().lower()
        if d and d not in GENERIC_DOMAINS and name:
            keys.append(f"{name}@{d}")
        for k in keys:
            by_key.setdefault(k, []).append(c)
    out = {}
    for k, cs in by_key.items():
        ids = {c.id for c in cs}
        if len(ids) > 1:
            first = min(cs, key=lambda c: (c.createdate or "", c.id))
            for c in cs:
                if c.id != first.id and c.id not in out:          # the earlier arrival is the original; the later ones are the repeats
                    out[c.id] = k
    return out


def _open_kinds(conn: sqlite3.Connection, contact_id: str) -> list[str]:
    return [r["kind"] for r in conn.execute("SELECT kind FROM flags WHERE contact_id=? AND resolved_at IS NULL", (contact_id,))]


def _record_flag(conn: sqlite3.Connection, c: Contact, kind: str, quote: str, p: float, model_version: str, llm: str) -> bool:
    """One open flag a kind a lead; a kind already open, or resolved for this same quote, is not raised again."""
    dup = conn.execute("SELECT 1 FROM flags WHERE contact_id=? AND kind=? AND (resolved_at IS NULL OR quote=?)", (c.id, kind, quote)).fetchone()
    if dup:
        return False
    conn.execute("INSERT INTO flags(contact_id, kind, quote, note, probability, model_version, channel, llm, created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                 (c.id, kind, quote, note_for(kind, quote, p), p, model_version, c.channel or "", llm, now_iso()))
    return True


def run(hs: HubSpotClient, conn: sqlite3.Connection, llm: LLM, limit: int = 25) -> dict:
    """Read up to `limit` candidate records not read before (for this input, by this reader), raise the flags that
    survive the checker, find duplicates by code, and put the open kinds on each lead's HubSpot review flag."""
    cands = candidates(hs, conn)
    pool = [c for c in pool_contacts(hs, conn)]
    dups = duplicates(pool)
    read = raised = 0
    kinds: Counter = Counter()
    touched: dict[str, Contact] = {}
    for c, why in cands:
        p = c.auxo_probability or 0.0
        last = _last_score(conn, c.id)
        version, input_hash = (last["model_version"], last["input_hash"]) if last else (c.auxo_model_version or "", "")
        if c.id in dups and _record_flag(conn, c, "duplicate", dups[c.id], p, version, "code"):
            raised += 1
            kinds["duplicate"] += 1
            touched[c.id] = c
        if read >= limit:
            continue
        if conn.execute("SELECT 1 FROM flag_reads WHERE contact_id=? AND input_hash=? AND llm=?", (c.id, input_hash, llm.name)).fetchone():
            continue
        company = hs.get_company(c.company_id) if c.company_id else None
        reading = read_flags(llm, record_text(c, company), p)
        if reading.error:
            continue                                    # the reader was unavailable; try again next run
        read += 1
        conn.execute("INSERT OR REPLACE INTO flag_reads(contact_id, input_hash, llm, reading, created_at) VALUES(?,?,?,?,?)",
                     (c.id, input_hash, llm.name, dumps({**reading.to_dict(), "why": why}), now_iso()))
        for f in reading.flags:
            if _record_flag(conn, c, f.kind, f.quote, p, version, llm.name):
                raised += 1
                kinds[f.kind] += 1
                touched[c.id] = c
    if touched:
        hs.batch_update_contacts([(cid, {"auxo_review_flag": _review_text(conn, c)}) for cid, c in touched.items()])
    return {"candidates": len(cands), "read": read, "flagged": raised, "kinds": dict(kinds), "reader": llm.name}


def _review_text(conn: sqlite3.Connection, c: Contact) -> str:
    kinds = _open_kinds(conn, c.id)
    flagged = "flagged: " + ", ".join(FLAG_KINDS[k][0].lower() for k in kinds) if kinds else ""
    own = "; ".join(part for part in (c.auxo_review_flag or "").split("; ") if part and not part.startswith("flagged: "))
    return "; ".join(x for x in (own, flagged) if x)[:200]


def resolve(hs: HubSpotClient, conn: sqlite3.Connection, flag_id: int, resolution: str, who: str, note: str = "") -> bool:
    """Close a flag with a resolution and who closed it; the lead's HubSpot review flag drops the kind."""
    if resolution not in RESOLUTIONS:
        raise ValueError("resolution must be one of " + ", ".join(RESOLUTIONS))
    row = conn.execute("SELECT contact_id FROM flags WHERE id=? AND resolved_at IS NULL", (flag_id,)).fetchone()
    if row is None:
        return False
    conn.execute("UPDATE flags SET resolved_at=?, resolution=?, resolved_by=?, resolution_note=? WHERE id=?",
                 (now_iso(), resolution, who, note.strip()[:300], flag_id))
    c = hs.get_contact(row["contact_id"])
    if c is not None:
        hs.batch_update_contacts([(c.id, {"auxo_review_flag": _review_text(conn, c)})])
    return True


def open_flags(hs: HubSpotClient, conn: sqlite3.Connection) -> list[dict]:
    """Open flags, newest first, each with the lead's record for the page to show."""
    out = []
    for r in conn.execute("SELECT * FROM flags WHERE resolved_at IS NULL ORDER BY created_at DESC, id DESC"):
        c = hs.get_contact(r["contact_id"])
        company = hs.get_company(c.company_id) if c and c.company_id else None
        out.append({**dict(r), "c": c, "co": company, "label": FLAG_KINDS.get(r["kind"], (r["kind"], ""))[0],
                    "owned": bool(c and c.hubspot_owner_id)})
    return out


def resolved_flags(conn: sqlite3.Connection, limit: int = 50) -> list[dict]:
    return [{**dict(r), "label": FLAG_KINDS.get(r["kind"], (r["kind"], ""))[0], "resolution_word": RESOLUTIONS.get(r["resolution"], r["resolution"])}
            for r in conn.execute("SELECT * FROM flags WHERE resolved_at IS NOT NULL ORDER BY resolved_at DESC, id DESC LIMIT ?", (limit,))]


def summary(conn: sqlite3.Connection) -> dict:
    """Open flags by kind and by channel, and the month's resolutions."""
    by_kind = Counter({r["kind"]: r["n"] for r in conn.execute("SELECT kind, COUNT(*) n FROM flags WHERE resolved_at IS NULL GROUP BY kind")})
    by_channel = Counter({r["channel"] or "unknown": r["n"] for r in conn.execute("SELECT channel, COUNT(*) n FROM flags WHERE resolved_at IS NULL GROUP BY channel")})
    since = (clock(conn) - timedelta(days=30)).isoformat()
    resolutions = Counter({r["resolution"]: r["n"] for r in conn.execute("SELECT resolution, COUNT(*) n FROM flags WHERE resolved_at >= ? GROUP BY resolution", (since,))})
    return {"open": sum(by_kind.values()),
            "by_kind": [{"kind": k, "label": FLAG_KINDS[k][0], "meaning": FLAG_KINDS[k][1], "n": by_kind.get(k, 0)} for k in FLAG_KINDS if by_kind.get(k)],
            "by_channel": sorted(({"channel": k, "n": n} for k, n in by_channel.items()), key=lambda x: -x["n"]),
            "resolutions": [{"resolution": k, "word": RESOLUTIONS[k], "n": resolutions.get(k, 0)} for k in RESOLUTIONS],
            "reads": conn.execute("SELECT COUNT(*) n FROM flag_reads").fetchone()["n"]}


def comparison(conn: sqlite3.Connection, bands=((0.0, 0.1), (0.1, 0.2), (0.2, 0.4), (0.4, 1.01))) -> list[dict]:
    """Do flagged leads book differently? Among touched leads old enough for a demo to have landed, the demo rate of
    flagged against unflagged leads in each band of their odds at the time. Small cells are shown as such."""
    from .scoring.model import DEMO_MATURITY_DAYS
    mature_before = (clock(conn) - timedelta(days=DEMO_MATURITY_DAYS)).isoformat()
    rows = conn.execute(
        "SELECT o.contact_id, o.demo_booked, s.probability, (SELECT COUNT(*) FROM flags f WHERE f.contact_id=o.contact_id) flagged "
        "FROM outcomes o JOIN scores s ON s.id = (SELECT MAX(id) FROM scores WHERE contact_id=o.contact_id) "
        "WHERE o.worked=1 AND o.lead_created_at <= ?", (mature_before,)).fetchall()
    out = []
    for lo, hi in bands:
        cell = {"band": f"{round(100 * lo)}–{round(100 * min(hi, 1.0))}%", "flagged_n": 0, "flagged_demos": 0, "other_n": 0, "other_demos": 0}
        for r in rows:
            p = r["probability"] or 0.0
            if not (lo <= p < hi):
                continue
            side = "flagged" if r["flagged"] else "other"
            cell[f"{side}_n"] += 1
            cell[f"{side}_demos"] += 1 if r["demo_booked"] else 0
        cell["flagged_rate"] = cell["flagged_demos"] / cell["flagged_n"] if cell["flagged_n"] else None
        cell["other_rate"] = cell["other_demos"] / cell["other_n"] if cell["other_n"] else None
        cell["small"] = cell["flagged_n"] < 30 or cell["other_n"] < 30
        out.append(cell)
    return out
