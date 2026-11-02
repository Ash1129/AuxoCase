"""The verdict under each candidate's sign-off (llm/verdict.py), and the automatic sign-off that acts on it.

The rules run at every page load, so a verdict is never missing; the reader's sentence replaces the fixed wording
once a run has written it for this candidate against this live model with these metrics. A new live model, or a
retrain that changes a candidate's metrics, makes a new key, so the sentence is read again.

Automatic sign-off (on unless switched off on the Model page): each run, after the sentences are read, the
candidates the rules send back are sent back, and of those the rules approve the one whose picks book the most
demos goes live, signed for both teams by "Automatic sign-off (the rules)" with the sentence as its note; the other
approvable ones are sent back as not the best of the round, so one version goes live a run and the pool is rescored
once. The approvals and send-backs are ordinary rows, so Decisions shows them like any person's.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Optional

from .feedback import loop as fb
from .llm.client import LLM
from .llm.verdict import Facts, WORDS, facts_for, read_verdict, rules, sentence_for
from .storage.db import get_setting, loads, now_iso, set_setting

AUTO_KEY = "auto_signoff"                       # "1" or "0"; absent means on
AUTO_NAME = "Automatic sign-off (the rules)"    # the approver on the rows it writes


def auto_on(conn: sqlite3.Connection) -> bool:
    return get_setting(conn, AUTO_KEY) != "0"


def set_auto(conn: sqlite3.Connection, on: bool, who: str) -> None:
    with conn:
        set_setting(conn, AUTO_KEY, "1" if on else "0")
        set_setting(conn, AUTO_KEY + "_by", who)
        set_setting(conn, AUTO_KEY + "_at", now_iso())


def auto_state(conn: sqlite3.Connection) -> dict:
    """on, by, at (who last threw the switch), recent: the automatic decisions, newest first."""
    rows = [dict(r) for r in conn.execute("SELECT version, decision, note, created_at FROM approvals WHERE approver=? ORDER BY id DESC LIMIT 12",
                                          (AUTO_NAME,))]
    seen, recent = set(), []
    for r in rows:                                  # one line a version: the promotion shows once, not once a team
        if r["version"] not in seen:
            seen.add(r["version"])
            recent.append(r)
    return {"on": auto_on(conn), "by": get_setting(conn, AUTO_KEY + "_by"), "at": get_setting(conn, AUTO_KEY + "_at"), "recent": recent}


def _hash(f: Facts) -> str:
    return hashlib.sha256(json.dumps(f.to_dict(), sort_keys=True).encode()).hexdigest()[:16]


def _registry(conn: sqlite3.Connection) -> tuple[Optional[dict], list[dict]]:
    regs = [dict(r) for r in conn.execute("SELECT version, status, metrics, trained_at FROM model_registry ORDER BY trained_at DESC")]
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
    """Read a sentence for every candidate that lacks one (the rules' wording is stored when the reader's fails), then,
    with automatic sign-off on, act on the verdicts: went_live is the version promoted this run or None."""
    out = read_sentences(conn, llm)
    out.update(decide(conn) if auto_on(conn) else {"auto": False, "went_live": None, "sent_back": []})
    return out


def read_sentences(conn: sqlite3.Connection, llm: LLM) -> dict:
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


def decide(conn: sqlite3.Connection) -> dict:
    """The automatic sign-off: send back what the rules send back; of what they approve, promote the one whose picks
    book the most demos (newest first on a tie) and send the rest back as not the best of the round."""
    live, waiting = _registry(conn)
    if not live:
        return {"auto": True, "went_live": None, "sent_back": []}
    lines = for_page(conn, waiting, live)
    sent_back, approvable = [], []
    for r in waiting:
        v = lines[r["version"]]
        if v["verdict"] == "approve":
            approvable.append(r)
        else:
            fb.approve(conn, r["version"], "marketing", AUTO_NAME, "send_back", v["sentence"])
            sent_back.append(r["version"])
    went_live = None
    if approvable:
        best = max(approvable, key=lambda r: (r["metrics"].get("lift_at_k") or 0, r.get("trained_at") or ""))
        note = lines[best["version"]]["sentence"]
        fb.approve(conn, best["version"], "marketing", AUTO_NAME, "approve", note)
        res = fb.approve(conn, best["version"], "sales", AUTO_NAME, "approve", note)
        went_live = best["version"] if res["status"] == "live" else None
        for r in approvable:
            if r is not best:
                fb.approve(conn, r["version"], "marketing", AUTO_NAME, "send_back",
                           f"Not the best of this round: {best['version']} went live instead. {lines[r['version']]['sentence']}")
                sent_back.append(r["version"])
    conn.commit()
    return {"auto": True, "went_live": went_live, "sent_back": sent_back}


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
