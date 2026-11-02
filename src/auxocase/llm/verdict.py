"""The one sentence under a candidate's sign-off: approve or send back, and why, for a reader who does not know ML.

The rules decide, the reader only words it. Code compares a candidate with the live
model on three things a manager can act on: does it pick better leads (lift over
HubSpot's points at K), can its numbers be trusted (calibration error no worse than
the live model's, within a point), and did it learn from at least as much history
(labelled rows). A candidate that fails calibration is a send-back whatever its
ranking; one that fails the other two is not worth the reshuffle; the rest are
approvals. The verdict and every number come from those facts, in fixed wording.

The model is then asked for one plain sentence from the same facts, and code checks
it the way it checks the signal and flag readers: one sentence, under 45 words, it
names the verdict, and every number in it is one of the facts. A sentence that
fails any check is dropped for the fixed wording, so a manager never reads a figure
the table does not hold.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Optional

from .client import LLM

ECE_MARGIN = 0.01          # a candidate may be worse-calibrated than the live model by a point and still pass
LIFT_MARGIN = 0.005        # within this of the live lift is a tie, not a gain
MAX_WORDS = 45

WORDS = {"approve": "Approve", "send_back": "Send back"}

SYSTEM = """You write one sentence for a marketing manager who does not know machine learning, under a lead-scoring model's sign-off form.
The decision has already been made by fixed rules; you only explain it. Rules:
- Start with the verdict word given: "Approve" or "Send back".
- Say why in the reasons' own terms, in the order given; do not add a reason the rules did not give or soften one they did.
- Use only the figures in the facts, written as given. Never compute a new figure.
- Plain words only: "picks better leads", "its numbers are off by N points", "learned from more history (N labelled leads)". Never a field name such as ece_points, lift_at_k or rows.
- One sentence, at most 40 words. Name the live model by its version when comparing.
- Output the sentence only, no quotes, no preamble."""


@dataclass
class Facts:
    version: str
    live: str
    lift: Optional[float]                 # lift over HubSpot's points at K
    live_lift: Optional[float]
    ece_points: Optional[float]           # calibration error in whole points, one decimal
    live_ece_points: Optional[float]
    rows: Optional[int]
    live_rows: Optional[int]

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def facts_for(version: str, metrics: dict, live_version: str, live_metrics: dict) -> Facts:
    def pts(x):
        return None if x is None else round(100 * x, 1)
    return Facts(version=version, live=live_version,
                 lift=metrics.get("lift_at_k"), live_lift=live_metrics.get("lift_at_k"),
                 ece_points=pts(metrics.get("ece")), live_ece_points=pts(live_metrics.get("ece")),
                 rows=metrics.get("rows"), live_rows=live_metrics.get("rows"))


def rules(f: Facts) -> tuple[str, list[str]]:
    """The verdict and its reasons, each reason a fixed phrase with the figures it rests on."""
    reasons: list[str] = []
    cal_ok = f.ece_points is not None and f.live_ece_points is not None and f.ece_points <= f.live_ece_points + 100 * ECE_MARGIN
    lift_better = f.lift is not None and f.live_lift is not None and f.lift - f.live_lift > LIFT_MARGIN
    lift_worse = f.lift is not None and f.live_lift is not None and f.live_lift - f.lift > LIFT_MARGIN
    rows_ok = f.rows is not None and f.live_rows is not None and f.rows >= f.live_rows
    if f.ece_points is None or f.lift is None or f.rows is None:
        return "send_back", ["its test results were not recorded, so it cannot be judged"]
    if not cal_ok:
        reasons.append(f"its numbers cannot be trusted: off by {f.ece_points} points against {f.live}'s {f.live_ece_points}")
        if lift_better:
            reasons.append(f"even though it picks better leads, lift {_x(f.lift)} against {_x(f.live_lift)}")
        return "send_back", reasons
    if lift_better and rows_ok:
        reasons.append(f"it picks better leads, lift {_x(f.lift)} against {f.live}'s {_x(f.live_lift)}")
        reasons.append(f"its numbers can be trusted, off by {f.ece_points} points against {f.live_ece_points}")
        reasons.append(f"it learned from more history, {_n(f.rows)} labelled rows against {_n(f.live_rows)}")
        return "approve", reasons
    if lift_worse:
        reasons.append(f"it picks worse leads, lift {_x(f.lift)} against {f.live}'s {_x(f.live_lift)}")
    elif not lift_better:
        reasons.append(f"it picks leads no better, lift {_x(f.lift)} against {f.live}'s {_x(f.live_lift)}, so the reshuffle buys nothing")
    if not rows_ok:
        reasons.append(f"it learned from less history, {_n(f.rows)} labelled rows against {_n(f.live_rows)}")
    return "send_back", reasons


def _x(v: Optional[float]) -> str:
    return "[NOT RECORDED]" if v is None else f"{v:.3f}×"


def _n(v: Optional[int]) -> str:
    return "[NOT RECORDED]" if v is None else f"{v:,}"


def sentence_for(verdict: str, reasons: list[str]) -> str:
    """The fixed wording: the verdict word, then the reasons joined into one sentence."""
    body = reasons[0] if len(reasons) == 1 else ", ".join(reasons[:-1]) + " and " + reasons[-1]
    return f"{WORDS[verdict]}: {body}."


_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def numbers_in(text: str) -> set[str]:
    """The figures in a text, with thousands commas and trailing decimal zeros dropped, so 1.260× is the fact 1.26."""
    out = set()
    for m in _NUM.findall(text):
        m = m.replace(",", "")
        if "." in m:
            m = m.rstrip("0").rstrip(".")
        out.add(m)
    return out


def check(text: str, verdict: str, f: Facts) -> Optional[str]:
    """One sentence, short, the verdict word first, and no number the facts do not hold; else None."""
    s = " ".join((text or "").strip().strip('"').split())
    if not s or len(s.split()) > MAX_WORDS:
        return None
    if not s.lower().startswith(WORDS[verdict].lower()):
        return None
    if "_" in s:
        return None                                            # a field name, not a sentence for a manager
    allowed = numbers_in(" ".join(str(v) for v in f.to_dict().values() if v is not None))
    allowed |= {str(int(v)) for v in (f.rows, f.live_rows) if v is not None}
    if not numbers_in(s) <= allowed:
        return None
    return s if s.endswith(".") else s + "."


def read_verdict(llm: LLM, verdict: str, reasons: list[str], f: Facts) -> tuple[Optional[str], str]:
    """The reader's sentence if it passes the check, else None; and the error if the reader failed."""
    user = f"TASK: verdict\nVERDICT: {WORDS[verdict]}\nREASONS: {json.dumps(reasons)}\nFACTS: {json.dumps(f.to_dict())}"
    try:
        raw = llm.complete(SYSTEM, user, max_tokens=120)
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"[:200]
    return check(raw, verdict, f), ""


def offline_verdict(user: str) -> str:
    """The stand-in: the fixed wording from the facts in the prompt, as the real reader would phrase it."""
    try:
        f = Facts(**json.loads(user.split("FACTS: ", 1)[1]))
    except Exception:
        return ""
    verdict, reasons = rules(f)
    return sentence_for(verdict, reasons)
