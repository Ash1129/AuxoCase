"""Read free-text closed-lost reasons into a fixed set of categories.

BrightPath answer 2: loss reasons are free text and messy. The reader puts
each into one category from a fixed list and quotes the words that put it
there; code keeps the category only if the quote is in the text, otherwise
the reason is "unclassified". Nothing here touches a lead's score: the
categories go to the report both teams read, by channel and company size,
which is where "larger companies need more features" (answer 6) shows up or
does not.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .client import LLM

CATEGORIES = {
    "price": "price or budget",
    "competitor": "chose or stayed with another vendor",
    "timing": "not now, timing",
    "missing_feature": "a feature or integration we lack",
    "no_decision": "no decision, went dark, champion left",
    "poor_fit": "not a fit (size, workforce type)",
    "other": "a reason that fits none of the above",
}

SYSTEM = (
    "You classify why a sales deal was lost, from the sales rep's free-text note. "
    "Answer with JSON only: {\"category\": one of " + ", ".join(CATEGORIES) + ", \"quote\": the exact words from the note "
    "that show the category (copy them, 1 to 12 words)}. If the note is empty or says nothing about why, answer "
    "{\"category\": \"other\", \"quote\": \"\"}. Never infer a reason the note does not state."
)


@dataclass
class LossReading:
    category: str
    quote: str
    source: str            # model | unclassified | empty


def _words(s: str) -> list[str]:
    return re.sub(r"[^a-z0-9$ ]+", " ", s.lower()).split()


def check(raw: str, text: str) -> LossReading:
    if not text.strip() or not re.search(r"[a-z]", text.lower()):
        return LossReading("other", "", "empty")
    start = raw.find("{")
    try:
        data = json.loads(raw[start:]) if start >= 0 else {}
    except json.JSONDecodeError:
        data = {}
    cat = str(data.get("category", "")).strip()
    quote = str(data.get("quote", "")).strip()
    if cat == "other" and not quote:          # "says nothing about why": no claim, nothing to verify
        return LossReading("other", "", "model")
    qw, tw = _words(quote), _words(text)
    found = bool(qw) and any(tw[i:i + len(qw)] == qw for i in range(len(tw) - len(qw) + 1))
    if cat not in CATEGORIES or not found or len(qw) > 12:
        return LossReading("unclassified", quote, "unclassified")
    return LossReading(cat, quote, "model")


def read_loss_reason(llm: LLM, text: str) -> LossReading:
    if not text.strip():
        return LossReading("other", "", "empty")
    raw = llm.complete(SYSTEM, f"TASK: loss_reason\nNOTE:\n{text}", max_tokens=120)
    return check(raw, text)


_RULES = [
    ("competitor", r"\b(adp|paychex|gusto|rippling|paycom|quickbooks payroll|justworks|current vendor)\b"),
    ("missing_feature", r"\b(multi entity|integration|accrual|benefits admin|global payroll|reporting|netsuite)\b"),
    ("price", r"(\$\$\$|\bexpensive\b|\bprice\b|\bpricing\b|\bbudget\b|\bcost\b|\bcheaper\b)"),
    ("timing", r"\b(not now|timing|next yr|next year|revisit|year end)\b"),
    ("no_decision", r"\b(went dark|no response|ghosted|champion left|no decision)\b"),
    ("poor_fit", r"\b(too small|not a fit|only \d+ employees|contractors only)\b"),
]


def offline_loss_reason(user: str) -> str:
    """Scripted reader for tests and the offline mode: the first rule that matches, quoting what matched."""
    text = user.split("NOTE:\n", 1)[-1]
    low = " ".join(_words(text))
    for cat, pattern in _RULES:
        m = re.search(pattern, low)
        if m:
            return json.dumps({"category": cat, "quote": m.group(0)})
    return json.dumps({"category": "other", "quote": ""})
