"""A HubSpot stand-in over the `hs_*` SQLite tables.

Why a mock this detailed: the service's hard parts are the CRM's *semantics*
(multi-status batch writes, at-least-once unordered webhooks, compare-and-set
owner assignment, properties that must exist before they can be written), not
the HTTP. Reproducing those semantics locally means every test and the demo
exercise the same failure modes a live portal would, without a test account
(H11). Everything here follows H04-H07; where HubSpot is permissive, so is
this, and where HubSpot refuses, this refuses the same way.
"""
from __future__ import annotations

import random
import sqlite3
from contextlib import contextmanager
from dataclasses import fields, replace
from datetime import datetime, timedelta
from typing import Any, Iterable, Iterator, Optional, Type, TypeVar

from ..storage.db import get_setting, now_iso, set_setting, tx
from .models import (
    AUXO_PROPERTIES,
    OPEN_LEAD_STATUSES,
    BatchResult,
    ChangeEvent,
    Company,
    Contact,
    Deal,
    Meeting,
    Owner,
    Task,
)

T = TypeVar("T")

BATCH_LIMIT = 100                 # HubSpot batch endpoints take at most 100 inputs (H05)
DUPLICATE_RATE = 0.03             # share of webhook events delivered twice (H04)
SWAP_RATE = 0.05                  # share of adjacent pairs delivered out of order (H04)
SUPPRESS_SETTING = "suppress_events_for_auxo"

# Columns the hs_* tables keep that the dataclasses do not expose.
_EXTRA_COLUMNS = {Deal: ("lastmodifieddate",), Meeting: ("createdate",)}

# Field names whose value is written back by this service; writes to them
# must not re-trigger the scorer (Blueprint pitfall: "saving the score can
# restart the workflow").
_AUXO_FIELDS = set(AUXO_PROPERTIES)

# Contact properties the batch endpoint accepts: every column except the id.
_CONTACT_WRITABLE = {f.name for f in fields(Contact)} - {"id"}


# --------------------------------------------------------------------------
# row <-> dataclass
# --------------------------------------------------------------------------
def _encode(value: Any) -> Any:
    """SQLite has no datetime or bool; store ISO text and 0/1."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, bool):
        return int(value)
    return value


def _decode(type_name: str, value: Any) -> Any:
    """Inverse of `_encode`, driven by the dataclass field's annotation text."""
    if value is None:
        return "" if type_name == "str" else None
    if "datetime" in type_name:
        return datetime.fromisoformat(value) if isinstance(value, str) else value
    if type_name == "bool":
        return bool(value)
    return value


def _from_row(cls: Type[T], row: sqlite3.Row) -> T:
    keys = row.keys()
    return cls(**{f.name: _decode(str(f.type), row[f.name]) for f in fields(cls) if f.name in keys})


def _to_row(obj: Any) -> dict[str, Any]:
    return {f.name: _encode(getattr(obj, f.name)) for f in fields(obj)}


def _insert(conn: sqlite3.Connection, table: str, values: dict[str, Any]) -> None:
    cols = ", ".join(values)
    marks = ", ".join("?" for _ in values)
    conn.execute(f"INSERT OR REPLACE INTO {table}({cols}) VALUES({marks})", tuple(values.values()))


