"""Machine-learning direction classifier with purged walk-forward validation.

The label is whether the forward ``horizon``-day return is positive. Training data for each
test block ends ``horizon`` bars before the block starts ("purging"), so overlapping labels
never leak future information into training. Out-of-sample metrics are reported against the
naive base rate: if accuracy does not beat "always predict up", the model has no edge.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from quantdesk.analytics.indicators import feature_frame

log = logging.getLogger(__name__)


def _make_model(kind: str):
    from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    if kind == "rf":
        return make_pipeline(SimpleImputer(), RandomForestClassifier(n_estimators=300, max_depth=5, min_samples_leaf=20,
                                                                     n_jobs=-1, random_state=0))
    if kind == "logit":
        return make_pipeline(SimpleImputer(), StandardScaler(), LogisticRegression(C=0.1, max_iter=1000))
    return HistGradientBoostingClassifier(max_depth=3, learning_rate=0.05, max_iter=200, min_samples_leaf=40,
                                          l2_regularization=1.0, random_state=0)


def build_dataset(df: pd.DataFrame, horizon: int = 5) -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    X = feature_frame(df)
    fwd = df["close"].shift(-horizon) / df["close"] - 1
    y = (fwd > 0).astype(float).where(fwd.notna())
    return X, y, fwd


@dataclass
class MLResult:
    model_kind: str
    horizon: int
    prob_up: float                     # latest out-of-sample probability of an up move
    oos_accuracy: float
    oos_auc: float
    base_rate: float                   # fraction of up-moves in OOS period (naive benchmark)
    oos_brier: float
    n_oos: int
    oos_proba: pd.Series = field(default_factory=pd.Series)
    importances: dict = field(default_factory=dict)

    @property
    def edge(self) -> float:
        return self.oos_accuracy - max(self.base_rate, 1 - self.base_rate)

    def to_dict(self) -> dict:
        return {"model": self.model_kind, "horizon": self.horizon, "prob_up": self.prob_up,
                "oos_accuracy": self.oos_accuracy, "oos_auc": self.oos_auc, "base_rate": self.base_rate,
                "edge_vs_naive": self.edge, "oos_brier": self.oos_brier, "n_oos": self.n_oos}


def walk_forward_proba(df: pd.DataFrame, horizon: int = 5, kind: str = "hgb", min_train: int = 500,
                       step: int = 63) -> pd.Series:
    """Out-of-sample P(up) for every bar after ``min_train``, refitting every ``step`` bars."""
    X, y, _ = build_dataset(df, horizon)
    proba = pd.Series(np.nan, index=df.index)
    n = len(df)
    for start in range(min_train, n, step):
        train_end = start - horizon  # purge overlapping labels
        tr = y.iloc[:train_end].dropna().index
        if len(tr) < 200 or y.loc[tr].nunique() < 2:
            continue
        model = _make_model(kind)
        model.fit(X.loc[tr], y.loc[tr])
        block = X.iloc[start:start + step]
        proba.iloc[start:start + len(block)] = model.predict_proba(block)[:, 1]
    return proba


def evaluate(df: pd.DataFrame, horizon: int = 5, kind: str = "hgb", min_train: int = 500, step: int = 63) -> MLResult:
    from sklearn.metrics import accuracy_score, brier_score_loss, roc_auc_score

    X, y, _ = build_dataset(df, horizon)
    proba = walk_forward_proba(df, horizon, kind, min_train, step)
    mask = proba.notna() & y.notna()
    if mask.sum() < 50:
        raise ValueError("Not enough history for walk-forward evaluation (need ~2.5+ years of daily data).")
    yt, pt = y[mask], proba[mask]
    acc = accuracy_score(yt, pt > 0.5)
    try:
        auc = roc_auc_score(yt, pt)
    except ValueError:
        auc = float("nan")

    # Final model on all labelled data -> probability for the latest bar.
    tr = y.dropna().index
    model = _make_model(kind)
    model.fit(X.loc[tr], y.loc[tr])
    latest = float(model.predict_proba(X.iloc[[-1]])[:, 1][0])
    importances = {}
    est = model[-1] if hasattr(model, "__getitem__") else model
    if hasattr(est, "feature_importances_"):
        importances = dict(sorted(zip(X.columns, est.feature_importances_), key=lambda kv: -kv[1])[:10])
    elif hasattr(est, "coef_"):
        importances = dict(sorted(zip(X.columns, np.abs(est.coef_[0])), key=lambda kv: -kv[1])[:10])
    return MLResult(kind, horizon, latest, float(acc), float(auc), float(yt.mean()),
                    float(brier_score_loss(yt, pt)), int(mask.sum()), proba, importances)
