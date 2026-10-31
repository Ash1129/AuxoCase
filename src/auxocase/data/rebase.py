"""Rebase the mock's dates onto the present and let the clock run live.

The generator and the simulator date everything on a demo clock (Aug 2025 to Oct 2028 on the shipped data); AuxoCase's
own logs are wall time. `rebase` moves every demo-clock timestamp back by whole years so the data ends about now,
leaves the wall-time logs alone, and sets `clock_live`, after which `clock()` is the wall clock: leads age, labels
mature and SLAs run in real time. Whole years keep months aligned, so month keys ('2028-05') stay valid; a leap day
lands on the 28th. Mixed columns, which both the generator and the service write, keep any value inside the wall
window (from the first logged run to now) and shift the rest. The stored evaluation's months move with the data.
"""
from __future__ import annotations

import calendar
import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from ..storage.db import clock, get_setting, now_iso, set_setting

# Columns the generator, the simulator or routing write on the demo clock: shifted whole.
DEMO_COLUMNS = {
    "hs_contacts": ("createdate",),
    "hs_deals": ("createdate", "closedate", "lastmodifieddate"),
    "hs_meetings": ("start_time", "createdate"),
    "hs_engagements": ("timestamp",),
    "hs_tasks": ("due_at",),
    "held_queue": ("held_at", "released_at"),
    "routing_log": ("created_at",),
    "outcomes": ("first_touch_at", "demo_booked_at", "closed_won_at", "closed_lost_at", "lead_created_at"),
    "touches": ("touched_at",),
    "channel_monitor": ("period",),
    "model_registry": ("trained_at",),
}
# Columns both clocks write: a value inside the wall window stays, the rest shift.
MIXED_COLUMNS = {"hs_contacts": ("lastmodifieddate",), "hs_events": ("occurred_at",)}
# Property history carries its writer: the generator's rows ('sdr') shift, the service's stay.
SOURCE_COLUMNS = {"hs_property_history": ("changed_at", "source", "sdr")}
JSON_COLUMNS = {"model_registry": ("metrics", "version")}       # (column, key column) with date strings inside
DATE = re.compile(r"^(\d{4})-(\d{2})(?:-(\d{2}))?")


def shift_text(v: Optional[str], years: int) -> Optional[str]:
    """'2028-02-29T10:00:00' back two years is '2026-02-28T10:00:00'; a value without a leading date is returned as is."""
    if not v:
        return v
    m = DATE.match(v)
    if not m:
        return v
    y = int(m.group(1)) - years
    if m.group(3) == "29" and m.group(2) == "02" and not calendar.isleap(y):
        return f"{y:04d}-02-28" + v[10:]
    return f"{y:04d}" + v[4:]


def wall_window(conn: sqlite3.Connection) -> tuple[str, str]:
    """From the first logged run (or score) to now: timestamps in here were written by the service, not the generator."""
    starts = [r[0] for r in conn.execute(
        "SELECT MIN(started_at) FROM run_log UNION ALL SELECT MIN(created_at) FROM scores UNION ALL SELECT MIN(created_at) FROM enrichment_log"
    ).fetchall() if r[0]]
    return (min(starts) if starts else "9999-12-31T23:59:59"), now_iso()


def _in_window(v: Optional[str], window: tuple[str, str]) -> bool:
    return bool(v) and len(v) >= 10 and window[0] <= v <= window[1]


def _shift_json(obj: Any, years: int, window: tuple[str, str]) -> Any:
    if isinstance(obj, dict):
        return {k: _shift_json(v, years, window) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_shift_json(v, years, window) for v in obj]
    if isinstance(obj, str) and DATE.match(obj) and not _in_window(obj, window):
        return shift_text(obj, years)
    return obj


def _shift_column(conn: sqlite3.Connection, table: str, col: str, years: int, where: str = "", params: tuple = ()) -> int:
    base = f"{col} IS NOT NULL AND {col} <> ''" + (f" AND ({where})" if where else "")
    n = conn.execute(f"UPDATE {table} SET {col} = (CAST(substr({col},1,4) AS INTEGER) - ?) || substr({col},5) "
                     f"WHERE {base} AND substr({col},5,6) <> '-02-29'", (years, *params)).rowcount
    for (rowid, v) in conn.execute(f"SELECT rowid, {col} FROM {table} WHERE {base} AND substr({col},5,6) = '-02-29'", params).fetchall():
        conn.execute(f"UPDATE {table} SET {col}=? WHERE rowid=?", (shift_text(v, years), rowid))
        n += 1
    return n


def years_to_now(conn: sqlite3.Connection, now: Optional[datetime] = None) -> int:
    """Whole years between the data clock and now: 2 on the shipped data in 2026."""
    return clock(conn).year - (now or datetime.utcnow()).year


def rebase(conn: sqlite3.Connection, years: Optional[int] = None, evaluation: Optional[Path] = None, live: bool = True) -> dict:
    """Shift the demo-clock dates back `years` (default: onto this year), move the stored evaluation's months with
    them, and set the clock live. Returns {years, rows: {table.column: n}, evaluation, clock_live}."""
    years = years_to_now(conn) if years is None else years
    window = wall_window(conn)
    rows: dict[str, int] = {}
    if years:
        for table, cols in DEMO_COLUMNS.items():
            for col in cols:
                rows[f"{table}.{col}"] = _shift_column(conn, table, col, years)
        for table, cols in MIXED_COLUMNS.items():
            for col in cols:
                rows[f"{table}.{col}"] = _shift_column(conn, table, col, years, f"NOT ({col} >= ? AND {col} <= ?)", window)
        for table, (col, by, value) in SOURCE_COLUMNS.items():
            rows[f"{table}.{col}"] = _shift_column(conn, table, col, years, f"{by} = ?", (value,))
        for table, (col, key) in JSON_COLUMNS.items():
            for k, raw in conn.execute(f"SELECT {key}, {col} FROM {table}").fetchall():
                if raw:
                    conn.execute(f"UPDATE {table} SET {col}=? WHERE {key}=?",
                                 (json.dumps(_shift_json(json.loads(raw), years, window)), k))
                    rows[f"{table}.{col}"] = rows.get(f"{table}.{col}", 0) + 1
        for key in ("clock", "generated_as_of"):
            v = get_setting(conn, key)
            if v:
                set_setting(conn, key, shift_text(v, years))
        if evaluation and Path(evaluation).exists():
            p = Path(evaluation)
            p.write_text(json.dumps(_shift_json(json.loads(p.read_text()), years, window), indent=1))
    if live:
        set_setting(conn, "clock_live", "1")
    conn.commit()
    return {"years": years, "rows": rows, "evaluation": str(evaluation) if evaluation else None, "clock_live": live}
