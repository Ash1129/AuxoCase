"""The pool: the leads the service ranks and routes.

Unowned leads in the pool's lifecycle stages, created within the pool's age
window. Older leads stay in HubSpot with whatever disposition they were given
(BrightPath answer 3); a lead that has waited a month unworked is not where
an SDR's next hour goes. Scoring ranks within this pool and routing draws
from it, so both read it from here.

Stages default to MQLs only, HubSpot's gate as the brief describes it. Adding
"lead" lets the model replace that gate once exploration labels exist below
it (docs/ARCHITECTURE.md, "Phase 2").
"""
from __future__ import annotations

import sqlite3
from datetime import timedelta

from .hubspot.client import HubSpotClient
from .hubspot.models import Contact
from .storage.db import clock, get_setting

DEFAULT_STAGES = ("marketingqualifiedlead",)
DEFAULT_MAX_AGE_DAYS = 30


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
