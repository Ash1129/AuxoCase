"""What the model calls cost, metered the way Revenium meters them.

The owner meters Claude Code with Revenium's CLI (`revenium-metering`), which
has Claude Code export every call's tokens and the cost Claude Code computed
to Revenium over OpenTelemetry. The service's own Claude calls run through
that same CLI (`llm/client.py`, `AUXO_LLM=cli`), so they are metered too, in
the owner's Revenium tenant. Two things make them useful there and here:

- Each call is tagged with Revenium's own resource attributes (OTLP
  integration): `revenium.product.name` = `AuxoCase`, `revenium.task.type` =
  the prompt's task (`signals`, `reason`, `outreach_email`, `loss_reason`)
  and `revenium.agent.name` = `auxocase`, so Revenium's cost-by-product and
  cost-by-task views show the service apart from the owner's own sessions.
- The same per-call figure (`total_cost_usd` and the token counts in the
  CLI's JSON) is written to `llm_usage` with the task and, for a draft, the
  lead it was for. The cost report prices drafting from this, not from an
  assumption, whenever calls have run.

`revenium.py`-style read-back: `costs.revenium_cost` asks Revenium's
analytics API for the product's metered cost over a range, with a read key
(`rev_rk_*`) or write key (`rev_sk_*`) in `REVENIUM_API_KEY`. The metering
key the CLI installs (`rev_mk_*`) can only send.
"""
from __future__ import annotations

import contextvars
import re
import sqlite3
from contextlib import contextmanager
from typing import Iterator, Optional

from ..storage.db import now_iso

PRODUCT = "AuxoCase"
AGENT = "auxocase"
_reference: contextvars.ContextVar[str] = contextvars.ContextVar("llm_reference", default="")


@contextmanager
def reference(ref: str) -> Iterator[None]:
    """Name what the next calls are for (a lead's id), so a draft's cost can be joined to the lead."""
    token = _reference.set(ref)
    try:
        yield
    finally:
        _reference.reset(token)


def current_reference() -> str:
    return _reference.get()


def task_of(user_prompt: str) -> str:
    """The prompts all open with a `TASK: <name>` line (signals, reason, outreach_email, loss_reason)."""
    m = re.match(r"\s*TASK:\s*([a-z_]+)", user_prompt or "")
    return m.group(1) if m else "unknown"


def resource_attributes(existing: str, task: str) -> str:
    """OTEL_RESOURCE_ATTRIBUTES for one call: what the shell set (Revenium's source and the owner's email), plus
    the product, task and agent Revenium groups by. A later key wins in OTel, so the service's values stand."""
    ours = {"revenium.product.name": PRODUCT, "revenium.task.type": task, "revenium.agent.name": AGENT}
    kept = [kv for kv in (existing or "").split(",") if kv and kv.split("=", 1)[0] not in ours]
    return ",".join(kept + [f"{k}={v}" for k, v in ours.items()])


def record(conn: sqlite3.Connection, reader: str, model: str, task: str, usage: Optional[dict], cost_usd: Optional[float],
           duration_ms: Optional[float]) -> None:
    u = usage or {}
    conn.execute(
        "INSERT INTO llm_usage(reader, model, task, reference, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, "
        "cost_usd, duration_ms, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (reader, model, task, current_reference() or None, u.get("input_tokens"), u.get("output_tokens"),
         u.get("cache_read_input_tokens"), u.get("cache_creation_input_tokens"), cost_usd, duration_ms, now_iso()))


def summary(conn: sqlite3.Connection) -> dict:
    """Calls, tokens and metered cost by task, and in all. `priced` is the share of calls that carried a cost (the
    CLI reports one; the API path records tokens only)."""
    rows = conn.execute(
        "SELECT task, COUNT(*) calls, COALESCE(SUM(input_tokens), 0) input_tokens, COALESCE(SUM(output_tokens), 0) output_tokens, "
        "COALESCE(SUM(cost_usd), 0) cost_usd, SUM(cost_usd IS NOT NULL) priced, MIN(created_at) first, MAX(created_at) last "
        "FROM llm_usage GROUP BY task ORDER BY cost_usd DESC").fetchall()
    by_task = [dict(r) for r in rows]
    for r in by_task:
        r["cost_usd"] = round(r["cost_usd"], 4)
    calls = sum(r["calls"] for r in by_task)
    return {"calls": calls, "cost_usd": round(sum(r["cost_usd"] for r in by_task), 4), "priced": sum(r["priced"] for r in by_task),
            "input_tokens": sum(r["input_tokens"] for r in by_task), "output_tokens": sum(r["output_tokens"] for r in by_task),
            "by_task": by_task, "first": min((r["first"] for r in by_task), default=None), "last": max((r["last"] for r in by_task), default=None)}


def draft_costs(conn: sqlite3.Connection, contact_ids: Optional[set[str]] = None) -> dict:
    """What first-email drafts cost: the mean metered cost of one `outreach_email` call, and for the given leads
    (every drafted lead when none are given) how many had a metered draft and what those cost. None when no priced
    draft has run."""
    r = conn.execute("SELECT COUNT(*) n, AVG(cost_usd) mean FROM llm_usage WHERE task='outreach_email' AND cost_usd IS NOT NULL").fetchone()
    if not r["n"]:
        return {"drafts_priced": 0, "mean_cost": None, "total": None, "metered": 0}
    where, params = "task='outreach_email' AND cost_usd IS NOT NULL", ()
    if contact_ids is not None:
        where += " AND reference IN (" + (",".join("?" * len(contact_ids)) or "''") + ")"
        params = tuple(contact_ids)
    s = conn.execute(f"SELECT COUNT(DISTINCT reference) n, COALESCE(SUM(cost_usd), 0) t FROM llm_usage WHERE {where}", params).fetchone()
    return {"drafts_priced": r["n"], "mean_cost": round(r["mean"], 5), "total": round(s["t"], 4), "metered": s["n"]}
