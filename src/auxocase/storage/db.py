"""One SQLite file for everything local.

Tables prefixed `hs_` belong to the mock HubSpot (they stand in for the CRM
and are dropped when the real client is used). The rest are the service's own
records: every score with its explanation, every routing decision, every SDR
override, every model version and approval, and the outcome labels joined
back to leads. These are what make the system auditable (Blueprint: "each
run is logged with its channel, rule score, AI signals, checker result and any
human change").
"""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Optional

DEFAULT_DB = Path(os.environ.get("AUXO_DB", "data/auxocase.db"))

SCHEMA = """
-- ---------- mock HubSpot ----------
CREATE TABLE IF NOT EXISTS hs_companies (
  id TEXT PRIMARY KEY, name TEXT, domain TEXT, industry TEXT, num_employees INTEGER,
  annual_revenue REAL, country TEXT, uses_payroll_vendor TEXT
);
CREATE TABLE IF NOT EXISTS hs_contacts (
  id TEXT PRIMARY KEY, email TEXT, first_name TEXT, last_name TEXT, job_title TEXT, seniority TEXT,
  company_id TEXT, country TEXT, lifecycle_stage TEXT, hs_lead_status TEXT,
  hs_analytics_source TEXT, hs_analytics_source_data_1 TEXT, hs_analytics_source_data_2 TEXT, channel TEXT,
  hs_analytics_num_page_views INTEGER, hs_analytics_num_visits INTEGER, num_form_submissions INTEGER,
  pricing_page_views INTEGER, webinar_registered INTEGER, webinar_attended INTEGER, referral_partner TEXT,
  email_clicks_30d INTEGER, notes TEXT, hubspot_score INTEGER, hubspot_owner_id TEXT,
  createdate TEXT, lastmodifieddate TEXT,
  auxo_score REAL, auxo_probability REAL, auxo_rank INTEGER, auxo_reason TEXT, auxo_model_version TEXT,
  auxo_scored_at TEXT, auxo_route TEXT, auxo_review_flag TEXT, auxo_holdout INTEGER,
  auxo_email_subject TEXT, auxo_email_body TEXT, auxo_below_gate INTEGER
);
CREATE INDEX IF NOT EXISTS hs_contacts_modified ON hs_contacts(lastmodifieddate);
CREATE INDEX IF NOT EXISTS hs_contacts_owner ON hs_contacts(hubspot_owner_id);
CREATE INDEX IF NOT EXISTS hs_contacts_created ON hs_contacts(createdate);
CREATE TABLE IF NOT EXISTS hs_deals (
  id TEXT PRIMARY KEY, contact_id TEXT, dealstage TEXT, amount REAL, createdate TEXT, closedate TEXT,
  hs_is_closed_won INTEGER, owner_id TEXT, lastmodifieddate TEXT, closed_lost_reason TEXT
);
CREATE INDEX IF NOT EXISTS hs_deals_contact ON hs_deals(contact_id);
CREATE TABLE IF NOT EXISTS loss_reasons (
  deal_id TEXT PRIMARY KEY, contact_id TEXT, category TEXT, quote TEXT, source TEXT, raw TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS enrichment_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, contact_id TEXT, asked TEXT, filled TEXT, provider TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS sim_truth (
  contact_id TEXT PRIMARY KEY, seniority TEXT, job_title TEXT, num_employees INTEGER
);
CREATE TABLE IF NOT EXISTS hs_meetings (
  id TEXT PRIMARY KEY, contact_id TEXT, owner_id TEXT, start_time TEXT, outcome TEXT, createdate TEXT
);
CREATE INDEX IF NOT EXISTS hs_meetings_contact ON hs_meetings(contact_id);
CREATE INDEX IF NOT EXISTS hs_contacts_stage ON hs_contacts(lifecycle_stage);
CREATE TABLE IF NOT EXISTS hs_owners (
  id TEXT PRIMARY KEY, name TEXT, email TEXT, active INTEGER, capacity_limit INTEGER, team TEXT, meetings_link TEXT
);
CREATE TABLE IF NOT EXISTS hs_engagements (
  id TEXT PRIMARY KEY, contact_id TEXT, type TEXT, direction TEXT, timestamp TEXT, owner_id TEXT, subject TEXT,
  connected INTEGER, logged_at TEXT
);
CREATE INDEX IF NOT EXISTS hs_engagements_contact ON hs_engagements(contact_id);
CREATE INDEX IF NOT EXISTS hs_engagements_logged ON hs_engagements(logged_at);
CREATE TABLE IF NOT EXISTS hs_sequence_enrollments (
  id TEXT PRIMARY KEY, contact_id TEXT, sequence_id TEXT, sender_email TEXT, enrolled_at TEXT, status TEXT
);
CREATE TABLE IF NOT EXISTS hs_tasks (
  id TEXT PRIMARY KEY, contact_id TEXT, owner_id TEXT, subject TEXT, body TEXT, status TEXT, created_at TEXT, due_at TEXT
);
CREATE TABLE IF NOT EXISTS hs_events (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT, object_id TEXT, subscription_type TEXT,
  property_name TEXT, occurred_at TEXT, delivered INTEGER DEFAULT 0, attempt INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS hs_property_history (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, object_type TEXT, object_id TEXT, property TEXT, value TEXT, changed_at TEXT, source TEXT
);
CREATE INDEX IF NOT EXISTS hs_property_history_object ON hs_property_history(object_id, property);
CREATE TABLE IF NOT EXISTS hs_properties (name TEXT PRIMARY KEY, type TEXT, created_at TEXT);

-- ---------- service records ----------
CREATE TABLE IF NOT EXISTS scores (
  id INTEGER PRIMARY KEY AUTOINCREMENT, contact_id TEXT, model_version TEXT, probability REAL, score REAL,
  rank INTEGER, pool_size INTEGER, reason TEXT, contributions TEXT, signals TEXT, llm_adjustment REAL,
  checker TEXT, review_flag TEXT, route TEXT, holdout INTEGER, input_hash TEXT, created_at TEXT
);
CREATE INDEX IF NOT EXISTS scores_contact ON scores(contact_id);
CREATE UNIQUE INDEX IF NOT EXISTS scores_idem ON scores(contact_id, model_version, input_hash);
CREATE TABLE IF NOT EXISTS routing_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, contact_id TEXT, owner_id TEXT, decision TEXT, reason TEXT,
  open_count_at_decision INTEGER, capacity_limit INTEGER, task_id TEXT, created_at TEXT, probability REAL, below_gate INTEGER
);
CREATE TABLE IF NOT EXISTS held_queue (
  contact_id TEXT PRIMARY KEY, held_at TEXT, reason TEXT, released_at TEXT, released_by TEXT, released_to TEXT
);
CREATE TABLE IF NOT EXISTS rotation (team TEXT PRIMARY KEY, last_owner_id TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS overrides (
  id INTEGER PRIMARY KEY AUTOINCREMENT, contact_id TEXT, owner_id TEXT, action TEXT, note TEXT,
  score_at_time REAL, rank_at_time INTEGER, created_at TEXT
);
CREATE TABLE IF NOT EXISTS review_decisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, contact_id TEXT, reviewer TEXT, decision TEXT, new_score REAL, note TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS outcomes (
  contact_id TEXT PRIMARY KEY, worked INTEGER DEFAULT 0, first_touch_at TEXT, demo_booked INTEGER DEFAULT 0,
  demo_booked_at TEXT, closed_won INTEGER DEFAULT 0, closed_won_at TEXT, closed_lost INTEGER DEFAULT 0,
  closed_lost_at TEXT, amount REAL, label_as_of TEXT, updated_at TEXT, lead_created_at TEXT
);
CREATE TABLE IF NOT EXISTS model_registry (
  version TEXT PRIMARY KEY, family TEXT, trained_at TEXT, train_rows INTEGER, positives INTEGER,
  metrics TEXT, feature_names TEXT, artifact_path TEXT, status TEXT, notes TEXT, promoted_at TEXT
);
CREATE TABLE IF NOT EXISTS approvals (
  id INTEGER PRIMARY KEY AUTOINCREMENT, version TEXT, team TEXT, approver TEXT, decision TEXT, note TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS channel_monitor (
  id INTEGER PRIMARY KEY AUTOINCREMENT, period TEXT, channel TEXT, n INTEGER, demos INTEGER, demo_rate REAL,
  predicted_rate REAL, lcl REAL, ucl REAL, flag TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS run_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, job TEXT, started_at TEXT, finished_at TEXT, status TEXT, detail TEXT
);
CREATE TABLE IF NOT EXISTS touches (
  contact_id TEXT PRIMARY KEY, owner_id TEXT, engagement_id TEXT, type TEXT, touched_at TEXT, status_set TEXT, detected_at TEXT
);
CREATE TABLE IF NOT EXISTS outreach (
  contact_id TEXT PRIMARY KEY, owner_id TEXT, subject TEXT, body TEXT, source TEXT, rejected TEXT,
  sequence_id TEXT, enrollment_id TEXT, status TEXT, created_at TEXT
);
-- The follow-up (step 2) and the last email (step 3) the service sends for the SDR once the first email has gone
-- (routing/followups.py): due at due_at, then sent (with the draft), stopped (reason), or skipped (the SDR sent it).
CREATE TABLE IF NOT EXISTS followups (
  contact_id TEXT, step INTEGER, owner_id TEXT, due_at TEXT, status TEXT, sent_at TEXT, subject TEXT, body TEXT, source TEXT,
  reason TEXT, created_at TEXT, updated_at TEXT, PRIMARY KEY (contact_id, step)
);
CREATE INDEX IF NOT EXISTS followups_due ON followups(status, due_at);
-- A lead's reply read before the SDR opens it (routing/replies.py): what it is, the answer drafted when one is due,
-- and what was done (closed, follow-ups moved, handed to the SDR).
CREATE TABLE IF NOT EXISTS replies (
  id INTEGER PRIMARY KEY AUTOINCREMENT, contact_id TEXT, engagement_id TEXT UNIQUE, owner_id TEXT, kind TEXT, summary TEXT, until TEXT,
  subject TEXT, body TEXT, source TEXT, rejected TEXT, acted TEXT, replied_at TEXT, created_at TEXT
);
CREATE INDEX IF NOT EXISTS replies_contact ON replies(contact_id);
-- Demos booked for the SDR (routing/scheduling.py): per reply handled, the times offered (JSON), the time booked and
-- HubSpot's meeting, status offered | clash | booked.
CREATE TABLE IF NOT EXISTS bookings (
  id INTEGER PRIMARY KEY AUTOINCREMENT, contact_id TEXT, engagement_id TEXT, owner_id TEXT, meeting_id TEXT, start_at TEXT,
  offered TEXT, status TEXT, created_at TEXT
);
CREATE INDEX IF NOT EXISTS bookings_contact ON bookings(contact_id);
-- An SDR's rough call notes written up (routing/callnotes.py): the summary, outcome, facts learned (JSON), the next
-- step with its day, the follow-up drafted from the call, and what was written to HubSpot.
CREATE TABLE IF NOT EXISTS call_notes (
  id INTEGER PRIMARY KEY AUTOINCREMENT, contact_id TEXT, engagement_id TEXT UNIQUE, owner_id TEXT, summary TEXT, outcome TEXT, facts TEXT,
  next_step TEXT, next_when TEXT, subject TEXT, body TEXT, source TEXT, rejected TEXT, acted TEXT, called_at TEXT, created_at TEXT
);
CREATE INDEX IF NOT EXISTS call_notes_contact ON call_notes(contact_id);
-- Phase 3: leads below the capacity line put in an automated sequence (arm 'emailed') or left to the ordinary
-- process as the comparison (arm 'control'). One row per lead, written when the lead became eligible.
CREATE TABLE IF NOT EXISTS tier2 (
  contact_id TEXT PRIMARY KEY, arm TEXT, status TEXT, probability REAL, rank INTEGER, line_probability REAL,
  owner_id TEXT, subject TEXT, body TEXT, source TEXT, rejected TEXT, sequence_id TEXT, enrollment_id TEXT,
  created_at TEXT, replied_at TEXT, assigned_at TEXT, resolved_at TEXT
);
CREATE INDEX IF NOT EXISTS tier2_status ON tier2(status);
-- Every model call the service made, with the cost the Claude CLI reports (the figure Revenium meters) and,
-- for a draft, the lead it was for (llm/usage.py).
CREATE TABLE IF NOT EXISTS llm_usage (
  id INTEGER PRIMARY KEY AUTOINCREMENT, reader TEXT, model TEXT, task TEXT, reference TEXT, input_tokens INTEGER,
  output_tokens INTEGER, cache_read_tokens INTEGER, cache_write_tokens INTEGER, cost_usd REAL, duration_ms REAL, created_at TEXT
);
CREATE INDEX IF NOT EXISTS llm_usage_task ON llm_usage(task, reference);
CREATE INDEX IF NOT EXISTS run_log_job ON run_log(job, id);
-- A phase switch thrown from the Next phases page: which, to what, by whom (feedback/loop and routing/tier2 hold the switches).
CREATE TABLE IF NOT EXISTS phase_decisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, phase TEXT, switch TEXT, value TEXT, forced INTEGER DEFAULT 0, who TEXT, note TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
-- A second look at scored leads for marketing (flags.py, llm/flags.py): every read, so a record is read once an input, and the flags.
CREATE TABLE IF NOT EXISTS flag_reads (
  contact_id TEXT, input_hash TEXT, llm TEXT, reading TEXT, created_at TEXT, PRIMARY KEY (contact_id, input_hash, llm)
);
CREATE TABLE IF NOT EXISTS flags (
  id INTEGER PRIMARY KEY AUTOINCREMENT, contact_id TEXT, kind TEXT, quote TEXT, note TEXT, probability REAL, model_version TEXT,
  channel TEXT, llm TEXT, created_at TEXT, resolved_at TEXT, resolution TEXT, resolved_by TEXT, resolution_note TEXT
);
CREATE INDEX IF NOT EXISTS flags_contact ON flags(contact_id);
-- The verdict under a candidate's sign-off (verdicts.py): one a candidate against a live model and its metrics.
CREATE TABLE IF NOT EXISTS verdicts (
  version TEXT, live_version TEXT, input_hash TEXT, verdict TEXT, sentence TEXT, source TEXT, llm TEXT, created_at TEXT,
  PRIMARY KEY (version, input_hash)
);
-- Who may open the pages (auth.py): accounts with a role, and the sessions their logins opened (a hash of each token).
CREATE TABLE IF NOT EXISTS staff (
  username TEXT PRIMARY KEY, name TEXT, role TEXT NOT NULL, owner_id TEXT, pw_hash TEXT NOT NULL, created_at REAL,
  disabled INTEGER DEFAULT 0, failed INTEGER DEFAULT 0, locked_until REAL
);
CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, username TEXT NOT NULL, created_at REAL, last_seen REAL);
CREATE INDEX IF NOT EXISTS sessions_user ON sessions(username);
"""


