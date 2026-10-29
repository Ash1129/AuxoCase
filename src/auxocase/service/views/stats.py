"""Figures more than one page shows."""
from __future__ import annotations

from datetime import timedelta
from typing import Optional

from ...storage.db import clock


def hit_rate(conn, days: int = 30) -> Optional[dict]:
    """How the leads handed out in the last `days` (service clock) did, once worked: the ranked ones and the
    exploration slice apart, since the slice is drawn at random and books less (L12: show the hit rate beside any
    miss). Top-level n/booked/rate are the two together; None when nothing handed out in the window was worked."""
    since = (clock(conn) - timedelta(days=days)).isoformat(timespec="seconds")
    rows = {r["decision"]: r for r in conn.execute(
        "SELECT r.decision, COUNT(DISTINCT o.contact_id) n, SUM(o.demo_booked) d FROM outcomes o JOIN routing_log r ON r.contact_id = o.contact_id "
        "WHERE o.worked = 1 AND r.decision IN ('assigned','holdout_assigned') AND r.created_at >= ? GROUP BY r.decision", (since,))}

    def part(decision: str) -> dict:
        r = rows.get(decision)
        n, booked = (r["n"], r["d"] or 0) if r else (0, 0)
        return {"n": n, "booked": booked, "rate": round(booked / n, 3) if n else None}

    ranked, exploration = part("assigned"), part("holdout_assigned")
    n = ranked["n"] + exploration["n"]
    if not n:
        return None
    booked = ranked["booked"] + exploration["booked"]
    return {"days": days, "since": since, "n": n, "booked": booked, "rate": round(booked / n, 3),
            "ranked": ranked, "exploration": exploration}
