"""The one sentence under a candidate's sign-off: approve or send back, and why, for a marketing manager.

The rules decide, the reader only words it. Code compares a candidate with the
live model on three things a manager can feel: do its top picks book more demos
than following HubSpot's list would (lift over HubSpot's points at K), can the
number on a lead be believed (calibration error no worse than the live model's,
within a point), and did it learn from at least as many worked leads (labelled
rows). A candidate whose numbers cannot be believed is a send-back whatever its
ranking; one that is no better on picks or history is not worth reshuffling
every queue for; the rest are approvals.

The facts handed to the reader are already in plain quantities: percent more
demos than HubSpot's list, what really happens when the model says N percent
(its biggest score band: the odds it printed and the share that booked), the
points its numbers are off by, and the worked leads it learned from. The reader
puts the rules' reasons into one sentence, and code checks it the way it checks
the signal and flag readers: one sentence, under 45 words, it names the verdict,
it uses no modelling word, and every figure in it is one of the facts. A
sentence that fails any check is dropped for the fixed wording.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Optional

from .client import LLM

ECE_MARGIN = 0.01          # a candidate may be worse-calibrated than the live model by a point and still pass
LIFT_MARGIN = 0.005        # within this of the live lift is a tie, not a gain
MAX_WORDS = 60            # the reader is asked for 40; the rules' own wording runs to about 50

WORDS = {"approve": "Approve", "send_back": "Send back"}
# words a marketing manager should never meet in the line; a sentence with one is dropped for the fixed wording
JARGON = ("lift", "calibrat", "ece", "precision", "model", "labelled", "rows", "capacity", "metric", "auc", "at k", "× ")

SYSTEM = """You write one sentence for a marketing manager, under a lead-scoring sign-off form. They know campaigns, leads and demos; they do not know statistics or machine learning.
The decision has already been made by fixed rules; you only explain it. Rules:
- Start with the verdict word given: "Approve" or "Send back".
- Say why in the reasons' own terms, in the order given; do not add a reason the rules did not give or soften one they did.
- Use only the figures in the facts, written as given. Never compute a new figure.
- Plain words only, like these: "its top picks book 23 percent more demos than HubSpot's list would", "when it says a lead is 45 percent, about 59 percent of those leads actually book, so the number on every lead would be off by about 14 points", "it learned from 5,595 worked leads".
- Never use these words: lift, calibration, error rate, precision, model, labelled, rows, metric, capacity. Say "demo-v6" and "demo-v1", not "the model".
- One sentence, at most 40 words. Output the sentence only, no quotes, no preamble."""


@dataclass
class Facts:
    version: str
    live: str
    more_demos: Optional[int]             # percent more demos than HubSpot's list, from lift over HubSpot's points at K
    live_more_demos: Optional[int]
    says: Optional[int]                   # the biggest score band: the odds it printed, whole percent
    books: Optional[int]                  # and the share of those leads that booked
    live_says: Optional[int]
    live_books: Optional[int]
    off: Optional[int]                    # calibration error in whole points, what "the number on every lead is off by"
    live_off: Optional[int]
    leads: Optional[int]                  # worked leads it learned from (labelled rows)
    live_leads: Optional[int]
    lift: Optional[float] = None          # the raw figures the rules decide on
    live_lift: Optional[float] = None
    ece: Optional[float] = None
    live_ece: Optional[float] = None

    def to_dict(self) -> dict:
        return self.__dict__.copy()

    def plain(self) -> dict:
        """The facts the reader sees: the plain quantities only."""
        return {k: v for k, v in self.__dict__.items() if k not in ("lift", "live_lift", "ece", "live_ece")}


def _typical(metrics: dict) -> tuple[Optional[int], Optional[int]]:
    bins = metrics.get("calibration_bins") or []
    if not bins:
        return None, None
    b = max(bins, key=lambda x: x.get("n", 0))
    return _whole(100 * b["predicted"]), _whole(100 * b["observed"])


def _whole(x: float) -> int:
    """Half up, so 44.5 reads 45 the way a person rounds it."""
    return int(x + 0.5) if x >= 0 else -int(-x + 0.5)


def _pct_more(lift: Optional[float]) -> Optional[int]:
    return None if lift is None else _whole(100 * (lift - 1))


def _points(ece: Optional[float]) -> Optional[int]:
    return None if ece is None else _whole(100 * ece)


def facts_for(version: str, metrics: dict, live_version: str, live_metrics: dict) -> Facts:
    says, books = _typical(metrics)
    lsays, lbooks = _typical(live_metrics)
    return Facts(version=version, live=live_version,
                 more_demos=_pct_more(metrics.get("lift_at_k")), live_more_demos=_pct_more(live_metrics.get("lift_at_k")),
                 says=says, books=books, live_says=lsays, live_books=lbooks,
                 off=_points(metrics.get("ece")), live_off=_points(live_metrics.get("ece")),
                 leads=metrics.get("rows"), live_leads=live_metrics.get("rows"),
                 lift=metrics.get("lift_at_k"), live_lift=live_metrics.get("lift_at_k"),
                 ece=metrics.get("ece"), live_ece=live_metrics.get("ece"))


def _n(v: Optional[int]) -> str:
    return "[NOT RECORDED]" if v is None else f"{v:,}"


def _picks(f: Facts, word: str) -> str:
    return f"its top picks book {f.more_demos} percent more demos than HubSpot's list would, {word} {f.live}'s {f.live_more_demos} percent"


def _honest(f: Facts) -> str:
    if f.says is not None and f.books is not None:
        return f"when it says a lead is {f.says} percent, about {f.books} percent of those leads book, so the number on a lead can be believed"
    return f"the number on a lead can be believed, off by {f.off} points against {f.live}'s {f.live_off}"


def _dishonest(f: Facts) -> str:
    if f.says is not None and f.books is not None:
        return (f"when it says a lead is {f.says} percent, about {f.books} percent of those leads actually book, so the number on every "
                f"lead would be off by about {f.off} points where {f.live}'s is off by {f.live_off}")
    return f"the number on every lead would be off by about {f.off} points where {f.live}'s is off by {f.live_off}"


def rules(f: Facts) -> tuple[str, list[str]]:
    """The verdict and its reasons, each reason a fixed phrase in plain words with the figures it rests on."""
    if f.ece is None or f.lift is None or f.leads is None:
        return "send_back", ["its test results were not recorded, so it cannot be judged"]
    cal_ok = f.live_ece is not None and f.ece <= f.live_ece + ECE_MARGIN
    lift_better = f.live_lift is not None and f.lift - f.live_lift > LIFT_MARGIN
    lift_worse = f.live_lift is not None and f.live_lift - f.lift > LIFT_MARGIN
    leads_ok = f.live_leads is not None and f.leads >= f.live_leads
    reasons: list[str] = []
    if not cal_ok:
        reasons.append(_dishonest(f))
        if lift_better:
            reasons.append(f"that outweighs {_picks(f, 'against')}")
        return "send_back", reasons
    if lift_better and leads_ok:
        return "approve", [_picks(f, "where"), _honest(f), f"it learned from more worked leads, {_n(f.leads)} against {_n(f.live_leads)}"]
    if lift_worse:
        reasons.append(f"its top picks book fewer demos than {f.live}'s, {f.more_demos} percent more than HubSpot's list would against {f.live_more_demos} percent")
    elif not lift_better:
        reasons.append(f"its top picks book no more demos than {f.live}'s, {f.more_demos} percent more than HubSpot's list would against {f.live_more_demos} percent, "
                       "so a change would reshuffle every queue for nothing")
    if not leads_ok:
        reasons.append(f"it learned from fewer worked leads, {_n(f.leads)} against {_n(f.live_leads)}")
    return "send_back", reasons


def sentence_for(verdict: str, reasons: list[str]) -> str:
    """The fixed wording: the verdict word, then the reasons joined into one sentence."""
    body = reasons[0] if len(reasons) == 1 else "; ".join(reasons[:-1]) + "; and " + reasons[-1]
    return f"{WORDS[verdict]}: {body}."


_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def numbers_in(text: str) -> set[str]:
    """The figures in a text, with thousands commas and trailing decimal zeros dropped, so 1.260 is the fact 1.26."""
    out = set()
    for m in _NUM.findall(text):
        m = m.replace(",", "")
        if "." in m:
            m = m.rstrip("0").rstrip(".")
        out.add(m)
    return out


def check(text: str, verdict: str, f: Facts) -> Optional[str]:
    """One short sentence, the verdict word first, no modelling word, and no figure the plain facts do not hold; else None."""
    s = " ".join((text or "").strip().strip('"').split())
    if not s or len(s.split()) > MAX_WORDS:
        return None
    if not s.lower().startswith(WORDS[verdict].lower()):
        return None
    low = s.lower() + " "
    if "_" in s or any(j in low for j in JARGON):
        return None
    allowed = numbers_in(" ".join(str(v) for v in f.plain().values() if v is not None))
    if not numbers_in(s) <= allowed:
        return None
    return s if s.endswith(".") else s + "."


def read_verdict(llm: LLM, verdict: str, reasons: list[str], f: Facts) -> tuple[Optional[str], str]:
    """The reader's sentence if it passes the check, else None; and the error if the reader failed."""
    user = f"TASK: verdict\nVERDICT: {WORDS[verdict]}\nREASONS: {json.dumps(reasons)}\nFACTS: {json.dumps(f.plain())}"
    try:
        raw = llm.complete(SYSTEM, user, max_tokens=120)
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"[:200]
    return check(raw, verdict, f), ""


def offline_verdict(user: str) -> str:
    """The stand-in: the rules' wording for the verdict and reasons in the prompt."""
    try:
        verdict = user.split("VERDICT: ", 1)[1].split("\n", 1)[0].strip()
        reasons = json.loads(user.split("REASONS: ", 1)[1].split("\n", 1)[0])
    except Exception:
        return ""
    key = next((k for k, w in WORDS.items() if w == verdict), None)
    return sentence_for(key, reasons) if key else ""