def connect(path: Path | str = DEFAULT_DB) -> sqlite3.Connection:
    path = Path(path)
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


# Columns added after a database may already exist; CREATE TABLE IF NOT EXISTS does not add them.
_ADDED_COLUMNS = {"hs_deals": {"closed_lost_reason": "TEXT"}, "outcomes": {"lead_created_at": "TEXT"},
                  "hs_owners": {"meetings_link": "TEXT"}, "hs_engagements": {"disposition": "TEXT", "body": "TEXT"},
                  "routing_log": {"probability": "REAL", "below_gate": "INTEGER"}, "replies": {"sent_at": "TEXT"},
                  "overrides": {"actor": "TEXT"},
                  "hs_contacts": {"auxo_email_subject": "TEXT", "auxo_email_body": "TEXT", "auxo_below_gate": "INTEGER"}}


def _migrate(conn: sqlite3.Connection) -> None:
    for table, cols in _ADDED_COLUMNS.items():
        have = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        for col, kind in cols.items():
            if col not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {kind}")


@contextmanager
def tx(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """A serialised write transaction (BEGIN IMMEDIATE): routing runs one at a time."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat()


def clock(conn: sqlite3.Connection) -> datetime:
    """The service's 'now' for maturity windows and SLAs: the simulated clock when the mock's generator or
    simulator set one (`clock`, else `generated_as_of`) and `clock_live` is not set, wall time otherwise (a real
    portal, or the mock after `run.py rebase`). Log timestamps stay wall time."""
    if get_setting(conn, "clock_live") == "1":
        return datetime.utcnow()
    v = get_setting(conn, "clock") or get_setting(conn, "generated_as_of")
    return datetime.fromisoformat(v) if v else datetime.utcnow()


def clock_frozen(conn: sqlite3.Connection) -> bool:
    """Whether `clock` is a simulated clock standing still, as against the wall clock."""
    return get_setting(conn, "clock_live") != "1" and bool(get_setting(conn, "clock") or get_setting(conn, "generated_as_of"))


def freeze_clock(conn: sqlite3.Connection, at: str) -> None:
    """The simulator's clock: `at` (ISO), and no longer live."""
    set_setting(conn, "clock", at)
    set_setting(conn, "clock_live", "0")


def dumps(obj: Any) -> str:
    return json.dumps(obj, default=str, ensure_ascii=False)


def loads(s: Optional[str]) -> Any:
    return json.loads(s) if s else None


def get_setting(conn: sqlite3.Connection, key: str, default: Optional[str] = None) -> Optional[str]:
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
