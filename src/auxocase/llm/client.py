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
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Optional, Protocol


class LLM(Protocol):
    name: str
    def complete(self, system: str, user: str, max_tokens: int = 600) -> str: ...


@dataclass
class ClaudeCLI:
    model: str = os.environ.get("AUXO_CLI_MODEL", "haiku")
    timeout_s: int = 90
    name: str = "claude-cli"

    def command(self, exe: str, system: str, user: str) -> list[str]:
        if os.environ.get("ANTHROPIC_API_KEY"):
            isolation = ["--bare"]       # API-key auth only; skips hooks, plugins, MCP, CLAUDE.md
        else:                            # a claude.ai sign-in: same isolation, flag by flag
            isolation = ["--restricted", "--tools", "", "--strict-mcp-config", "--disable-slash-commands"]
        return [exe, "-p", "--output-format", "json", *isolation, "--no-session-persistence",
                "--model", self.model, "--system-prompt", system, user]

    def complete(self, system: str, user: str, max_tokens: int = 600) -> str:
        exe = shutil.which("claude")
        if not exe:
            raise RuntimeError("claude CLI not found on PATH")
        # Run outside the repo so no project CLAUDE.md or settings ride along with the prompt. The CLI thinks by
        # default, which turned a three-second draft into thirty; these are short, checked answers, so it is off.
        proc = subprocess.run(self.command(exe, system, user), capture_output=True, text=True,
                              timeout=self.timeout_s, cwd=tempfile.gettempdir(),
                              env={**os.environ, "MAX_THINKING_TOKENS": "0"})
        try:
            data = json.loads(proc.stdout.strip().splitlines()[-1])
        except (json.JSONDecodeError, IndexError) as e:
            raise RuntimeError(f"claude CLI returned no JSON (exit {proc.returncode}): {proc.stderr[:200]}") from e
        if data.get("is_error"):
            raise RuntimeError(f"claude CLI error: {data.get('result')}")
        return str(data.get("result", ""))


@dataclass
class AnthropicAPI:
    model: str = os.environ.get("AUXO_API_MODEL", "claude-haiku-5-5")
    name: str = "anthropic-api"

    def complete(self, system: str, user: str, max_tokens: int = 600) -> str:
        try:
            import anthropic  # optional dependency
        except ImportError as e:
            raise RuntimeError("pip install anthropic to use AUXO_LLM=api") from e
        client = anthropic.Anthropic()
        msg = client.messages.create(model=self.model, max_tokens=max_tokens, system=system,
                                     messages=[{"role": "user", "content": user}])
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
        return "{}"


def make_llm(kind: Optional[str] = None) -> LLM:
    kind = (kind or os.environ.get("AUXO_LLM", "offline")).lower()
    if kind == "cli":
        return ClaudeCLI()
    if kind == "api":
        return AnthropicAPI()
    return Offline()
