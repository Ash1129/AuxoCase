"""Rebase the mock's dates onto the present and let the clock run live.

The generator and the simulator date everything on a demo clock (Aug 2025 to Oct 2028 on the shipped data); AuxoCase's
own logs are wall time. `rebase` moves every demo-clock timestamp back by whole years so the data ends about now,
leaves the wall-time logs alone, and sets `clock_live`, after which `clock()` is the wall clock: leads age, labels
mature and SLAs run in real time. Whole years keep months aligned, so month keys ('2028-05') stay valid; a leap day
lands on the 28th. Mixed columns, which both the generator and the service write, keep any value inside the wall
window (from the first logged run to now) and shift the rest. The stored evaluation's months move with the data.

`exact=True` shifts by the exact span from the newest lead to now instead, so that lead arrived this minute and every
age on the pages is real: for a mock the simulator ran ahead of the wall clock, where whole years would leave the
newest months in the future. Months no longer align (a month key moves with its first day), and since the service
has usually been writing on the live clock by then, every column but the leads' and deals' own dates keeps values
inside the wall window and shifts the rest, the way the mixed columns always have.
"""
from __future__ import annotations

import calendar
import json
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional, Union

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
# The generator's and the simulator's alone: shifted whole even on an exact rebase, when the rest keep the wall window.
OWN_COLUMNS = {("hs_contacts", "createdate"), ("hs_deals", "createdate"), ("hs_deals", "closedate"), ("hs_deals", "lastmodifieddate")}
DATE = re.compile(r"^(\d{4})-(\d{2})(?:-(\d{2}))?")
Shift = Union[int, timedelta]       # whole years back, or an exact span back


def shift_text(v: Optional[str], by: Shift) -> Optional[str]:
    """'2028-02-29T10:00:00' back two years is '2026-02-28T10:00:00'; back an exact span, the value moves by it
    (a date or a month key by whole days: '2028-05' back 20 days is '2028-04'). A value without a leading date is returned as is."""
    if not v:
        return v
    m = DATE.match(v)
    if not m:
        return v
    if isinstance(by, timedelta):
        return _shift_exact(v, m, by)
    years = by
    y = int(m.group(1)) - years
    if m.group(3) == "29" and m.group(2) == "02" and not calendar.isleap(y):
        return f"{y:04d}-02-28" + v[10:]
    return f"{y:04d}" + v[4:]


def _shift_exact(v: str, m: "re.Match[str]", by: timedelta) -> str:
    days = timedelta(days=by.days)                                      # a date or month key moves by whole days
    if m.group(3) is None:                                              # a month key
        return f"{datetime(int(m.group(1)), int(m.group(2)), 1) - days:%Y-%m}" + v[7:]
    rest = v[10:]
    if rest[:1] in ("T", " "):
        try:
            dt = datetime.fromisoformat(v)
        except ValueError:
            dt = None
        if dt is not None:
            return (dt - by).isoformat(sep=rest[0], timespec="microseconds" if "." in rest else "seconds")
    return f"{datetime.strptime(v[:10], '%Y-%m-%d') - days:%Y-%m-%d}" + rest


def wall_window(conn: sqlite3.Connection) -> tuple[str, str]:
    """From the first logged run (or score) to now: timestamps in here were written by the service, not the generator."""
    starts = [r[0] for r in conn.execute(
        "SELECT MIN(started_at) FROM run_log UNION ALL SELECT MIN(created_at) FROM scores UNION ALL SELECT MIN(created_at) FROM enrichment_log"
    ).fetchall() if r[0]]
    return (min(starts) if starts else "9999-12-31T23:59:59"), now_iso()


def _in_window(v: Optional[str], window: tuple[str, str]) -> bool:
    return bool(v) and len(v) >= 10 and window[0] <= v <= window[1]


def _shift_json(obj: Any, by: Shift, window: tuple[str, str]) -> Any:
    if isinstance(obj, dict):
        return {k: _shift_json(v, by, window) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_shift_json(v, by, window) for v in obj]
    if isinstance(obj, str) and DATE.match(obj) and not _in_window(obj, window):
        return shift_text(obj, by)
    return obj


