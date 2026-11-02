"""The verdict under each candidate's sign-off (llm/verdict.py): computed once a candidate against a live model, kept
beside the approvals.

The rules run at every page load, so a verdict is never missing; the reader's sentence replaces the fixed wording
once a run has written it for this candidate against this live model with these metrics. A new live model, or a
retrain that changes a candidate's metrics, makes a new key, so the sentence is read again.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Optional

from .llm.client import LLM
from .llm.verdict import Facts, WORDS, facts_for, read_verdict, rules, sentence_for
from .storage.db import loads, now_iso


def _hash(f: Facts) -> str:
    return hashlib.sha256(json.dumps(f.to_dict(), sort_keys=True).encode()).hexdigest()[:16]


def _registry(conn: sqlite3.Connection) -> tuple[Optional[dict], list[dict]]:
    regs = [dict(r) for r in conn.execute("SELECT version, status, metrics FROM model_registry ORDER BY trained_at DESC")]
    for r in regs:
        r["metrics"] = loads(r["metrics"]) or {}
    live = next((r for r in regs if r["status"] == "live"), None)
    return live, [r for r in regs if r["status"] == "candidate"]


def pending(conn: sqlite3.Connection) -> list[tuple[Facts, str]]:
    """The candidates with no reader's sentence for the current live model and metrics."""
    live, waiting = _registry(conn)
    if not live:
        return []
    out = []
    for r in waiting:
        f = facts_for(r["version"], r["metrics"], live["version"], live["metrics"])
        h = _hash(f)
        if not conn.execute("SELECT 1 FROM verdicts WHERE version=? AND input_hash=?", (f.version, h)).fetchone():
            out.append((f, h))
    return out


def run(conn: sqlite3.Connection, llm: LLM) -> dict:
    """Read a sentence for every candidate that lacks one; the rules' wording is stored when the reader's fails."""
    read, kept, errors = 0, 0, []
    for f, h in pending(conn):
        verdict, reasons = rules(f)
        sentence, err = read_verdict(llm, verdict, reasons, f)
        source = "reader" if sentence else "rules"
        if err:
            errors.append({"version": f.version, "error": err})
        with conn:
            conn.execute("INSERT OR REPLACE INTO verdicts(version, live_version, input_hash, verdict, sentence, source, llm, created_at) "
                         "VALUES(?,?,?,?,?,?,?,?)",
                         (f.version, f.live, h, verdict, sentence or sentence_for(verdict, reasons), source, llm.name, now_iso()))
        read += 1
        kept += 1 if sentence else 0
    return {"read": read, "reader_sentences": kept, "reader": llm.name, "errors": errors}


def for_page(conn: sqlite3.Connection, waiting: list[dict], current: Optional[dict]) -> dict[str, dict]:
    """version -> {verdict, word, sentence, source ('reader' or 'rules'), llm, created_at}: the rules now, the reader's
    sentence when one is stored for these facts."""
    out: dict[str, dict] = {}
    if not current:
        return out
    for r in waiting:
        f = facts_for(r["version"], r["metrics"], current["version"], current["metrics"])
        verdict, reasons = rules(f)
        row = conn.execute("SELECT * FROM verdicts WHERE version=? AND input_hash=?", (f.version, _hash(f))).fetchone()
        if row and row["verdict"] == verdict:
            out[r["version"]] = {"verdict": verdict, "word": WORDS[verdict], "sentence": row["sentence"], "source": row["source"],
                                 "llm": row["llm"], "created_at": row["created_at"]}
        else:
            out[r["version"]] = {"verdict": verdict, "word": WORDS[verdict], "sentence": sentence_for(verdict, reasons),
                                 "source": "rules", "llm": None, "created_at": None}
    return out
