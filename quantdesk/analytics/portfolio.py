"""Portfolio construction: Markowitz, minimum variance, risk parity, HRP, Kelly."""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import leaves_list, linkage
from scipy.optimize import minimize
from scipy.spatial.distance import squareform

TRADING_DAYS = 252


def shrunk_covariance(returns: pd.DataFrame) -> pd.DataFrame:
    """Ledoit-Wolf shrinkage: a far more stable covariance estimate than the sample one."""
    from sklearn.covariance import LedoitWolf

    lw = LedoitWolf().fit(returns.dropna().to_numpy())
    return pd.DataFrame(lw.covariance_ * TRADING_DAYS, index=returns.columns, columns=returns.columns)


def _bounds(n: int, long_only: bool, max_weight: float):
    return [(0.0 if long_only else -max_weight, max_weight)] * n


def max_sharpe(mu: pd.Series, cov: pd.DataFrame, rf: float = 0.065, long_only: bool = True,
               max_weight: float = 1.0) -> pd.Series:
    n = len(mu)

    def neg_sharpe(w):
        ret = w @ mu.to_numpy()
        vol = np.sqrt(w @ cov.to_numpy() @ w)
        return -(ret - rf) / vol if vol > 0 else 1e6

    res = minimize(neg_sharpe, np.full(n, 1 / n), bounds=_bounds(n, long_only, max_weight),
                   constraints=[{"type": "eq", "fun": lambda w: w.sum() - 1}], method="SLSQP")
    return pd.Series(res.x, index=mu.index)


def min_variance(cov: pd.DataFrame, long_only: bool = True, max_weight: float = 1.0) -> pd.Series:
    n = len(cov)
    res = minimize(lambda w: w @ cov.to_numpy() @ w, np.full(n, 1 / n), bounds=_bounds(n, long_only, max_weight),
                   constraints=[{"type": "eq", "fun": lambda w: w.sum() - 1}], method="SLSQP")
    return pd.Series(res.x, index=cov.index)


def risk_parity(cov: pd.DataFrame) -> pd.Series:
    """Equal Risk Contribution: each asset contributes the same share of portfolio variance."""
    c = cov.to_numpy()
    n = len(c)

    def obj(w):
        port_var = w @ c @ w
        rc = w * (c @ w) / port_var
        return np.sum((rc - 1 / n) ** 2)

    res = minimize(obj, np.full(n, 1 / n), bounds=[(1e-6, 1)] * n,
                   constraints=[{"type": "eq", "fun": lambda w: w.sum() - 1}], method="SLSQP")
    return pd.Series(res.x, index=cov.index)


def hrp(returns: pd.DataFrame) -> pd.Series:
    """Hierarchical Risk Parity (Lopez de Prado, 2016): cluster by correlation, then bisect risk."""
    cov = returns.cov().to_numpy()
    corr = returns.corr().to_numpy()
    dist = np.sqrt(np.clip((1 - corr) / 2, 0, 1))
    np.fill_diagonal(dist, 0)
    order = leaves_list(linkage(squareform(dist, checks=False), method="single"))
    w = np.ones(len(order))
    clusters = [list(order)]
    while clusters:
        nxt = []
        for cl in clusters:
            if len(cl) <= 1:
                continue
            half = len(cl) // 2
            left, right = cl[:half], cl[half:]

            def cvar_(idx):
                sub = cov[np.ix_(idx, idx)]
                ivp = 1 / np.diag(sub)
                ivp /= ivp.sum()
                return ivp @ sub @ ivp

            vl, vr = cvar_(left), cvar_(right)
            alpha = 1 - vl / (vl + vr)
            w[left] *= alpha
            w[right] *= 1 - alpha
            nxt += [left, right]
        clusters = nxt
    return pd.Series(w / w.sum(), index=returns.columns)


def efficient_frontier(mu: pd.Series, cov: pd.DataFrame, points: int = 30, long_only: bool = True) -> pd.DataFrame:
    n = len(mu)
    targets = np.linspace(mu.min(), mu.max(), points)
    rows = []
    for t in targets:
        res = minimize(lambda w: w @ cov.to_numpy() @ w, np.full(n, 1 / n), bounds=_bounds(n, long_only, 1.0),
                       constraints=[{"type": "eq", "fun": lambda w: w.sum() - 1},
                                    {"type": "eq", "fun": lambda w, t=t: w @ mu.to_numpy() - t}], method="SLSQP")
        if res.success:
            rows.append({"return": t, "vol": float(np.sqrt(res.fun))})
    return pd.DataFrame(rows)


def portfolio_stats(w: pd.Series, mu: pd.Series, cov: pd.DataFrame, rf: float = 0.065) -> dict:
    ret = float(w @ mu)
    vol = float(np.sqrt(w @ cov @ w))
    rc = w * (cov @ w) / (vol**2) if vol > 0 else w * 0
    return {"expected_return": ret, "volatility": vol, "sharpe": (ret - rf) / vol if vol else float("nan"),
            "risk_contribution": rc.to_dict()}


def kelly_fraction(win_prob: float, win_loss_ratio: float) -> float:
    """Discrete Kelly: f* = p - (1-p)/b. Use a fraction of it (e.g. 1/4) in practice."""
    if win_loss_ratio <= 0:
        return 0.0
    return max(0.0, win_prob - (1 - win_prob) / win_loss_ratio)


def kelly_continuous(mu_annual: float, sigma_annual: float, rf: float = 0.065) -> float:
    """Continuous-time Kelly leverage for a GBM asset: (mu - r) / sigma^2."""
    return (mu_annual - rf) / sigma_annual**2 if sigma_annual > 0 else 0.0