def _shift_column(conn: sqlite3.Connection, table: str, col: str, by: Shift, where: str = "", params: tuple = ()) -> int:
    base = f"{col} IS NOT NULL AND {col} <> ''" + (f" AND ({where})" if where else "")
    if isinstance(by, timedelta):
        rows = conn.execute(f"SELECT rowid, {col} FROM {table} WHERE {base}", params).fetchall()
        conn.executemany(f"UPDATE {table} SET {col}=? WHERE rowid=?", [(shift_text(v, by), rowid) for rowid, v in rows])
        return len(rows)
    years = by
    n = conn.execute(f"UPDATE {table} SET {col} = (CAST(substr({col},1,4) AS INTEGER) - ?) || substr({col},5) "
                     f"WHERE {base} AND substr({col},5,6) <> '-02-29'", (years, *params)).rowcount
    for (rowid, v) in conn.execute(f"SELECT rowid, {col} FROM {table} WHERE {base} AND substr({col},5,6) = '-02-29'", params).fetchall():
        conn.execute(f"UPDATE {table} SET {col}=? WHERE rowid=?", (shift_text(v, years), rowid))
        n += 1
    return n


def years_to_now(conn: sqlite3.Connection, now: Optional[datetime] = None) -> int:
    """Whole years between the data clock and now: 2 on the shipped data in 2026."""
    return clock(conn).year - (now or datetime.utcnow()).year


def span_to_now(conn: sqlite3.Connection, now: Optional[datetime] = None) -> timedelta:
    """The exact span from the newest lead to now, to the second; zero when there is no lead or it is already past."""
    newest = conn.execute("SELECT MAX(createdate) FROM hs_contacts").fetchone()[0]
    if not newest:
        return timedelta(0)
    seconds = (datetime.fromisoformat(newest) - (now or datetime.utcnow())).total_seconds()
    return timedelta(seconds=int(seconds)) if seconds > 0 else timedelta(0)


def rebase(conn: sqlite3.Connection, years: Optional[int] = None, evaluation: Optional[Path] = None, live: bool = True,
           exact: bool = False, now: Optional[datetime] = None) -> dict:
    """Shift the demo-clock dates back `years` (default: onto this year), or with `exact` by the span from the
    newest lead to now (see the module note), move the stored evaluation's months with them, and set the clock live.
    Returns {years, span (the exact shift as text, or None), rows: {table.column: n}, evaluation, clock_live}."""
    by: Shift = span_to_now(conn, now) if exact else (years_to_now(conn, now) if years is None else years)
    window = wall_window(conn)
    rows: dict[str, int] = {}
    if by:
        for table, cols in DEMO_COLUMNS.items():
            for col in cols:
                if exact and (table, col) not in OWN_COLUMNS:
                    rows[f"{table}.{col}"] = _shift_column(conn, table, col, by, f"NOT ({col} >= ? AND {col} <= ?)", window)
                else:
                    rows[f"{table}.{col}"] = _shift_column(conn, table, col, by)
        for table, cols in MIXED_COLUMNS.items():
            for col in cols:
                rows[f"{table}.{col}"] = _shift_column(conn, table, col, by, f"NOT ({col} >= ? AND {col} <= ?)", window)
        for table, (col, who, value) in SOURCE_COLUMNS.items():
            rows[f"{table}.{col}"] = _shift_column(conn, table, col, by, f"{who} = ?", (value,))
        for table, (col, key) in JSON_COLUMNS.items():
            for k, raw in conn.execute(f"SELECT {key}, {col} FROM {table}").fetchall():
                if raw:
                    conn.execute(f"UPDATE {table} SET {col}=? WHERE {key}=?",
                                 (json.dumps(_shift_json(json.loads(raw), by, window)), k))
                    rows[f"{table}.{col}"] = rows.get(f"{table}.{col}", 0) + 1
        for key in ("clock", "generated_as_of"):
            v = get_setting(conn, key)
            if v:
                set_setting(conn, key, shift_text(v, by))
        if evaluation and Path(evaluation).exists():
            p = Path(evaluation)
            p.write_text(json.dumps(_shift_json(json.loads(p.read_text()), by, window), indent=1))
    if live:
        set_setting(conn, "clock_live", "1")
    conn.commit()
    return {"years": None if exact else by, "span": str(by) if exact else None, "rows": rows,
            "evaluation": str(evaluation) if evaluation else None, "clock_live": live}
