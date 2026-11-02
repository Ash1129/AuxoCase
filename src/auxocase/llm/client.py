"""One small interface over three ways to reach Claude.

- `cli`: the Claude Code CLI in headless mode (`claude -p --output-format
  json`). This is what the owner asked to use in development; it bills the
  signed-in Claude account, needs a logged-in CLI on the machine, and is not
  a production path (no per-request isolation, no rate-limit handling).
  `--bare` would be the leanest call, but it reads only ANTHROPIC_API_KEY and
  never a claude.ai sign-in, so it is used only when a key is set; otherwise
  the call switches off tools, MCP servers, skills and settings files itself.
- `api`: the Anthropic Messages API with an API key, the production path.
- `offline`: a scripted stand-in with no model at all, used by every test and
  by the demo when no key or CLI is available. It reads the same prompts and
  answers from fixed rules, so the rest of the system is exercised end to end.

The caller never sees which one answered; it gets text back, and every
answer is checked by code before it changes anything (the "checker tied to
the record" principle from Blueprint job 1).
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
from dataclasses import dataclass, field
from typing import Optional, Protocol

from . import usage


class LLM(Protocol):
    name: str
    def complete(self, system: str, user: str, max_tokens: int = 600) -> str: ...


CLI_MODELS = ("sonnet", "opus")      # the CLI runs on the owner's Claude Code plan: Sonnet for the checked, short answers, Opus for what a lead reads
DRAFT_TASKS = ("outreach_email", "call_brief")     # what a lead reads or hears: Opus
WEB_TASKS = ("enrich",)              # looks a lead up on the public web: search and page fetch on, nothing else; code checks the quote
CLI_WEB_TOOLS = "WebSearch,WebFetch"
API_WEB_TOOLS = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 4}]


def _cli_model(value: str, what: str) -> str:
    if not any(m in value for m in CLI_MODELS):
        raise ValueError(f"{what} must be one of {CLI_MODELS} (or a full model id of one), not {value!r}")
    return value


@dataclass
class ClaudeCLI:
    model: str = os.environ.get("AUXO_CLI_MODEL", "sonnet")                 # signals, reasons, loss reasons
    draft_model: str = os.environ.get("AUXO_CLI_DRAFT_MODEL", "opus")       # the emails a lead reads and the call brief
    timeout_s: int = 90
    name: str = "claude-cli"
    conn: Optional[sqlite3.Connection] = field(default=None, repr=False)   # where each call's metered cost is recorded

    def __post_init__(self) -> None:
        _cli_model(self.model, "AUXO_CLI_MODEL")
        _cli_model(self.draft_model, "AUXO_CLI_DRAFT_MODEL")

    def model_for(self, task: str) -> str:
        return self.draft_model if task in DRAFT_TASKS else self.model

    def command(self, exe: str, system: str, user: str) -> list[str]:
        task = usage.task_of(user)
        tools = CLI_WEB_TOOLS if task in WEB_TASKS else ""       # only the web lookup may search; every other task answers from its prompt
        if os.environ.get("ANTHROPIC_API_KEY"):
            isolation = ["--bare", "--tools", tools]       # API-key auth only; skips hooks, plugins, MCP, CLAUDE.md
        else:                                              # a claude.ai sign-in: same isolation, flag by flag
            isolation = ["--restricted", "--tools", tools, "--strict-mcp-config", "--disable-slash-commands"]
        return [exe, "-p", "--output-format", "json", *isolation, "--no-session-persistence",
                "--model", self.model_for(task), "--system-prompt", system, user]

    def complete(self, system: str, user: str, max_tokens: int = 600) -> str:
        exe = shutil.which("claude")
        if not exe:
            raise RuntimeError("claude CLI not found on PATH")
        # Run outside the repo so no project CLAUDE.md or settings ride along with the prompt. The CLI thinks by
        # default, which turned a three-second draft into thirty; these are short, checked answers, so it is off.
        # The telemetry the shell carries (Revenium's exporter, when the owner set it up) goes with the call, tagged
        # with the product and the task so Revenium can show the service's spend apart (llm/usage.py).
        task = usage.task_of(user)
        env = {**os.environ, "MAX_THINKING_TOKENS": "0",
               "OTEL_RESOURCE_ATTRIBUTES": usage.resource_attributes(os.environ.get("OTEL_RESOURCE_ATTRIBUTES", ""), task)}
        timeout = self.timeout_s * 2 if task in WEB_TASKS else self.timeout_s      # a lookup searches and reads pages
        proc = subprocess.run(self.command(exe, system, user), capture_output=True, text=True,
                              timeout=timeout, cwd=tempfile.gettempdir(), env=env)
        try:
            data = json.loads(proc.stdout.strip().splitlines()[-1])
        except (json.JSONDecodeError, IndexError) as e:
            raise RuntimeError(f"claude CLI returned no JSON (exit {proc.returncode}): {proc.stderr[:200]}") from e
        if data.get("is_error"):
            raise RuntimeError(f"claude CLI error: {data.get('result')}")
        if self.conn is not None:
            # modelUsage lists every model the CLI touched, its own small side calls (Haiku) first: record the one
            # that did the work, the entry that carried the cost, else the model the task asked for.
            mu = data.get("modelUsage") or {}
            model = max(mu, key=lambda m: float((mu[m] or {}).get("costUSD") or 0)) if mu else self.model_for(task)
            usage.record(self.conn, self.name, model, task, data.get("usage"), data.get("total_cost_usd"), data.get("duration_api_ms"))
        return str(data.get("result", ""))


@dataclass
class AnthropicAPI:
    model: str = os.environ.get("AUXO_API_MODEL", "claude-sonnet-5-5")
    name: str = "anthropic-api"
    conn: Optional[sqlite3.Connection] = field(default=None, repr=False)

    def complete(self, system: str, user: str, max_tokens: int = 600) -> str:
        try:
            import anthropic  # optional dependency
        except ImportError as e:
            raise RuntimeError("pip install anthropic to use AUXO_LLM=api") from e
        client = anthropic.Anthropic()
        extra = {"tools": API_WEB_TOOLS} if usage.task_of(user) in WEB_TASKS else {}    # the server-side web search, for the lookup only
        msg = client.messages.create(model=self.model, max_tokens=max_tokens, system=system,
                                     messages=[{"role": "user", "content": user}], **extra)
        if self.conn is not None:
            # Tokens only: the API reports no price. Revenium's Anthropic middleware is the metering path for this route.
            u = getattr(msg, "usage", None)
            usage.record(self.conn, self.name, getattr(msg, "model", self.model), usage.task_of(user),
                         {"input_tokens": getattr(u, "input_tokens", None), "output_tokens": getattr(u, "output_tokens", None),
                          "cache_read_input_tokens": getattr(u, "cache_read_input_tokens", None),
                          "cache_creation_input_tokens": getattr(u, "cache_creation_input_tokens", None)}, None, None)
        return "".join(getattr(b, "text", "") for b in msg.content)


@dataclass
class Offline:
    """Scripted answers. Knows the two prompts the service sends (signals and reason) by a marker line."""
    name: str = "offline"

    def complete(self, system: str, user: str, max_tokens: int = 600) -> str:
        from .signals import offline_signals
        from .reason import offline_reason
        if user.startswith("TASK: signals"):
            return offline_signals(user)
        if user.startswith("TASK: reason"):
            return offline_reason(user)
        if user.startswith("TASK: loss_reason"):
            from .loss_reasons import offline_loss_reason
            return offline_loss_reason(user)
        if user.startswith("TASK: flags"):
            from .flags import offline_flags
            return offline_flags(user)
        if user.startswith("TASK: verdict"):
            from .verdict import offline_verdict
            return offline_verdict(user)
        if user.startswith("TASK: enrich"):
            return "{}"          # no web offline: nothing found, the lead stays blank
        return "{}"


def make_llm(kind: Optional[str] = None, conn: Optional[sqlite3.Connection] = None) -> LLM:
    """With `conn`, every call's tokens and metered cost are recorded in that database (`llm_usage`)."""
    kind = (kind or os.environ.get("AUXO_LLM", "offline")).lower()
    if kind == "cli":
        return ClaudeCLI(conn=conn)
    if kind == "api":
        return AnthropicAPI(conn=conn)
    return Offline()
