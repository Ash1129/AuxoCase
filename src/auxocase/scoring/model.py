"""Train, evaluate, persist and apply the demo-booking model.

Design, each point traceable to the knowledge base:

- Two candidates on the same feature matrix: regularised logistic regression
  and a gradient-boosted tree. The tree wins only if it beats LR on time-split
  precision@500 and stays calibrated (L02, L04). Logistic regression is the
  default because its contributions are exact, which the rep-facing reasons
  need (L08).
- Time-ordered split: train on earlier months, validate on the next (L03).
- Metrics: precision@K and lift@K over the points model at the SDR capacity
  K=500 per month, AUC-PR with its baseline printed beside it, Brier score and
  expected calibration error; never accuracy (L03, L04).
- Labels are three-state: booked / worked-no-demo / not-worked. Not-worked is
  unlabelled and never a negative (L05). Leads younger than the demo maturity
  window are excluded, not treated as negatives (L09).
- A Platt calibrator fitted on the validation window maps the ranking score to
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
CLOSE_MATURITY_DAYS = 120         # the generator closes 30-120 days after the demo
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
    validation_months: list[str]
    calibration_bins: list[dict] = field(default_factory=list)
    by_channel: dict = field(default_factory=dict)
    challenger: dict = field(default_factory=dict)

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


def train(table: TrainingTable, as_of: datetime, version: str, validation_months: int = 2,
          capacity: int = CAPACITY_PER_MONTH, prefer: Optional[str] = None) -> DemoModel:
    mask = _labelled(table, as_of)
    X = table.X[mask].reset_index(drop=True)
    y = (table.label_state[mask] == "booked").astype(int).to_numpy()
    created = pd.to_datetime(table.created[mask]).reset_index(drop=True)
    points = table.points[mask].to_numpy()
    hub = table.hubspot_score[mask].to_numpy()
    if y.sum() < MIN_POSITIVES:
        raise ValueError(f"only {int(y.sum())} positives; keep the points model until there are at least {MIN_POSITIVES}")

    months = created.dt.to_period("M")
    uniq = sorted(months.unique())
    val_periods = uniq[-validation_months:] if len(uniq) > validation_months else uniq[-1:]
    val = months.isin(val_periods).to_numpy()
    tr = ~val
    if y[tr].sum() < MIN_POSITIVES // 2:
        tr = np.ones(len(y), bool)

    # K is the capacity's share of the whole pool in the validation months (worked or not), applied to the
    # labelled rows we can score: 500 of 1,500 MQLs is the top third, so K is a third of the labelled rows (L03).
    all_months = pd.to_datetime(table.created).dt.to_period("M")
    pool_val = int(all_months.isin(val_periods).sum())
    k_val = int(round(val.sum() * min(1.0, capacity * len(val_periods) / max(1, pool_val))))
    k_val = max(50, min(k_val, int(val.sum())))

    candidates: dict[str, tuple[Pipeline, LogisticRegression, Metrics]] = {}
    for family in ("lr", "hgb"):
        pipe = _pipeline(family)
        pipe.fit(X[tr], y[tr])
        raw_val = pipe.predict_proba(X[val])[:, 1]
        cal = _platt(raw_val, y[val])
        p_val = _apply_platt(cal, raw_val)
        ece, bins = expected_calibration_error(y[val], p_val)
        pk = precision_at_k(y[val], p_val, k_val)
        ppk = precision_at_k(y[val], points[val].astype(float), k_val)
        hpk = precision_at_k(y[val], hub[val].astype(float), k_val)
        by_channel = {}
        ch = X.loc[val, "channel"].to_numpy()
        for c in sorted(set(ch)):
            m = ch == c
            by_channel[c] = {"n": int(m.sum()), "observed": round(float(y[val][m].mean()), 3), "predicted": round(float(p_val[m].mean()), 3)}
        metrics = Metrics(
            rows=int(mask.sum()), positives=int(y.sum()), base_rate=round(float(y[val].mean()), 4),
            precision_at_k=round(pk, 4), hubspot_precision_at_k=round(hpk, 4), points_precision_at_k=round(ppk, 4),
            lift_at_k=round(pk / hpk, 3) if hpk > 0 else float("nan"),
            lift_over_points=round(pk / ppk, 3) if ppk > 0 else float("nan"),
            auc_pr=round(float(average_precision_score(y[val], p_val)), 4),
            auc_roc=round(float(roc_auc_score(y[val], p_val)), 4) if len(set(y[val])) > 1 else float("nan"),
            brier=round(float(brier_score_loss(y[val], p_val)), 4), ece=round(ece, 4), k=k_val,
            validation_months=[str(p) for p in val_periods], calibration_bins=bins, by_channel=by_channel,
        )
        candidates[family] = (pipe, cal, metrics)

    lr_m, hgb_m = candidates["lr"][2], candidates["hgb"][2]
    # L02/L04: the tree model must beat LR on precision@K by a margin and not be worse calibrated.
    family = "hgb" if (hgb_m.precision_at_k > lr_m.precision_at_k * 1.03 and hgb_m.ece <= lr_m.ece + 0.01) else "lr"
    if prefer in candidates:
        family = prefer
    pipe, cal, metrics = candidates[family]
    other = "hgb" if family == "lr" else "lr"
    metrics.challenger = {"family": other, "precision_at_k": candidates[other][2].precision_at_k, "ece": candidates[other][2].ece, "auc_pr": candidates[other][2].auc_pr}

    # Refit the chosen pipeline on all labelled rows; keep the validation-window calibrator (L04).
    pipe = _pipeline(family)
    pipe.fit(X, y)

    close_model, close_cal, close_rate = _train_close(table, as_of)
    return DemoModel(version=version, family=family, pipeline=pipe, calibrator=cal, feature_names=list(FEATURES),
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
