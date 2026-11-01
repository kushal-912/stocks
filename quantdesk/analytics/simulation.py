"""Monte Carlo: GBM paths, block bootstrap of history, and outcome distributions."""
from __future__ import annotations

import numpy as np
import pandas as pd


def gbm_paths(s0: float, mu: float, sigma: float, days: int, n_paths: int = 1000,
              seed: int | None = None, dt: float = 1 / 252) -> np.ndarray:
    """Geometric Brownian Motion: dS = mu S dt + sigma S dW. Returns array (days+1, n_paths)."""
    rng = np.random.default_rng(seed)
    z = rng.standard_normal((days, n_paths))
    steps = (mu - 0.5 * sigma**2) * dt + sigma * np.sqrt(dt) * z
    log_paths = np.vstack([np.zeros(n_paths), np.cumsum(steps, axis=0)])
    return s0 * np.exp(log_paths)


def bootstrap_paths(returns: pd.Series, s0: float, days: int, n_paths: int = 1000,
                    block: int = 10, seed: int | None = None) -> np.ndarray:
    """Stationary-ish block bootstrap: resamples blocks of real returns to keep volatility clustering and fat tails."""
    rng = np.random.default_rng(seed)
    r = returns.dropna().to_numpy()
    n = len(r)
    out = np.empty((days, n_paths))
    for j in range(n_paths):
        sampled = []
        while len(sampled) < days:
            start = rng.integers(0, max(1, n - block))
            sampled.extend(r[start:start + block])
        out[:, j] = sampled[:days]
    return s0 * np.vstack([np.ones(n_paths), np.cumprod(1 + out, axis=0)])


def outcome_table(paths: np.ndarray, invested: float) -> dict:
    final = paths[-1] / paths[0] * invested
    return {
        "p05": float(np.percentile(final, 5)),
        "p25": float(np.percentile(final, 25)),
        "median": float(np.median(final)),
        "p75": float(np.percentile(final, 75)),
        "p95": float(np.percentile(final, 95)),
        "prob_loss": float((final < invested).mean()),
        "expected": float(final.mean()),
    }


def synthetic_ohlcv(days: int = 750, s0: float = 100.0, mu: float = 0.10, sigma: float = 0.25,
                    seed: int = 7, start: str = "2021-01-01") -> pd.DataFrame:
    """Realistic-looking synthetic OHLCV for tests and offline demos."""
    rng = np.random.default_rng(seed)
    close = gbm_paths(s0, mu, sigma, days - 1, 1, seed=seed)[:, 0]
    idx = pd.bdate_range(start, periods=days)
    noise = np.abs(rng.normal(0, sigma / np.sqrt(252), days))
    open_ = np.r_[s0, close[:-1]] * (1 + rng.normal(0, 0.002, days))
    high = np.maximum(open_, close) * (1 + noise)
    low = np.minimum(open_, close) * (1 - noise)
    vol = rng.integers(500_000, 5_000_000, days).astype(float)
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": vol}, index=idx)
