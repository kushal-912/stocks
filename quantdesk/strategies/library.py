"""Classic, well-documented rule-based strategies. Each is a hypothesis to test, not a recommendation."""
from __future__ import annotations

import numpy as np
import pandas as pd

from quantdesk.analytics import indicators as ind
from quantdesk.strategies.base import Strategy, hysteresis


class BuyAndHold(Strategy):
    name = "buy_hold"
    description = "Invest everything on day one and hold. The benchmark every strategy must beat after costs."

    def generate(self, df):
        return pd.Series(1.0, index=df.index)


class SMACrossover(Strategy):
    name = "sma_cross"
    description = "Long while the fast moving average is above the slow one (e.g. 50/200 'golden cross')."

    def __init__(self, fast: int = 50, slow: int = 200):
        super().__init__(fast=fast, slow=slow)

    def generate(self, df):
        f, s = ind.sma(df["close"], self.params["fast"]), ind.sma(df["close"], self.params["slow"])
        return (f > s).astype(float).where(s.notna(), 0.0)


class EMACrossover(Strategy):
    name = "ema_cross"
    description = "Faster-reacting crossover using exponential moving averages."

    def __init__(self, fast: int = 12, slow: int = 26):
        super().__init__(fast=fast, slow=slow)

    def generate(self, df):
        f, s = ind.ema(df["close"], self.params["fast"]), ind.ema(df["close"], self.params["slow"])
        return (f > s).astype(float).where(s.notna(), 0.0)


class RSIMeanReversion(Strategy):
    name = "rsi_reversion"
    description = "Buy oversold dips (RSI below entry) inside a long-term uptrend; exit when RSI recovers."

    def __init__(self, period: int = 14, entry: float = 30, exit: float = 55, trend_filter: int = 200):
        super().__init__(period=period, entry=entry, exit=exit, trend_filter=trend_filter)

    def generate(self, df):
        p = self.params
        r = ind.rsi(df["close"], p["period"])
        up = df["close"] > ind.sma(df["close"], p["trend_filter"]) if p["trend_filter"] else True
        return hysteresis((r < p["entry"]) & up, r > p["exit"])


class BollingerReversion(Strategy):
    name = "bollinger_reversion"
    description = "Buy a close below the lower Bollinger band, exit at the middle band."

    def __init__(self, n: int = 20, k: float = 2.0):
        super().__init__(n=n, k=k)

    def generate(self, df):
        bb = ind.bollinger(df["close"], self.params["n"], self.params["k"])
        return hysteresis(df["close"] < bb["lower"], df["close"] > bb["mid"])


class DonchianBreakout(Strategy):
    name = "donchian_breakout"
    description = "Turtle-style trend following: enter on an N-day high, exit on an M-day low."

    def __init__(self, entry: int = 55, exit: int = 20):
        super().__init__(entry=entry, exit=exit)

    def generate(self, df):
        hi = df["high"].rolling(self.params["entry"]).max().shift()
        lo = df["low"].rolling(self.params["exit"]).min().shift()
        return hysteresis(df["close"] > hi, df["close"] < lo)


class MACDMomentum(Strategy):
    name = "macd"
    description = "Long while the MACD line is above its signal line and above zero."

    def generate(self, df):
        m = ind.macd(df["close"])
        return ((m["macd"] > m["signal"]) & (m["macd"] > 0)).astype(float)


class TimeSeriesMomentum(Strategy):
    name = "ts_momentum"
    description = "12-1 month time-series momentum (Moskowitz, Ooi & Pedersen 2012), volatility-scaled."

    def __init__(self, lookback: int = 252, skip: int = 21, vol_target: float = 0.20):
        super().__init__(lookback=lookback, skip=skip, vol_target=vol_target)

    def generate(self, df):
        p = self.params
        c = df["close"]
        mom = c.shift(p["skip"]) / c.shift(p["lookback"]) - 1
        vol = np.log(c).diff().rolling(63).std() * np.sqrt(252)
        scale = (p["vol_target"] / vol).clip(upper=1.0)
        # Re-decide weekly to limit turnover.
        sig = ((mom > 0).astype(float) * scale).where(mom.notna(), 0.0)
        return sig.where(np.arange(len(sig)) % 5 == 0).ffill().fillna(0.0)


class SupertrendFollow(Strategy):
    name = "supertrend"
    description = "Long while Supertrend (ATR trailing band) points up."

    def __init__(self, n: int = 10, mult: float = 3.0):
        super().__init__(n=n, mult=mult)

    def generate(self, df):
        st = ind.supertrend(df, self.params["n"], self.params["mult"])
        return (st["direction"] > 0).astype(float)


class MLStrategy(Strategy):
    name = "ml_classifier"
    description = "Walk-forward gradient boosting: long when out-of-sample P(up) exceeds a threshold."

    def __init__(self, horizon: int = 5, threshold: float = 0.55, kind: str = "hgb"):
        super().__init__(horizon=horizon, threshold=threshold, kind=kind)

    def generate(self, df):
        from quantdesk.models.ml import walk_forward_proba

        p = self.params
        proba = walk_forward_proba(df, p["horizon"], p["kind"])
        return hysteresis(proba > p["threshold"], proba < 0.5)
