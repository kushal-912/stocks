"""The composite signal: several independent views of the same stock, each scaled to [-1, 1].

    trend           where price sits relative to its long averages, weighted by ADX trend strength
    momentum        12-1 month risk-adjusted momentum + MACD impulse
    mean_reversion  contrarian RSI / Bollinger %B, damped when the trend is strong
    forecast        Kalman-filter drift t-stat (backtests) or the ARIMA/ETS/Kalman ensemble (daily run)
    ml              walk-forward gradient-boosting probability, 2p - 1
    sentiment       recency-weighted news sentiment (live/daily only - no historical news archive)

Combining weakly correlated signals is the core idea of most quant equity models: each is noisy,
but the errors partly cancel. The weights live in config/settings.yaml.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from quantdesk.analytics import indicators as ind
from quantdesk.config import get_settings
from quantdesk.models.timeseries import kalman_trend
from quantdesk.strategies.base import Strategy, hysteresis


def technical_components(df: pd.DataFrame) -> pd.DataFrame:
    c = df["close"]
    out = pd.DataFrame(index=df.index)

    sma50, sma200 = ind.sma(c, 50), ind.sma(c, 200)
    ad = ind.adx(df)
    strength = (ad["adx"] / 40).clip(0, 1)
    raw_trend = (np.sign(c - sma200) + np.sign(sma50 - sma200) + np.tanh((ad["plus_di"] - ad["minus_di"]) / 20)) / 3
    out["trend"] = raw_trend * (0.5 + 0.5 * strength)

    vol = np.log(c).diff().rolling(252, min_periods=126).std() * np.sqrt(252)
    mom = (c.shift(21) / c.shift(252) - 1) / vol
    macd_imp = np.tanh(ind.macd(c)["hist"] / ind.atr(df) * 2)
    out["momentum"] = 0.7 * np.tanh(mom) + 0.3 * macd_imp

    r = ind.rsi(c, 14)
    pb = ind.bollinger(c)["pct_b"]
    mr = 0.5 * (-(r - 50) / 25).clip(-1, 1) + 0.5 * (-(pb - 0.5) * 2).clip(-1, 1)
    out["mean_reversion"] = mr * (1 - 0.6 * strength)

    kf = kalman_trend(c)
    t = kf["slope"] / kf["slope_sd"].replace(0, np.nan)
    out["forecast"] = np.tanh(t / 3)
    return out.clip(-1, 1)


def combine(components: pd.DataFrame | pd.Series, weights: dict[str, float] | None = None):
    """Weighted average over whichever components are present (NaN components are skipped)."""
    weights = weights or get_settings().signals.weights
    if isinstance(components, pd.Series):
        num = sum(weights.get(k, 0) * v for k, v in components.items() if pd.notna(v))
        den = sum(weights.get(k, 0) for k, v in components.items() if pd.notna(v))
        return float(num / den) if den else 0.0
    cols = [c for c in components.columns if c in weights]
    w = pd.Series({c: weights[c] for c in cols})
    present = components[cols].notna()
    num = components[cols].fillna(0).mul(w, axis=1).sum(axis=1)
    den = present.mul(w, axis=1).sum(axis=1)
    return (num / den.replace(0, np.nan)).fillna(0.0)


class CompositeStrategy(Strategy):
    name = "composite"
    description = "Weighted blend of trend, momentum, mean-reversion, Kalman forecast and (optionally) ML."

    def __init__(self, use_ml: bool = False, buy: float | None = None, sell: float | None = None,
                 scale_by_score: bool = False):
        s = get_settings().signals
        super().__init__(use_ml=use_ml, buy=buy if buy is not None else s.buy_threshold,
                         sell=sell if sell is not None else s.sell_threshold, scale_by_score=scale_by_score)

    def scores(self, df: pd.DataFrame) -> tuple[pd.Series, pd.DataFrame]:
        comps = technical_components(df)
        if self.params["use_ml"]:
            from quantdesk.models.ml import walk_forward_proba

            comps["ml"] = 2 * walk_forward_proba(df) - 1
        return combine(comps), comps

    def generate(self, df):
        score, _ = self.scores(df)
        pos = hysteresis(score > self.params["buy"], score < self.params["sell"])
        if self.params["scale_by_score"]:
            pos = pos * (score / 0.5).clip(0.25, 1.0)
        return pos
