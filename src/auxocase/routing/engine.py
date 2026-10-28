"""Give each scored lead to an SDR, within capacity, and alert them.

Blueprint job 2 as designed (fixed rules, no AI), with three additions the
knowledge base asked for:

- The capacity line. The brief's team works about 500 of 1,500 MQLs a month.
  Routing is by rank in the current pool: leads above the line are assigned,
  the rest are left in the pool and re-ranked as new leads arrive, so the
  line itself is what marketing and sales negotiate (L03, L10).
- An exploration slice (L05). A fixed share of the assigned slots (default
  8%) goes to leads drawn at random from below the line, marked
  `auxo_holdout`, so next month's model has labels the SDRs' taste didn't
  choose. This is the only way the reported lift can be trusted.
- Speed to lead (L07). Every assignment creates a task due within hours, and
  assigned leads with no first touch by the SLA are re-alerted, not left.

Mechanics from the kit: the routing step runs one lead at a time under a
write lock; the owner is set with compare-and-set so a retry can't assign
twice; an assignment counts as done only when both owner and task exist;
when everyone is at capacity the lead is held with a timestamp for the
manager, whose queue is a named job.
"""
from __future__ import annotations

import random
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from ..hubspot.client import HubSpotClient
from ..hubspot.models import Contact, Owner
from ..storage.db import clock, dumps, get_setting, now_iso, set_setting, tx

DEFAULT_EXPLORATION_SHARE = 0.08
DEFAULT_TASK_DUE_HOURS = 4
DEFAULT_FIRST_TOUCH_SLA_HOURS = 24


@dataclass
class RoutingDecision:
    contact_id: str
    decision: str                 # assigned | held | below_line | incomplete | already_owned | holdout_assigned
    owner_id: Optional[str] = None
    task_id: Optional[str] = None
    reason: str = ""


def _missing_fields(c: Contact) -> list[str]:
    missing = []
    if not c.email:
        missing.append("email")
    if c.auxo_probability is None and c.auxo_score is None:
        missing.append("auxo_score")
    if not c.channel:
        missing.append("channel")
    return missing


