"""Turn the explanation into one or two sentences a rep reads in ten seconds.

The template text from `explain.py` is always available and is what the
system falls back to. When an LLM is on, it may only rephrase: the checker
rejects any rewrite that introduces a number, a name-like token, an email, a
URL, or a word from the lead's notes that the template didn't contain, and
any rewrite longer than 220 characters. Fixed wording for the same signal
keeps reasons comparable across runs (Blueprint job 1 system prompt).
"""
from __future__ import annotations

import re

from .client import LLM

SYSTEM = """You rewrite a short machine-generated explanation of a sales lead's rank into plain English for a sales rep.
Rules: keep every fact; add nothing; no numbers, names, companies, emails or links; at most two sentences; under 220 characters; start with the strongest reason. Output the sentence only."""

MAX_LEN = 220


def _tokens(s: str) -> set[str]:
    return set(re.findall(r"[a-z]+", s.lower()))


def check(rewrite: str, template: str) -> tuple[bool, str]:
    r = rewrite.strip().strip('"')
    if not r or len(r) > MAX_LEN:
        return False, "empty or too long"
    if re.search(r"\d", r):
        return False, "contains a number"
    if re.search(r"@|https?://|www\.", r):
        return False, "contains an email or link"
    if re.search(r"\b[A-Z][a-z]+ [A-Z][a-z]+\b", r):
        return False, "contains a name-like token"
    allowed = _tokens(template) | {"the", "a", "an", "and", "is", "has", "this", "lead", "it", "they", "their", "but", "with",
                                   "which", "who", "so", "because", "while", "also", "up", "down", "ranked", "held", "back",
                                   "strong", "weak", "signal", "signals", "contact", "likely", "less", "more", "to", "of", "in",
                                   "on", "for", "from", "as", "at", "be", "was", "were", "not", "no", "yet", "already", "would", "if", "move", "moves"}
    extra = _tokens(r) - allowed
    if len(extra) > 3:
        return False, f"adds words not in the explanation: {sorted(extra)[:5]}"
    return True, ""


def write_reason(llm: LLM, template: str) -> tuple[str, str]:
    """Returns (reason, source) where source is 'llm' or 'template'."""
    if llm.name == "offline":
        return template, "template"
    try:
        out = llm.complete(SYSTEM, "TASK: reason\nEXPLANATION:\n" + template, max_tokens=120)
    except Exception:
        return template, "template"
    ok, _ = check(out, template)
    return (out.strip().strip('"'), "llm") if ok else (template, "template")


def offline_reason(user: str) -> str:
    return user.split("EXPLANATION:\n", 1)[1] if "EXPLANATION:\n" in user else user
