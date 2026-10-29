"""Give each scored lead to an SDR, within capacity, and alert them.

Blueprint job 2 as designed (fixed rules, no AI), with three additions the
knowledge base asked for:

- The capacity line. The brief's team works about 500 of 1,500 MQLs a month.
  Routing is by rank in the current pool (pool.py): free SDR slots go to the
  top of the pool, the rest stay and are re-ranked as new leads arrive. The
  line drawn for people to see is the probability at the rank the team can
  reach in the next week at its monthly capacity; it is what marketing and
  sales negotiate (L03, L10).
- Small queues (BrightPath answers 4 and 9). An SDR holds one waiting lead
  (`capacity_limit` on the owner, counted over leads not yet touched), and
  routing refills the slot as soon as it is touched, so the best new lead takes the next free slot instead of
  waiting behind a month of older ones. That is where faster follow-up
  without more SDRs comes from.
- Freshness. When demand runs ahead of capacity, the pool keeps a backlog of
  good older leads; ranked on probability alone they crowd out the new ones.
  A lead past its fresh window is worth `stale_lead_factor` of what it was
  (BrightPath measured about 0.5 after a day: answer 4), so routing orders the
  pool by probability times that factor. The factor is a setting, and the
  evaluation measures it from outcomes so it can be kept honest.
- An exploration slice (L05). A fixed share of the assigned slots (default
  8%) goes to leads drawn at random from below the line, marked
  `auxo_holdout`, so next month's model has labels the SDRs' taste didn't
  choose. This is the only way the reported lift can be trusted. With
  `explore_below_gate_share` set, part of that slice is drawn instead from
  plain leads HubSpot's MQL gate kept out, scored as they are picked, so the
  model gets outcomes there before anyone ranks by it (pool.py).
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
from typing import Callable, Optional

from ..hubspot.client import HubSpotClient
from ..hubspot.models import Contact, Owner
from ..pool import BELOW_GATE_REASON, BELOW_GATE_STAGE, below_gate_contacts, explore_below_gate_share, pool_contacts
from ..storage.db import clock, dumps, get_setting, now_iso, set_setting, tx

DEFAULT_EXPLORATION_SHARE = 0.08
DEFAULT_TASK_DUE_HOURS = 4
DEFAULT_FIRST_TOUCH_SLA_HOURS = 24
DEFAULT_TEAM_CAPACITY_PER_MONTH = 500    # the brief: 12 SDRs work about 500 MQLs a month
DEFAULT_LINE_HORIZON_DAYS = 7
DEFAULT_HOLD_AFTER_HOURS = 24            # an above-line lead unplaced this long goes to the manager
DEFAULT_FRESH_HOURS = 24
REPLY_REASON = "replied to automated outreach"     # phase 3: a lead answering the sequence jumps the ranking
EXPLORATION_CREDIT = "exploration_slot_credit"
BELOW_GATE_CREDIT = "below_gate_slot_credit"


def priority(conn: sqlite3.Connection, probability: Optional[float], created: datetime, now: datetime) -> float:
    """What touching the lead now is worth: its probability, discounted once it is past the fresh window."""
    fresh_hours = float(get_setting(conn, "fresh_hours") or DEFAULT_FRESH_HOURS)
    factor = float(get_setting(conn, "stale_lead_factor") or 1.0)
    age_hours = (now - created).total_seconds() / 3600
    return (probability or 0.0) * (1.0 if age_hours <= fresh_hours else factor)


@dataclass
class RoutingDecision:
    contact_id: str
    decision: str                 # assigned | held | below_line | incomplete | already_owned | holdout_assigned
    owner_id: Optional[str] = None
    task_id: Optional[str] = None
    reason: str = ""
    probability: Optional[float] = None   # the lead's score when it was handed out
    below_gate: bool = False              # HubSpot's MQL gate had not passed it when it was handed out


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
    def __init__(self, hs: HubSpotClient, conn: sqlite3.Connection, seed: Optional[int] = None,
                 exploration_share: float = DEFAULT_EXPLORATION_SHARE,
                 task_due_hours: int = DEFAULT_TASK_DUE_HOURS, team: str = "sdr", hold_limit: int = 50,
                 hold_after_hours: int = DEFAULT_HOLD_AFTER_HOURS,
                 score: Optional[Callable[[list[Contact]], None]] = None):
        self.hs, self.conn, self.team = hs, conn, team
        self._score = score or self._score_with_live_model
        # Which leads the exploration slice draws must be random. A fixed seed on a Router built fresh for each pass
        # (as the scheduler, the refill and the pages do) would draw the same position every time, so the seed is
        # left to the OS unless a simulation or a test asks for one. How many slots it gets is not left to chance:
        # see _take_slots.
        self.rng = random.Random(seed)
        self.exploration_share = exploration_share
        self.task_due_hours = task_due_hours
        self.hold_limit = hold_limit
        self.hold_after_hours = hold_after_hours

    # ---- the pool and the line ------------------------------------------------
    def pool(self, now: Optional[datetime] = None) -> list[Contact]:
        """Scored leads in the pool, most valuable to touch now first."""
        now = now or clock(self.conn)
        # Phase 3: a lead in the automated sequence is out of the SDRs' pool until it replies or the sequence ends,
        # and a lead that replied is warm, so it goes first whatever its old below-line score: the next free slot
        # is its, including one that a full team had to put in the held queue.
        automated, replied = set(), set()
        for r in self.conn.execute("SELECT contact_id, status FROM tier2 WHERE status IN ('sent', 'replied')"):
            (automated if r["status"] == "sent" else replied).add(r["contact_id"])
        rows = [c for c in pool_contacts(self.hs, self.conn)
                if c.auxo_probability is not None and c.auxo_route != "incomplete" and c.id not in automated]
        rows.sort(key=lambda c: (c.id not in replied, -priority(self.conn, c.auxo_probability, c.createdate, now), c.createdate))
        return rows

    def free_slots(self) -> tuple[dict[str, int], list[Owner], int]:
        owners = [o for o in self.hs.list_owners(active_only=True) if o.team == self.team]
        counts = self.hs.waiting_lead_counts()   # capacity is untouched leads, so a touch frees the slot
        free = sum(max(0, o.capacity_limit - counts.get(o.id, 0)) for o in owners)
        return counts, owners, free

    # ---- one routing pass -----------------------------------------------------
    def run(self, max_assignments: Optional[int] = None, now: Optional[datetime] = None) -> list[RoutingDecision]:
        now = now or clock(self.conn)
        decisions: list[RoutingDecision] = []
        counts, owners, free = self.free_slots()
        if not owners:
            return decisions
        pool = self.pool(now)
        # A held lead that has left the pool (aged out, or owned some other way) is no longer the manager's to place.
        in_pool = {c.id for c in pool}
        for r in self.conn.execute("SELECT contact_id FROM held_queue WHERE released_at IS NULL").fetchall():
            if r["contact_id"] not in in_pool:
                self.conn.execute("UPDATE held_queue SET released_at=?, released_by='left the pool' WHERE contact_id=? AND released_at IS NULL",
                                  (now.isoformat(timespec="seconds"), r["contact_id"]))
        if not pool:
            return decisions
        line_p = self._line_probability(pool, now)
        set_setting(self.conn, "capacity_line_probability", dumps(line_p))
        budget = free if max_assignments is None else min(free, max_assignments)
        if budget <= 0:
            # Nobody is free. Above-line leads that have waited past the hold time go to the manager's queue,
            # bounded so the page stays readable; the rest wait for the next free slot.
            held = {r["contact_id"] for r in self.conn.execute("SELECT contact_id FROM held_queue WHERE released_at IS NULL")}
            cutoff = now - timedelta(hours=self.hold_after_hours)
            waiting = [c for c in pool if c.id not in held and c.createdate <= cutoff
                       and line_p is not None and priority(self.conn, c.auxo_probability, c.createdate, now) >= line_p]
            room = max(0, self.hold_limit - len(held))   # a cap on the whole queue, not per pass: the scheduler routes every few minutes
            for c in waiting[:room]:
                decisions.append(self._assign_one(c, owners, counts, False, now))
            set_setting(self.conn, "last_routing_run", now_iso())
            return decisions

        n_explore = min(budget, self._take_slots(EXPLORATION_CREDIT, budget * self.exploration_share))
        n_top = budget - n_explore
        top = pool[:n_top]
        below = pool[n_top:]
        gated = self._below_gate_picks(n_explore)
        n_explore -= len(gated)
        explore = self.rng.sample(below, min(n_explore, len(below))) if n_explore and below else []
        chosen = [(c, False, None) for c in top] + [(c, True, None) for c in explore] + [(c, True, BELOW_GATE_REASON) for c in gated]
        for contact, is_holdout, reason in chosen:
            decisions.append(self._assign_one(contact, owners, counts, is_holdout, now, reason))
        set_setting(self.conn, "last_routing_run", now_iso())
        return decisions

    def _below_gate_picks(self, n_explore: int) -> list[Contact]:
        """The share of this pass's exploration slots that goes below HubSpot's gate, drawn at random from plain leads
        and scored now: they are not in the pool, so nothing has scored them, and the SDR and the later check of the
        model's predictions both need the score as it stood when the lead was handed out."""
        share = explore_below_gate_share(self.conn)
        if not n_explore or share <= 0:
            return []
        n = min(n_explore, self._take_slots(BELOW_GATE_CREDIT, n_explore * share))
        candidates = [c for c in below_gate_contacts(self.hs, self.conn) if c.email and c.channel] if n else []
        picks = self.rng.sample(sorted(candidates, key=lambda c: c.id), min(n, len(candidates))) if candidates else []
        if picks:
            self._score(picks)
            picks = [c for c in (self.hs.get_contact(p.id) for p in picks) if c is not None and c.auxo_probability is not None]
        return picks

    def _take_slots(self, key: str, expected: float) -> int:
        """Whole slots out of a running fractional credit kept in settings. Most passes free one slot or a few, and
        8% of one slot rounds to nothing every time; carried over, the credit hands out a slot every twelve or so
        passes, so the share holds over time however small and frequent the passes are."""
        credit = float(get_setting(self.conn, key) or 0.0) + expected
        n = int(credit)
        set_setting(self.conn, key, repr(credit - n))
        return n

    def _score_with_live_model(self, contacts: list[Contact]) -> None:
        from ..llm.client import make_llm
        from ..scoring.pipeline import Scorer, load_live_model
        Scorer(self.hs, self.conn, load_live_model(self.conn), make_llm(conn=self.conn)).score_batch(contacts)

    def _line_probability(self, pool: list[Contact], now: datetime) -> Optional[float]:
        """The value at the rank the team reaches in the line horizon at its monthly capacity."""
        per_month = float(get_setting(self.conn, "team_capacity_per_month") or DEFAULT_TEAM_CAPACITY_PER_MONTH)
        days = float(get_setting(self.conn, "capacity_line_days") or DEFAULT_LINE_HORIZON_DAYS)
        k = max(1, int(round(per_month * days / 30.4)))
        c = pool[min(k, len(pool)) - 1]
        return priority(self.conn, c.auxo_probability, c.createdate, now)

    def assign_now(self, contact: Contact, reason: str, prefer_owner_id: Optional[str] = None,
                   now: Optional[datetime] = None) -> RoutingDecision:
        """Hand one lead out ahead of the ranking (a reply to automated outreach): to the preferred SDR if they have
        a slot, else the next in rotation, else the held queue."""
        now = now or clock(self.conn)
        counts, owners, _ = self.free_slots()
        if not owners:
            return self._log(RoutingDecision(contact.id, "held", reason="no active SDRs"), counts)
        return self._assign_one(contact, owners, counts, False, now, reason, prefer_owner_id)

    def _next_owner(self, owners: list[Owner], counts: dict[str, int], prefer: Optional[str] = None) -> Optional[Owner]:
        """Round robin starting after the last assigned owner, skipping anyone at their limit. A preferred owner
        with room comes first and leaves the rotation where it was."""
        for o in owners:
            if prefer and o.id == prefer and counts.get(o.id, 0) < o.capacity_limit:
                return o
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
                    is_holdout: bool, now: datetime, reason: Optional[str] = None, prefer: Optional[str] = None) -> RoutingDecision:
        missing = _missing_fields(contact)
        if missing:
            self.hs.batch_update_contacts([(contact.id, {"auxo_route": "incomplete", "auxo_review_flag": "missing: " + ", ".join(missing)})])
            return self._log(RoutingDecision(contact.id, "incomplete", reason=", ".join(missing)), counts)
        with tx(self.conn):
            fresh = self.hs.get_contact(contact.id)
            if fresh is None or fresh.hubspot_owner_id:
                return self._log(RoutingDecision(contact.id, "already_owned", owner_id=fresh.hubspot_owner_id if fresh else None), counts)
            owner = self._next_owner(owners, counts, prefer)
            if owner is None:
                self.conn.execute("INSERT OR IGNORE INTO held_queue(contact_id, held_at, reason) VALUES(?,?,?)",
                                  (contact.id, now.isoformat(timespec="seconds"), (reason + "; " if reason else "") + "all SDRs at capacity"))
                self.hs.batch_update_contacts([(contact.id, {"auxo_route": "held"})])
                return self._log(RoutingDecision(contact.id, "held", reason=(reason + "; " if reason else "") + "all SDRs at capacity"), counts)
            if not self.hs.set_owner(contact.id, owner.id, expected_current=None):
                return self._log(RoutingDecision(contact.id, "already_owned"), counts)
            counts[owner.id] = counts.get(owner.id, 0) + 1
            if owner.id != prefer:
                set_setting(self.conn, f"rotation:{self.team}", owner.id)
            subject = f"New lead to qualify: rank {contact.auxo_rank or '?'}, {round(100 * (contact.auxo_probability or 0))}% demo likelihood"
            if is_holdout:
                subject = "Calibration lead (please work like any other): " + subject
            elif reason == REPLY_REASON:
                subject = "Replied to your automated email: " + subject
            task = self.hs.create_task(owner.id, contact.id, subject, contact.auxo_reason or "", due_at=now + timedelta(hours=self.task_due_hours))
            self.hs.batch_update_contacts([(contact.id, {"auxo_route": "assigned", "auxo_holdout": bool(is_holdout)})])
            below_gate = self._mark_below_gate(fresh)
            self.conn.execute("UPDATE held_queue SET released_at=?, released_by='routing', released_to=? WHERE contact_id=? AND released_at IS NULL",
                              (now.isoformat(timespec="seconds"), owner.id, contact.id))
        d = RoutingDecision(contact.id, "holdout_assigned" if is_holdout else "assigned", owner_id=owner.id, task_id=task.id,
                            reason=reason or ("exploration slice" if is_holdout else "above the capacity line"),
                            probability=fresh.auxo_probability, below_gate=below_gate)
        return self._log(d, counts, owner)

    def _mark_below_gate(self, before: Contact) -> bool:
        """Whether the lead was still below HubSpot's gate when it was handed out. If so, flag it on the contact,
        since the assignment just moved it to SQL and its stage no longer says so. A write of its own: a portal
        that has not created the property yet loses the badge, not the assignment's other fields."""
        if before.lifecycle_stage != BELOW_GATE_STAGE:
            return False
        self.hs.batch_update_contacts([(before.id, {"auxo_below_gate": True})])
        return True

    def _log(self, d: RoutingDecision, counts: dict[str, int], owner: Optional[Owner] = None) -> RoutingDecision:
        self.conn.execute(
            "INSERT INTO routing_log(contact_id, owner_id, decision, reason, open_count_at_decision, capacity_limit, task_id, created_at, "
            "probability, below_gate) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (d.contact_id, d.owner_id, d.decision, d.reason, counts.get(d.owner_id or "", None), owner.capacity_limit if owner else None, d.task_id,
             clock(self.conn).isoformat(timespec="seconds"), d.probability, int(d.below_gate)))
        return d

    # ---- the manager's queue and the SLA ---------------------------------------
    def release_held(self, contact_id: str, owner_id: str, released_by: str, note: str = "") -> bool:
        """A manager assigns a held lead by hand, or raises a limit with a reason elsewhere and re-runs."""
        with tx(self.conn):
            before = self.hs.get_contact(contact_id)
            if before is None or not self.hs.set_owner(contact_id, owner_id, expected_current=None):
                return False
            task = self.hs.create_task(owner_id, contact_id, "Lead released from the held queue", note, due_at=clock(self.conn) + timedelta(hours=self.task_due_hours))
            self.conn.execute("UPDATE held_queue SET released_at=?, released_by=?, released_to=? WHERE contact_id=?",
                              (clock(self.conn).isoformat(timespec="seconds"), released_by, owner_id, contact_id))
            self.hs.batch_update_contacts([(contact_id, {"auxo_route": "assigned"})])
            self._log(RoutingDecision(contact_id, "assigned", owner_id=owner_id, task_id=task.id, reason=f"released by {released_by}",
                                      probability=before.auxo_probability, below_gate=self._mark_below_gate(before)), {})
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