class Router:
    def __init__(self, hs: HubSpotClient, conn: sqlite3.Connection, seed: int = 11,
                 exploration_share: float = DEFAULT_EXPLORATION_SHARE,
                 task_due_hours: int = DEFAULT_TASK_DUE_HOURS, team: str = "sdr", hold_limit: int = 50):
        self.hs, self.conn, self.team = hs, conn, team
        self.rng = random.Random(seed)
        self.exploration_share = exploration_share
        self.task_due_hours = task_due_hours
        self.hold_limit = hold_limit

    # ---- the pool and the line ------------------------------------------------
    def pool(self) -> list[Contact]:
        """Scored, unowned MQLs, best first."""
        rows = [c for c in self.hs.list_contacts(lifecycle_stage="marketingqualifiedlead")
                if c.hubspot_owner_id is None and c.auxo_probability is not None and c.auxo_route != "incomplete"]
        rows.sort(key=lambda c: (-(c.auxo_probability or 0), c.createdate))
        return rows

    def free_slots(self) -> tuple[dict[str, int], list[Owner], int]:
        owners = [o for o in self.hs.list_owners(active_only=True) if o.team == self.team]
        counts = self.hs.open_lead_counts()
        free = sum(max(0, o.capacity_limit - counts.get(o.id, 0)) for o in owners)
        return counts, owners, free

    # ---- one routing pass -----------------------------------------------------
    def run(self, max_assignments: Optional[int] = None, now: Optional[datetime] = None) -> list[RoutingDecision]:
        now = now or clock(self.conn)
        decisions: list[RoutingDecision] = []
        counts, owners, free = self.free_slots()
        if not owners:
            return decisions
        pool = self.pool()
        budget = free if max_assignments is None else min(free, max_assignments)
        if not pool:
            return decisions
        if budget <= 0:
            # Nobody is free. The top of the pool goes to the held queue, bounded so the manager's page stays
            # readable; everything below stays in the pool for the next run (held_queue ignores repeats).
            already = {r["contact_id"] for r in self.conn.execute("SELECT contact_id FROM held_queue WHERE released_at IS NULL")}
            for c in [c for c in pool if c.id not in already][: self.hold_limit]:
                decisions.append(self._assign_one(c, owners, counts, False, now))
            set_setting(self.conn, "last_routing_run", now_iso())
            return decisions

        n_explore = int(round(budget * self.exploration_share))
        n_top = budget - n_explore
        top = pool[:n_top]
        below = pool[n_top:]
        explore = self.rng.sample(below, min(n_explore, len(below))) if n_explore and below else []
        chosen = [(c, False) for c in top] + [(c, True) for c in explore]

        for contact, is_holdout in chosen:
            decisions.append(self._assign_one(contact, owners, counts, is_holdout, now))
        # Everything else stays in the pool, below the line; record the line so the UI can show it.
        line_p = top[-1].auxo_probability if top else None
        set_setting(self.conn, "capacity_line_probability", dumps(line_p))
        set_setting(self.conn, "last_routing_run", now_iso())
        return decisions

    def _next_owner(self, owners: list[Owner], counts: dict[str, int]) -> Optional[Owner]:
        """Round robin starting after the last assigned owner, skipping anyone at their limit."""
        last = get_setting(self.conn, f"rotation:{self.team}")
        order = sorted(owners, key=lambda o: o.id)
        start = 0
        if last:
            ids = [o.id for o in order]
            if last in ids:
                start = (ids.index(last) + 1) % len(order)
        for i in range(len(order)):
            o = order[(start + i) % len(order)]
            if counts.get(o.id, 0) < o.capacity_limit:
                return o
        return None

    def _assign_one(self, contact: Contact, owners: list[Owner], counts: dict[str, int],
                    is_holdout: bool, now: datetime) -> RoutingDecision:
        missing = _missing_fields(contact)
        if missing:
            self.hs.batch_update_contacts([(contact.id, {"auxo_route": "incomplete", "auxo_review_flag": "missing: " + ", ".join(missing)})])
            return self._log(RoutingDecision(contact.id, "incomplete", reason=", ".join(missing)), counts)
        with tx(self.conn):
            fresh = self.hs.get_contact(contact.id)
            if fresh is None or fresh.hubspot_owner_id:
                return self._log(RoutingDecision(contact.id, "already_owned", owner_id=fresh.hubspot_owner_id if fresh else None), counts)
            owner = self._next_owner(owners, counts)
            if owner is None:
                self.conn.execute("INSERT OR IGNORE INTO held_queue(contact_id, held_at, reason) VALUES(?,?,?)",
                                  (contact.id, now_iso(), "all SDRs at capacity"))
                self.hs.batch_update_contacts([(contact.id, {"auxo_route": "held"})])
                return self._log(RoutingDecision(contact.id, "held", reason="all SDRs at capacity"), counts)
            if not self.hs.set_owner(contact.id, owner.id, expected_current=None):
                return self._log(RoutingDecision(contact.id, "already_owned"), counts)
            counts[owner.id] = counts.get(owner.id, 0) + 1
            set_setting(self.conn, f"rotation:{self.team}", owner.id)
            subject = f"New lead to qualify: rank {contact.auxo_rank or '?'}, {round(100 * (contact.auxo_probability or 0))}% demo likelihood"
            if is_holdout:
                subject = "Calibration lead (please work like any other): " + subject
            task = self.hs.create_task(owner.id, contact.id, subject, contact.auxo_reason or "", due_at=now + timedelta(hours=self.task_due_hours))
            self.hs.batch_update_contacts([(contact.id, {"auxo_route": "assigned", "auxo_holdout": bool(is_holdout)})])
        d = RoutingDecision(contact.id, "holdout_assigned" if is_holdout else "assigned", owner_id=owner.id, task_id=task.id,
                            reason="exploration slice" if is_holdout else "above the capacity line")
        return self._log(d, counts, owner)

    def _log(self, d: RoutingDecision, counts: dict[str, int], owner: Optional[Owner] = None) -> RoutingDecision:
        self.conn.execute(
            "INSERT INTO routing_log(contact_id, owner_id, decision, reason, open_count_at_decision, capacity_limit, task_id, created_at) VALUES(?,?,?,?,?,?,?,?)",
            (d.contact_id, d.owner_id, d.decision, d.reason, counts.get(d.owner_id or "", None), owner.capacity_limit if owner else None, d.task_id, now_iso()))
        return d

    # ---- the manager's queue and the SLA ---------------------------------------
    def release_held(self, contact_id: str, owner_id: str, released_by: str, note: str = "") -> bool:
        """A manager assigns a held lead by hand, or raises a limit with a reason elsewhere and re-runs."""
        with tx(self.conn):
            if not self.hs.set_owner(contact_id, owner_id, expected_current=None):
                return False
            task = self.hs.create_task(owner_id, contact_id, "Lead released from the held queue", note, due_at=clock(self.conn) + timedelta(hours=self.task_due_hours))
            self.conn.execute("UPDATE held_queue SET released_at=?, released_by=?, released_to=? WHERE contact_id=?",
                              (now_iso(), released_by, owner_id, contact_id))
            self.hs.batch_update_contacts([(contact_id, {"auxo_route": "assigned"})])
            self._log(RoutingDecision(contact_id, "assigned", owner_id=owner_id, task_id=task.id, reason=f"released by {released_by}"), {})
        return True

    def stale_assignments(self, sla_hours: int = DEFAULT_FIRST_TOUCH_SLA_HOURS, now: Optional[datetime] = None) -> list[dict]:
        """Assigned leads with no first touch inside the SLA (L07: speed to lead)."""
        now = now or clock(self.conn)
        since = (now - timedelta(hours=sla_hours)).isoformat()
        rows = self.conn.execute(
            "SELECT r.contact_id, r.owner_id, r.created_at FROM routing_log r WHERE r.decision IN ('assigned','holdout_assigned') AND r.created_at <= ? "
            "AND NOT EXISTS (SELECT 1 FROM overrides o WHERE o.contact_id = r.contact_id) "
            "AND NOT EXISTS (SELECT 1 FROM outcomes oc WHERE oc.contact_id = r.contact_id AND oc.worked = 1)", (since,)).fetchall()
        return [dict(r) for r in rows]

    def re_alert_stale(self, sla_hours: int = DEFAULT_FIRST_TOUCH_SLA_HOURS, now: Optional[datetime] = None) -> int:
        n = 0
        for r in self.stale_assignments(sla_hours, now):
            existing = [t for t in self.hs.list_tasks(owner_id=r["owner_id"]) if t.contact_id == r["contact_id"] and t.subject.startswith("Reminder")]
            if existing:
                continue
            self.hs.create_task(r["owner_id"], r["contact_id"], f"Reminder: lead untouched for {sla_hours}h", "Please make first contact or mark it skipped with a reason.")
            n += 1
        return n
