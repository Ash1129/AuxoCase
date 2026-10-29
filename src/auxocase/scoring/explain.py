"""Why a lead is ranked where it is.

For the logistic-regression model the contribution of each feature to the
log-odds is exact: coefficient x (standardised value), summed over a
categorical's one-hot columns. For the tree challenger we fall back to a
permutation-style local estimate on the same columns, which is slower and
approximate, which is one more reason LR is the default (L08).

Reasons are criteria and direction in fixed wording, not model internals
(L08, regulator guidance in L11): the top three positive drivers, the single
largest negative one, and one counterfactual line where the gap to the
capacity line is small. Names, emails and note text never appear here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from .features import CATEGORICAL, FEATURES, driver_label, frame, reason_clause

REASON_WORDING = "2"     # part of a score's input hash, so a change of wording is written back on the next scoring
from .model import DemoModel


@dataclass
class Explanation:
    contributions: list[tuple[str, float]]          # (label key, signed log-odds contribution), sorted by |value|
    positives: list[str] = field(default_factory=list)
    negatives: list[str] = field(default_factory=list)
    counterfactual: str = ""

    def reason_text(self) -> str:
        parts = []
        if self.positives:
            parts.append("Ranked up because " + _join(self.positives) + ".")
        if self.negatives:
            parts.append("Held back because " + _join(self.negatives) + ".")
        if self.counterfactual:
            parts.append(self.counterfactual)
        return " ".join(parts)


def _join(clauses: list[str]) -> str:
    """"a", "a and b", "a, b and c"."""
    return clauses[0] if len(clauses) == 1 else ", ".join(clauses[:-1]) + " and " + clauses[-1]


def driver_rows(contributions: list, limit: int = 6, bar_width: int = 136) -> list[dict]:
    """The score breakdown as the pages draw it: a label, which way it moved the score, the signed effect on the
    log-odds, and a bar scaled to the largest effect shown."""
    items = [(str(k), float(v)) for k, v in (contributions or [])][:limit]
    biggest = max((abs(v) for _, v in items), default=0.0) or 1.0
    return [{"label": driver_label(k, v), "better": v > 0, "word": "better" if v > 0 else "worse",
             "effect": f"{'+' if v > 0 else '−'}{abs(v):.2f}", "bar": max(2, round(bar_width * abs(v) / biggest))}
            for k, v in items]


def _feature_columns(model: DemoModel) -> list[str]:
    pre = model.pipeline.named_steps["pre"]
    return list(pre.get_feature_names_out())


def _lr_contributions(model: DemoModel, row: dict) -> dict[str, float]:
    pre = model.pipeline.named_steps["pre"]
    clf = model.pipeline.named_steps["clf"]
    X = frame([row])
    Z = pre.transform(X)
    Z = Z.toarray() if hasattr(Z, "toarray") else Z
    cols = _feature_columns(model)
    coefs = clf.coef_[0]
    out: dict[str, float] = {}
    for name, z, c in zip(cols, Z[0], coefs):
        kind, raw = name.split("__", 1)
        if kind == "cat":
            # OneHotEncoder names columns like "channel_referral" (or "channel_infrequent_sklearn")
            feat = next((f for f in CATEGORICAL if raw.startswith(f + "_")), raw)
            val = raw[len(feat) + 1:]
            key = f"{feat}={val}"
        else:
            key = raw
        out[key] = out.get(key, 0.0) + float(z * c)
    return out


def _tree_contributions(model: DemoModel, row: dict) -> dict[str, float]:
    """Local, approximate: change one feature at a time to its neutral value and read the shift in log-odds."""
    base = frame([row])
    p0 = float(model.pipeline.predict_proba(base)[:, 1][0])
    l0 = np.log(p0 / (1 - p0 + 1e-9) + 1e-9)
    out: dict[str, float] = {}
    for f in FEATURES:
        alt = dict(row)
        alt[f] = "unknown" if f in CATEGORICAL else 0
        p1 = float(model.pipeline.predict_proba(frame([alt]))[:, 1][0])
        l1 = np.log(p1 / (1 - p1 + 1e-9) + 1e-9)
        key = f"{f}={row[f]}" if f in CATEGORICAL else f
        out[key] = float(l0 - l1)
    return out


def explain(model: DemoModel, row: dict, probability: float, cutoff_probability: Optional[float] = None) -> Explanation:
    contrib = _lr_contributions(model, row) if model.family == "lr" else _tree_contributions(model, row)
    # Drop features that are "off" (value 0 contributes nothing meaningful to a rep).
    items = [(k, v) for k, v in contrib.items() if abs(v) > 0.05 and _is_on(row, k)]
    items.sort(key=lambda kv: -abs(kv[1]))
    pos = [reason_clause(k, v) for k, v in items if v > 0][:3]
    neg = [reason_clause(k, v) for k, v in items if v < 0][:1]
    cf = ""
    if cutoff_probability is not None and probability < cutoff_probability:
        cf = _counterfactual(model, row, probability, cutoff_probability)
    return Explanation(contributions=items, positives=pos, negatives=neg, counterfactual=cf)


def _is_on(row: dict, key: str) -> bool:
    if "=" in key:
        f, v = key.split("=", 1)
        return str(row.get(f)) == v
    return bool(row.get(key))


# Actions a rep or marketing can actually take; each is a feature change and a sentence (L08: "what would change the rank").
ACTIONS = [
    ("pricing_page_views", 1, "would move up if the contact visits the pricing page"),
    ("webinar_attended", 1, "would move up if the contact attends a webinar"),
    ("num_form_submissions", 1, "would move up if the contact submits a demo request form"),
    ("email_clicks_30d", 2, "would move up if the contact engages with the next two emails"),
]


def _counterfactual(model: DemoModel, row: dict, p: float, cutoff: float) -> str:
    for feat, delta, text in ACTIONS:
        alt = dict(row)
        alt[feat] = (alt.get(feat) or 0) + delta
        p1 = float(model.predict_proba(frame([alt]))[0])
        if p1 >= cutoff:
            return f"It {text}."
    return ""
