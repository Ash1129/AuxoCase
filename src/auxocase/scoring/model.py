"""Train, evaluate, persist and apply the demo-booking model.

Design, each point traceable to the knowledge base:

- Two candidates on the same feature matrix: regularised logistic regression
  and a gradient-boosted tree. The tree wins only if it beats LR on time-split
  precision@500 and stays calibrated (L02, L04). Logistic regression is the
  default because its contributions are exact, which the rep-facing reasons
  need (L08).
- Time-ordered three-way split (L03): fit on the oldest months, choose the
  family and fit the calibrator on the next two, and report the newest two
  once. The model that goes live is the one measured; it is not refitted.
- Metrics: precision@K and lift@K over the points model at the SDR capacity
  K=500 per month, AUC-PR with its baseline printed beside it, Brier score and
  expected calibration error; never accuracy (L03, L04).
- Labels are three-state: booked / worked-no-demo / not-worked. Not-worked is
  unlabelled and never a negative (L05). Leads younger than the demo maturity
  window are excluded, not treated as negatives (L09).
- A Platt calibrator fitted on the calibration months maps the ranking score to
  a probability; the calibrator version travels with the model version (L04).
- The close model is trained only on post-demo rows and reported separately;
  closed-won is not the demo model's target (L13).
"""
from __future__ import annotations

import json
import pickle
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from .features import CATEGORICAL, FEATURES, NUMERIC, frame

CAPACITY_PER_MONTH = 500          # the brief: 12 SDRs work about 500 MQLs a month
DEMO_MATURITY_DAYS = 30           # L09: demo booked is observable within ~30 days of MQL
CLOSE_MATURITY_DAYS = 45          # brief answer 5: a 30-day business cycle for deals, with slack
MIN_POSITIVES = 60                # below this, keep the points model (cold start)


@dataclass
class TrainingTable:
    """One row per MQL with its label state at `as_of`.

    label_state: 'booked' | 'worked_no_demo' | 'not_worked'
    closed: 1/0/None for the close model (None where no demo or immature)
    """
    X: pd.DataFrame
    label_state: pd.Series
    created: pd.Series
    points: pd.Series            # the service's own channel-aware cold-start table
    hubspot_score: pd.Series     # the incumbent: HubSpot's points model, the brief's baseline
    closed: pd.Series
    contact_ids: pd.Series
    holdout: pd.Series


@dataclass
class Metrics:
    rows: int
    positives: int
    base_rate: float
    precision_at_k: float
    hubspot_precision_at_k: float   # HubSpot's current points model at the same K
    points_precision_at_k: float   # the service's cold-start table at the same K
    lift_at_k: float               # model over HubSpot's points model: the number the brief asks about
    lift_over_points: float
    auc_pr: float
    auc_roc: float
    brier: float
    ece: float
    k: int
    test_months: list[str]                 # the newest months, untouched until the model was chosen and calibrated
    calibration_bins: list[dict] = field(default_factory=list)
    by_channel: dict = field(default_factory=dict)
    challenger: dict = field(default_factory=dict)
    calibration_months: list[str] = field(default_factory=list)   # where the family was chosen and Platt fitted
    train_months: list[str] = field(default_factory=list)
    held_out: bool = True                  # False only when history is too short to keep a test month apart
    train_rows: int = 0
    calibration_rows: int = 0
    test_rows: int = 0

    def to_dict(self) -> dict:
        return self.__dict__


def _pipeline(family: str) -> Pipeline:
    pre = ColumnTransformer([
        ("cat", OneHotEncoder(handle_unknown="ignore", min_frequency=20), CATEGORICAL),
        ("num", StandardScaler(), NUMERIC),
    ])
    if family == "lr":
        clf = LogisticRegression(C=0.5, max_iter=2000, class_weight=None)
    elif family == "hgb":
        clf = HistGradientBoostingClassifier(max_depth=4, learning_rate=0.06, max_iter=300, l2_regularization=1.0)
    else:
        raise ValueError(family)
    return Pipeline([("pre", pre), ("clf", clf)])


def _platt(scores: np.ndarray, y: np.ndarray) -> Optional[LogisticRegression]:
    """Two-parameter sigmoid map from the raw model output to a probability (L04)."""
    if len(set(np.asarray(y).tolist())) < 2:   # nothing to calibrate against yet: pass the raw output through
        return None
    logit = np.log(np.clip(scores, 1e-6, 1 - 1e-6) / np.clip(1 - scores, 1e-6, 1))
    cal = LogisticRegression(C=1e6, max_iter=1000)
    cal.fit(logit.reshape(-1, 1), y)
    return cal


def _apply_platt(cal: Optional[LogisticRegression], scores: np.ndarray) -> np.ndarray:
    if cal is None:
        return np.asarray(scores, dtype=float)
    logit = np.log(np.clip(scores, 1e-6, 1 - 1e-6) / np.clip(1 - scores, 1e-6, 1))
    return cal.predict_proba(logit.reshape(-1, 1))[:, 1]


