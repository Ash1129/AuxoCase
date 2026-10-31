"""The clock as the pages show it: Eastern time with seconds, from the service's UTC timestamps. AUXO_TZ picks another
zone (an IANA name); the label is 'ET' for New York and the zone's own abbreviation otherwise. The page script keeps
a shown clock ticking from the rendered instant."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

ZONE = os.environ.get("AUXO_TZ", "America/New_York")
TZ = ZoneInfo(ZONE)
LABEL = "ET" if ZONE == "America/New_York" else datetime.now(TZ).strftime("%Z")


def display(dt: datetime) -> datetime:
    """A naive UTC timestamp (as the service stores them) in the display zone."""
    return (dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt).astimezone(TZ)


def clock_text(dt: datetime, weekday: bool = False) -> str:
    """'31 Oct 2026 11:16:42', or with the weekday 'Sat 31 Oct 2026, 11:16:42'."""
    d = display(dt)
    return d.strftime("%a %d %b %Y, %H:%M:%S") if weekday else d.strftime("%d %b %Y %H:%M:%S")


def clock_iso(dt: datetime) -> str:
    """The instant for the page script: UTC, 'Z'-suffixed."""
    return (dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
