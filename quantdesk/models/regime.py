"""Market regime detection with a Gaussian mixture on rolling return and volatility."""
from __future__ import annotations

import numpy as np
import pandas as pd


def detect_regimes(close: pd.Series, n_regimes: int = 3, window: int = 21, seed: int = 0) -> pd.DataFrame:
    """Label each day as one of ``n_regimes`` states, named by their average return/volatility.

    Note: fitted on the full sample, so use it for description, not as a backtest feature.
    """
    from sklearn.mixture import GaussianMixture

    r = np.log(close).diff()
    feats = pd.DataFrame({"ret": r.rolling(window).mean() * 252, "vol": r.rolling(window).std() * np.sqrt(252)}).dropna()
    if len(feats) < n_regimes * 30:
        return pd.DataFrame(index=close.index, columns=["regime", "label"])
    z = (feats - feats.mean()) / feats.std()
    gm = GaussianMixture(n_components=n_regimes, covariance_type="full", random_state=seed, n_init=3).fit(z)
    states = gm.predict(z)
    stats = feats.groupby(states).mean()
    order = stats["ret"].sort_values().index.tolist()
    names = {}
    for rank, st in enumerate(order):
        if rank == 0:
            names[st] = "bear / stress" if stats.loc[st, "vol"] >= stats["vol"].median() else "bear / quiet"
        elif rank == len(order) - 1:
            names[st] = "bull"
        else:
            names[st] = "sideways"
    out = pd.DataFrame({"regime": states, "label": [names[s] for s in states]}, index=feats.index)
    out["prob"] = gm.predict_proba(z).max(axis=1)
    return out