def precision_at_k(y: np.ndarray, s: np.ndarray, k: int) -> float:
    k = min(k, len(s))
    if k == 0:
        return float("nan")
    idx = np.argsort(-s, kind="stable")[:k]
    return float(y[idx].mean())


def expected_calibration_error(y: np.ndarray, p: np.ndarray, bins: int = 10) -> tuple[float, list[dict]]:
    edges = np.linspace(0, 1, bins + 1)
    ece, out = 0.0, []
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (p >= lo) & (p < hi) if hi < 1 else (p >= lo) & (p <= hi)
        if m.sum() == 0:
            continue
        conf, acc = float(p[m].mean()), float(y[m].mean())
        ece += m.mean() * abs(conf - acc)
        out.append({"lo": round(lo, 2), "hi": round(hi, 2), "n": int(m.sum()), "predicted": round(conf, 3), "observed": round(acc, 3)})
    return float(ece), out


@dataclass
class DemoModel:
    version: str
    family: str
    pipeline: Pipeline
    calibrator: LogisticRegression
    feature_names: list[str]
    trained_at: datetime
    metrics: Metrics
    close_model: Optional[Pipeline] = None
    close_calibrator: Optional[LogisticRegression] = None
    close_base_rate: float = 0.2

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        raw = self.pipeline.predict_proba(X[FEATURES])[:, 1]
        return _apply_platt(self.calibrator, raw)

    def predict_close_proba(self, X: pd.DataFrame) -> np.ndarray:
        if self.close_model is None:
            return np.full(len(X), self.close_base_rate)
        raw = self.close_model.predict_proba(X[FEATURES])[:, 1]
        return _apply_platt(self.close_calibrator, raw) if self.close_calibrator is not None else raw

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump(self, f)

    @staticmethod
    def load(path: Path) -> "DemoModel":
        with open(path, "rb") as f:
            return pickle.load(f)


def _labelled(table: TrainingTable, as_of: datetime) -> np.ndarray:
    """Rows that can be a 0 or a 1: worked leads (or holdout leads) old enough for the demo label to have matured."""
    mature = table.created <= (as_of - timedelta(days=DEMO_MATURITY_DAYS))
    labelled = table.label_state.isin(["booked", "worked_no_demo"]) | table.holdout.astype(bool)
    return (mature & labelled).to_numpy()


def split_months(months: list, test_months: int = 2, calibration_months: int = 2) -> tuple[list, list, list, bool]:
    """(train, calibration, test) month lists, oldest first, and whether the test months are truly held out.

    The test months are the newest, the calibration months just before them, the rest train the model. With
    too little history the windows shrink to one month each; with under three months the test month is also
    the calibration month and is reported as not held out."""
    months = sorted(months)
    if len(months) >= test_months + calibration_months + 2:
        test, cal = months[-test_months:], months[-(test_months + calibration_months):-test_months]
    elif len(months) >= 3:
        test, cal = months[-1:], months[-2:-1]
    else:
        return months, months[-1:], months[-1:], False
    return [m for m in months if m not in test and m not in cal], cal, test, True


