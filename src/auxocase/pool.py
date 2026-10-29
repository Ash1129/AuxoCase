"""The pool: the leads the service ranks and routes.

Unowned leads in the pool's lifecycle stages, created within the pool's age
window. Older leads stay in HubSpot with whatever disposition they were given
(BrightPath answer 3); a lead that has waited a month unworked is not where
an SDR's next hour goes. Scoring ranks within this pool and routing draws
from it, so both read it from here.

Stages default to MQLs only, HubSpot's gate as the brief describes it. Adding
"lead" lets the model replace that gate once exploration labels exist below
it (docs/ARCHITECTURE.md, "Phase 2").

Before that, `explore_below_gate_share` sends part of the exploration slice to
plain leads drawn at random from below the gate, so outcomes exist there before
anyone relies on the model's ranking of them. Both are off by default; `run.py
below-gate` switches them and reports how many labels below the gate exist.
"""
from __future__ import annotations

import sqlite3
from datetime import timedelta

from .hubspot.client import HubSpotClient
from .hubspot.models import Contact
from .storage.db import clock, get_setting

DEFAULT_STAGES = ("marketingqualifiedlead",)
DEFAULT_MAX_AGE_DAYS = 30
BELOW_GATE_STAGE = "lead"               # HubSpot's stage for a contact its MQL gate has not passed
BELOW_GATE_REASON = "exploration slice, below HubSpot's MQL gate"   # the routing log's reason for those picks
BELOW_GATE_READY_WORKED = 100           # worked below-gate picks with matured labels before ranking there is judged


def stages(conn: sqlite3.Connection) -> tuple[str, ...]:
    v = get_setting(conn, "pool_stages")
    return tuple(s.strip() for s in v.split(",") if s.strip()) if v else DEFAULT_STAGES


def max_age_days(conn: sqlite3.Connection) -> int:
    v = get_setting(conn, "pool_max_age_days")
    return int(v) if v else DEFAULT_MAX_AGE_DAYS


def pool_contacts(hs: HubSpotClient, conn: sqlite3.Connection) -> list[Contact]:
    since = clock(conn) - timedelta(days=max_age_days(conn))
    out: list[Contact] = []
    for stage in stages(conn):
        out += [c for c in hs.list_contacts(lifecycle_stage=stage, created_since=since) if not c.hubspot_owner_id]
    return out


def ranks_below_gate(conn: sqlite3.Connection) -> bool:
    return BELOW_GATE_STAGE in stages(conn)


def explore_below_gate_share(conn: sqlite3.Connection) -> float:
    """The share of the exploration slice drawn from below the gate while the pool itself stays MQLs (0 = off)."""
    v = get_setting(conn, "explore_below_gate_share")
    return min(1.0, max(0.0, float(v))) if v else 0.0


def below_gate_contacts(hs: HubSpotClient, conn: sqlite3.Connection) -> list[Contact]:
    """Unowned plain leads inside the pool's age window: what below-gate exploration draws from. Empty when the
    pool already ranks below the gate, since the ordinary exploration draw then covers them."""
    if ranks_below_gate(conn):
        return []
    since = clock(conn) - timedelta(days=max_age_days(conn))
    return [c for c in hs.list_contacts(lifecycle_stage=BELOW_GATE_STAGE, created_since=since) if not c.hubspot_owner_id]


def passed_gate(c: Contact) -> bool:
    """Whether HubSpot's MQL gate passed this lead. A plain lead this service handed out moved to SQL on assignment
    without the gate's say, and carries `auxo_below_gate`, so its stage alone would count it as an MQL."""
    return c.lifecycle_stage != BELOW_GATE_STAGE and not c.auxo_below_gate