class MockHubSpot:
    """`HubSpotClient` over SQLite, plus helpers to seed and to play the SDRs.

    `faithful_delivery=True` turns off the deliberate webhook chaos
    (duplicates, reordering) for tests that need to reason about order.
    """

    def __init__(self, conn: sqlite3.Connection, faithful_delivery: bool = False, chaos_seed: int = 42):
        self.conn = conn
        self.faithful_delivery = faithful_delivery
        self._rng = random.Random(chaos_seed)
        self._deferred: list[ChangeEvent] = []   # duplicates held back to the next poll
        self._requests = 0

    # ---------------------------------------------------------------- infra
    def _count(self, n: int = 1) -> None:
        self._requests += n

    def request_count(self) -> int:
        """Public calls so far; one per batch chunk, as HubSpot would bill them."""
        return self._requests

    def reset_request_count(self) -> None:
        self._requests = 0

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        """Serialised write unless the caller already opened a transaction (the generator does)."""
        if self.conn.in_transaction:
            yield self.conn
        else:
            with tx(self.conn) as c:
                yield c

    def _next_id(self, table: str, prefix: str) -> str:
        n = self.conn.execute(f"SELECT COALESCE(MAX(rowid), 0) + 1 FROM {table}").fetchone()[0]
        return f"{prefix}{n}"

    def _history(self, object_type: str, object_id: str, prop: str, value: Any, at: str, source: str) -> None:
        self.conn.execute(
            "INSERT INTO hs_property_history(object_type, object_id, property, value, changed_at, source) VALUES(?,?,?,?,?,?)",
            (object_type, object_id, prop, None if value is None else str(_encode(value)), at, source),
        )

    def _enqueue(self, object_id: str, subscription_type: str, property_name: str, at: str, delivered: bool = False) -> None:
        event_id = self._next_id("hs_events", "evt-")
        self.conn.execute(
            "INSERT INTO hs_events(event_id, object_id, subscription_type, property_name, occurred_at, delivered, attempt) VALUES(?,?,?,?,?,?,0)",
            (event_id, object_id, subscription_type, property_name, at, int(delivered)),
        )

    def _events_suppressed(self) -> bool:
        return get_setting(self.conn, SUPPRESS_SETTING, "1") == "1"

    def set_event_suppression(self, enabled: bool) -> None:
        """Whether writes to auxo_* properties fire events. Off only to demonstrate the feedback loop."""
        set_setting(self.conn, SUPPRESS_SETTING, "1" if enabled else "0")

    def _should_emit(self, prop: str) -> bool:
        return not (prop in _AUXO_FIELDS and self._events_suppressed())

    # --------------------------------------------------------------- schema
    def ensure_properties(self) -> list[str]:
        self._count()
        existing = {r["name"] for r in self.conn.execute("SELECT name FROM hs_properties")}
        created = [name for name in AUXO_PROPERTIES if name not in existing]
        with self._write():
            for name in created:
                self.conn.execute(
                    "INSERT INTO hs_properties(name, type, created_at) VALUES(?,?,?)",
                    (name, AUXO_PROPERTIES[name], now_iso()),
                )
        return created

    def _known_properties(self) -> set[str]:
        """auxo_* names are writable only once created, as on the real portal."""
        defined = {r["name"] for r in self.conn.execute("SELECT name FROM hs_properties")}
        return (_CONTACT_WRITABLE - _AUXO_FIELDS) | (defined & _AUXO_FIELDS)

    # ------------------------------------------------- contacts / companies
    def get_contact(self, contact_id: str) -> Optional[Contact]:
        self._count()
        row = self.conn.execute("SELECT * FROM hs_contacts WHERE id=?", (contact_id,)).fetchone()
        return _from_row(Contact, row) if row else None

    def get_company(self, company_id: str) -> Optional[Company]:
        self._count()
        row = self.conn.execute("SELECT * FROM hs_companies WHERE id=?", (company_id,)).fetchone()
        return _from_row(Company, row) if row else None

    def search_contacts_modified_since(self, since: datetime, limit: int = 200) -> list[Contact]:
        self._count()
        rows = self.conn.execute(
            "SELECT * FROM hs_contacts WHERE lastmodifieddate >= ? ORDER BY lastmodifieddate, id LIMIT ?",
            (since.isoformat(), min(limit, 200)),
        ).fetchall()
        return [_from_row(Contact, r) for r in rows]

    def list_contacts(self, lifecycle_stage: Optional[str] = None) -> list[Contact]:
        self._count()
        if lifecycle_stage is None:
            rows = self.conn.execute("SELECT * FROM hs_contacts ORDER BY createdate, id").fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM hs_contacts WHERE lifecycle_stage=? ORDER BY createdate, id", (lifecycle_stage,)
            ).fetchall()
        return [_from_row(Contact, r) for r in rows]

    def batch_update_contacts(self, updates: Iterable[tuple[str, dict]]) -> BatchResult:
        """207-style: each chunk of 100 is one request; bad inputs fail alone, the rest land."""
        items = list(updates)
        result = BatchResult(ok=[], failed={})
        known = self._known_properties()
        for start in range(0, len(items), BATCH_LIMIT):
            self._count()
            chunk = items[start:start + BATCH_LIMIT]
            with self._write():
                for contact_id, props in chunk:
                    error = self._apply_update(contact_id, props, known)
                    if error:
                        result.failed[contact_id] = error
                    else:
                        result.ok.append(contact_id)
        return result

    def _apply_update(self, contact_id: str, props: dict, known: set[str]) -> Optional[str]:
        unknown = [p for p in props if p not in known]
        if unknown:
            return f"PROPERTY_DOESNT_EXIST: {', '.join(sorted(unknown))}"
        row = self.conn.execute("SELECT * FROM hs_contacts WHERE id=?", (contact_id,)).fetchone()
        if row is None:
            return "OBJECT_NOT_FOUND"
        at = now_iso()
        changed = {k: _encode(v) for k, v in props.items() if _encode(v) != row[k]}
        if not changed:
            return None
        sets = ", ".join(f"{k}=?" for k in changed)
        self.conn.execute(
            f"UPDATE hs_contacts SET {sets}, lastmodifieddate=? WHERE id=?",
            (*changed.values(), at, contact_id),
        )
        for prop, value in changed.items():
            self._history("contact", contact_id, prop, value, at, "auxocase")
            if self._should_emit(prop):
                self._enqueue(contact_id, "contact.propertyChange", prop, at)
        return None

    # ---------------------------------------------------------- change feed
    def poll_events(self, max_events: int = 100) -> list[ChangeEvent]:
        """Undelivered events in seq order, then roughed up the way webhooks arrive (H04)."""
        self._count()
        rows = self.conn.execute(
            "SELECT * FROM hs_events WHERE delivered=0 ORDER BY seq LIMIT ?", (max_events,)
        ).fetchall()
        events = [
            ChangeEvent(
                event_id=r["event_id"], object_id=r["object_id"], subscription_type=r["subscription_type"],
                property_name=r["property_name"] or "", occurred_at=datetime.fromisoformat(r["occurred_at"]),
                attempt=r["attempt"],
            )
            for r in rows
        ]
        if rows:
            with self._write():
                self.conn.executemany("UPDATE hs_events SET delivered=1 WHERE seq=?", [(r["seq"],) for r in rows])
        if self.faithful_delivery:
            return events
        return self._roughen(events)

    def _roughen(self, events: list[ChangeEvent]) -> list[ChangeEvent]:
        out = self._deferred + list(events)
        self._deferred = []
        dupes = [replace(e, attempt=e.attempt + 1) for e in events if self._rng.random() < DUPLICATE_RATE]
        i = 0
        while i < len(out) - 1:
            if self._rng.random() < SWAP_RATE:
                out[i], out[i + 1] = out[i + 1], out[i]
                i += 2
            else:
                i += 1
        for d in dupes:
            (out if self._rng.random() < 0.5 else self._deferred).append(d)
        return out

    # -------------------------------------------------------------- routing
    def list_owners(self, active_only: bool = True) -> list[Owner]:
        self._count()
        sql = "SELECT * FROM hs_owners" + (" WHERE active=1" if active_only else "") + " ORDER BY id"
        return [_from_row(Owner, r) for r in self.conn.execute(sql).fetchall()]

    def open_lead_counts(self) -> dict[str, int]:
        self._count()
        counts = {r["id"]: 0 for r in self.conn.execute("SELECT id FROM hs_owners WHERE active=1")}
        marks = ", ".join("?" for _ in OPEN_LEAD_STATUSES)
        rows = self.conn.execute(
            f"SELECT hubspot_owner_id AS o, COUNT(*) AS n FROM hs_contacts "
            f"WHERE hubspot_owner_id IS NOT NULL AND hubspot_owner_id != '' AND hs_lead_status IN ({marks}) "
            f"AND lifecycle_stage IN ('marketingqualifiedlead', 'salesqualifiedlead') GROUP BY o",
            OPEN_LEAD_STATUSES,
        ).fetchall()
        for r in rows:
            counts[r["o"]] = r["n"]
        return counts

    def set_owner(self, contact_id: str, owner_id: str, expected_current: Optional[str] = None) -> bool:
        """Compare-and-set so a retried routing run cannot steal a lead an SDR already has (H06)."""
        self._count()
        with self._write():
            row = self.conn.execute(
                "SELECT hubspot_owner_id, hs_lead_status, lifecycle_stage FROM hs_contacts WHERE id=?", (contact_id,)
            ).fetchone()
            if row is None:
                return False
            current = row["hubspot_owner_id"] or None
            if current != (expected_current or None):
                return False
            at = now_iso()
            changes = {"hubspot_owner_id": owner_id}
            if not row["hs_lead_status"]:
                changes["hs_lead_status"] = "NEW"
            if row["lifecycle_stage"] == "marketingqualifiedlead":
                changes["lifecycle_stage"] = "salesqualifiedlead"
            self._write_contact_changes(contact_id, changes, at, source="auxocase", emit=True)
        return True

    def _write_contact_changes(self, contact_id: str, changes: dict[str, Any], at: str, source: str, emit: bool) -> None:
        sets = ", ".join(f"{k}=?" for k in changes)
        self.conn.execute(
            f"UPDATE hs_contacts SET {sets}, lastmodifieddate=? WHERE id=?",
            (*[_encode(v) for v in changes.values()], at, contact_id),
        )
        for prop, value in changes.items():
            self._history("contact", contact_id, prop, value, at, source)
            if emit and self._should_emit(prop):
                self._enqueue(contact_id, "contact.propertyChange", prop, at)

    def create_task(self, owner_id: str, contact_id: str, subject: str, body: str, due_at: Optional[datetime] = None) -> Task:
        self._count()
        task = Task(
            id=self._next_id("hs_tasks", "task-"), contact_id=contact_id, owner_id=owner_id,
            subject=subject, body=body, created_at=datetime.fromisoformat(now_iso()), due_at=due_at,
        )
        with self._write():
            _insert(self.conn, "hs_tasks", _to_row(task))
        return task

    def list_tasks(self, owner_id: Optional[str] = None) -> list[Task]:
        self._count()
        if owner_id is None:
            rows = self.conn.execute("SELECT * FROM hs_tasks ORDER BY created_at, id").fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM hs_tasks WHERE owner_id=? ORDER BY created_at, id", (owner_id,)).fetchall()
        return [_from_row(Task, r) for r in rows]

    # ------------------------------------------------------------- outcomes
    def deals_modified_since(self, since: datetime) -> list[Deal]:
        self._count()
        rows = self.conn.execute(
            "SELECT * FROM hs_deals WHERE lastmodifieddate >= ? ORDER BY lastmodifieddate, id", (since.isoformat(),)
        ).fetchall()
        return [_from_row(Deal, r) for r in rows]

    def meetings_since(self, since: datetime) -> list[Meeting]:
        self._count()
        rows = self.conn.execute(
            "SELECT * FROM hs_meetings WHERE createdate >= ? ORDER BY createdate, id", (since.isoformat(),)
        ).fetchall()
        return [_from_row(Meeting, r) for r in rows]

    def deals_for_contact(self, contact_id: str) -> list[Deal]:
        self._count()
        rows = self.conn.execute("SELECT * FROM hs_deals WHERE contact_id=? ORDER BY createdate, id", (contact_id,)).fetchall()
        return [_from_row(Deal, r) for r in rows]

    def meetings_for_contact(self, contact_id: str) -> list[Meeting]:
        self._count()
        rows = self.conn.execute("SELECT * FROM hs_meetings WHERE contact_id=? ORDER BY start_time, id", (contact_id,)).fetchall()
        return [_from_row(Meeting, r) for r in rows]

    # ------------------------------------------------- mock-only: seeding
    def add_contact(self, contact: Contact, emit_event: bool = True) -> None:
        """Insert as HubSpot would on form submit; `emit_event=False` for history that was 'delivered' long ago."""
        with self._write():
            _insert(self.conn, "hs_contacts", _to_row(contact))
            if emit_event:
                self._enqueue(contact.id, "contact.creation", "", contact.createdate.isoformat())

    def add_company(self, company: Company) -> None:
        with self._write():
            _insert(self.conn, "hs_companies", _to_row(company))

    def add_owner(self, owner: Owner) -> None:
        with self._write():
            _insert(self.conn, "hs_owners", _to_row(owner))

    def add_deal(self, deal: Deal, emit_event: bool = True) -> None:
        with self._write():
            row = _to_row(deal)
            row["lastmodifieddate"] = (deal.closedate or deal.createdate).isoformat()
            _insert(self.conn, "hs_deals", row)
            if emit_event:
                self._enqueue(deal.id, "deal.creation", "", deal.createdate.isoformat())

    def add_meeting(self, meeting: Meeting, created_at: Optional[datetime] = None, emit_event: bool = True) -> None:
        with self._write():
            row = _to_row(meeting)
            row["createdate"] = (created_at or meeting.start_time).isoformat()
            _insert(self.conn, "hs_meetings", row)
            if emit_event:
                self._enqueue(meeting.id, "meeting.creation", "", row["createdate"])

    def simulate_sdr_activity(
        self, contact_id: str, owner_id: str, outcome: str, at: datetime,
        amount: float = 0.0, emit_events: bool = True,
    ) -> None:
        """Write what an SDR's actions leave behind in HubSpot (H07).

        'worked'       owner + open lead status (MQL -> SQL)
        'demo_booked'  CONNECTED + a SCHEDULED meeting + a deal at appointmentscheduled (-> opportunity)
        'closed_won'   deal -> closedwon, hs_is_closed_won, closedate (-> customer)
        'closed_lost'  deal -> closedlost, closedate
        'junk'         UNQUALIFIED
        Each call is one step so callers can space them in time like real SDRs do.
        """
        at_s = at.isoformat()
        with self._write():
            row = self.conn.execute(
                "SELECT hubspot_owner_id, hs_lead_status, lifecycle_stage FROM hs_contacts WHERE id=?", (contact_id,)
            ).fetchone()
            if row is None:
                raise KeyError(contact_id)
            changes: dict[str, Any] = {}
            if (row["hubspot_owner_id"] or None) != owner_id:
                changes["hubspot_owner_id"] = owner_id
            if row["lifecycle_stage"] in ("lead", "marketingqualifiedlead") and outcome != "junk":
                changes["lifecycle_stage"] = "salesqualifiedlead"

            if outcome == "worked":
                changes["hs_lead_status"] = "IN_PROGRESS"
            elif outcome == "junk":
                changes["hs_lead_status"] = "UNQUALIFIED"
            elif outcome == "demo_booked":
                changes["hs_lead_status"] = "CONNECTED"
                changes["lifecycle_stage"] = "opportunity"
                meeting = Meeting(
                    id=self._next_id("hs_meetings", "mtg-"), contact_id=contact_id, owner_id=owner_id,
                    start_time=at + timedelta(days=4), outcome="SCHEDULED",
                )
                self.add_meeting(meeting, created_at=at, emit_event=emit_events)
                deal = Deal(
                    id=self._next_id("hs_deals", "deal-"), contact_id=contact_id, dealstage="appointmentscheduled",
                    amount=amount, createdate=at, owner_id=owner_id,
                )
                self.add_deal(deal, emit_event=emit_events)
            elif outcome in ("closed_won", "closed_lost"):
                won = outcome == "closed_won"
                stage = "closedwon" if won else "closedlost"
                deal_row = self.conn.execute(
                    "SELECT id FROM hs_deals WHERE contact_id=? ORDER BY createdate DESC LIMIT 1", (contact_id,)
                ).fetchone()
                if deal_row is None:
                    # Closing without a booked demo happens (inbound buyers); keep the trail consistent.
                    self.simulate_sdr_activity(contact_id, owner_id, "demo_booked", at - timedelta(days=30), amount, emit_events)
                    deal_row = self.conn.execute(
                        "SELECT id FROM hs_deals WHERE contact_id=? ORDER BY createdate DESC LIMIT 1", (contact_id,)
                    ).fetchone()
                self.conn.execute(
                    "UPDATE hs_deals SET dealstage=?, hs_is_closed_won=?, closedate=?, lastmodifieddate=?, "
                    "amount=CASE WHEN ? > 0 THEN ? ELSE amount END WHERE id=?",
                    (stage, int(won), at_s, at_s, amount, amount, deal_row["id"]),
                )
                self._history("deal", deal_row["id"], "dealstage", stage, at_s, "sdr")
                if emit_events:
                    self._enqueue(deal_row["id"], "deal.propertyChange", "dealstage", at_s)
                self.conn.execute(
                    "UPDATE hs_meetings SET outcome='COMPLETED' WHERE contact_id=? AND outcome='SCHEDULED'", (contact_id,)
                )
                if won:
                    changes["lifecycle_stage"] = "customer"
            else:
                raise ValueError(f"unknown outcome {outcome!r}")

            # HubSpot records history only for real changes; the first hs_lead_status row is the first touch.
            if changes.get("hs_lead_status") == row["hs_lead_status"]:
                del changes["hs_lead_status"]
            if changes:
                self._write_contact_changes(contact_id, changes, at_s, source="sdr", emit=emit_events)