def train(table: TrainingTable, as_of: datetime, version: str, test_months: int = 2, calibration_months: int = 2,
          capacity: int = CAPACITY_PER_MONTH, prefer: Optional[str] = None) -> DemoModel:
    """Fit on the oldest months, choose the family and calibrate on the next, report the newest once.

    The model that goes live is exactly the one measured: no refit after the test, so its reported numbers
    describe it, and the calibrator matches the pipeline it was fitted on. The cost is that the newest four
    months teach the next model, not this one."""
    mask = _labelled(table, as_of)
    X = table.X[mask].reset_index(drop=True)
    y = (table.label_state[mask] == "booked").astype(int).to_numpy()
    created = pd.to_datetime(table.created[mask]).reset_index(drop=True)
    points = table.points[mask].to_numpy()
    hub = table.hubspot_score[mask].to_numpy()
    if y.sum() < MIN_POSITIVES:
        raise ValueError(f"only {int(y.sum())} positives; keep the points model until there are at least {MIN_POSITIVES}")

    months = created.dt.to_period("M")
    train_p, cal_p, test_p, held_out = split_months(sorted(months.unique()), test_months, calibration_months)
    tr, cal, te = (months.isin(train_p).to_numpy(), months.isin(cal_p).to_numpy(), months.isin(test_p).to_numpy())
    if y[tr].sum() < MIN_POSITIVES // 2:
        raise ValueError(f"only {int(y[tr].sum())} positives in the training months; keep the points model for now")

    # K is the capacity's share of the whole pool in a window's months (worked or not), applied to the labelled
    # rows we can score: 500 of 1,500 MQLs is the top third, so K is a third of the labelled rows (L03).
    all_months = pd.to_datetime(table.created).dt.to_period("M")

    def k_for(window: np.ndarray, periods: list) -> int:
        pool = int(all_months.isin(periods).sum())
        k = int(round(window.sum() * min(1.0, capacity * len(periods) / max(1, pool))))
        return max(50, min(k, int(window.sum())))

    k_cal, k_test = k_for(cal, cal_p), k_for(te, test_p)

    def measure(p: np.ndarray, w: np.ndarray, k: int) -> tuple[float, float]:
        return precision_at_k(y[w], p, k), expected_calibration_error(y[w], p)[0]

    fitted: dict[str, tuple[Pipeline, Optional[LogisticRegression]]] = {}
    on_cal: dict[str, tuple[float, float]] = {}
    for family in ("lr", "hgb"):
        pipe = _pipeline(family)
        pipe.fit(X[tr], y[tr])
        raw_cal = pipe.predict_proba(X[cal])[:, 1]
        calibrator = _platt(raw_cal, y[cal])
        fitted[family] = (pipe, calibrator)
        on_cal[family] = measure(_apply_platt(calibrator, raw_cal), cal, k_cal)

    # L02/L04: the tree must beat LR on precision@K by a margin and not be worse calibrated, judged on the
    # calibration months; the test months are not looked at until the choice is made.
    (lr_pk, lr_ece), (hgb_pk, hgb_ece) = on_cal["lr"], on_cal["hgb"]
    family = "hgb" if (hgb_pk > lr_pk * 1.03 and hgb_ece <= lr_ece + 0.01) else "lr"
    if prefer in fitted:
        family = prefer

    def report(fam: str) -> Metrics:
        pipe, calibrator = fitted[fam]
        p = _apply_platt(calibrator, pipe.predict_proba(X[te])[:, 1])
        yt = y[te]
        ece, bins = expected_calibration_error(yt, p)
        pk = precision_at_k(yt, p, k_test)
        ppk = precision_at_k(yt, points[te].astype(float), k_test)
        hpk = precision_at_k(yt, hub[te].astype(float), k_test)
        ch = X.loc[te, "channel"].to_numpy()
        by_channel = {c: {"n": int((ch == c).sum()), "observed": round(float(yt[ch == c].mean()), 3),
                          "predicted": round(float(p[ch == c].mean()), 3)} for c in sorted(set(ch))}
        return Metrics(
            rows=int(mask.sum()), positives=int(y.sum()), base_rate=round(float(yt.mean()), 4),
            precision_at_k=round(pk, 4), hubspot_precision_at_k=round(hpk, 4), points_precision_at_k=round(ppk, 4),
            lift_at_k=round(pk / hpk, 3) if hpk > 0 else float("nan"),
            lift_over_points=round(pk / ppk, 3) if ppk > 0 else float("nan"),
            auc_pr=round(float(average_precision_score(yt, p)), 4),
            auc_roc=round(float(roc_auc_score(yt, p)), 4) if len(set(yt)) > 1 else float("nan"),
            brier=round(float(brier_score_loss(yt, p)), 4), ece=round(ece, 4), k=k_test,
            test_months=[str(m) for m in test_p], calibration_bins=bins, by_channel=by_channel,
            calibration_months=[str(m) for m in cal_p], train_months=[str(m) for m in train_p], held_out=held_out,
            train_rows=int(tr.sum()), calibration_rows=int(cal.sum()), test_rows=int(te.sum()),
        )

    metrics = report(family)
    other = "hgb" if family == "lr" else "lr"
    o = report(other)
    metrics.challenger = {"family": other, "precision_at_k": o.precision_at_k, "ece": o.ece, "auc_pr": o.auc_pr,
                          "chosen_on": "calibration months", "calibration_precision_at_k": {f: round(v[0], 4) for f, v in on_cal.items()}}

    pipe, calibrator = fitted[family]
    close_model, close_cal, close_rate = _train_close(table, as_of)
    return DemoModel(version=version, family=family, pipeline=pipe, calibrator=calibrator, feature_names=list(FEATURES),
                     trained_at=as_of, metrics=metrics, close_model=close_model, close_calibrator=close_cal,
                     close_base_rate=close_rate)


def _train_close(table: TrainingTable, as_of: datetime):
    """p(close | demo) on post-demo rows only, with the longer maturity window (L13, L09)."""
    mature = table.created <= (as_of - timedelta(days=CLOSE_MATURITY_DAYS))
    m = (table.label_state == "booked") & mature & table.closed.notna()
    if m.sum() < 80 or table.closed[m].sum() < 15:
        rate = float(table.closed[m].mean()) if m.sum() else 0.2
        return None, None, rate
    X = table.X[m].reset_index(drop=True)
    y = table.closed[m].astype(int).to_numpy()
    pipe = _pipeline("lr")
    pipe.fit(X, y)
    raw = pipe.predict_proba(X)[:, 1]
    return pipe, _platt(raw, y), float(y.mean())


def score_frame(model: DemoModel, rows: list[dict]) -> tuple[np.ndarray, np.ndarray]:
    X = frame(rows)
    return model.predict_proba(X), model.predict_close_proba(X)
