"""Technical indicators, implemented from their textbook definitions with pandas.

Every function takes Series/DataFrames indexed by time and uses only data up to each bar
(no look-ahead), so the outputs are safe to use as features in backtests.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def wilder(s: pd.Series, n: int) -> pd.Series:
    """Wilder's smoothing (an EMA with alpha = 1/n), used by RSI, ATR and ADX."""
    return s.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    """Relative Strength Index: 100 - 100 / (1 + avg_gain / avg_loss)."""
    d = close.diff()
    gain = wilder(d.clip(lower=0), n)
    loss = wilder(-d.clip(upper=0), n)
    rs = gain / loss.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    return out.where(loss != 0, 100.0).where(gain.notna())


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    line = ema(close, fast) - ema(close, slow)
    sig = ema(line, signal)
    return pd.DataFrame({"macd": line, "signal": sig, "hist": line - sig})


def bollinger(close: pd.Series, n: int = 20, k: float = 2.0) -> pd.DataFrame:
    mid = sma(close, n)
    sd = close.rolling(n, min_periods=n).std(ddof=0)
    upper, lower = mid + k * sd, mid - k * sd
    pct_b = (close - lower) / (upper - lower)
    return pd.DataFrame({"mid": mid, "upper": upper, "lower": lower, "pct_b": pct_b, "width": (upper - lower) / mid})


def true_range(df: pd.DataFrame) -> pd.Series:
    prev = df["close"].shift()
    return pd.concat([df["high"] - df["low"], (df["high"] - prev).abs(), (df["low"] - prev).abs()], axis=1).max(axis=1)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    return wilder(true_range(df), n)


def stochastic(df: pd.DataFrame, n: int = 14, d: int = 3) -> pd.DataFrame:
    lo, hi = df["low"].rolling(n).min(), df["high"].rolling(n).max()
    k = 100 * (df["close"] - lo) / (hi - lo).replace(0, np.nan)
    return pd.DataFrame({"k": k, "d": k.rolling(d).mean()})


def adx(df: pd.DataFrame, n: int = 14) -> pd.DataFrame:
    """Average Directional Index: trend *strength* (not direction). >25 is commonly read as trending."""
    up, down = df["high"].diff(), -df["low"].diff()
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    tr = wilder(true_range(df), n)
    plus_di = 100 * wilder(pd.Series(plus_dm, index=df.index), n) / tr
    minus_di = 100 * wilder(pd.Series(minus_dm, index=df.index), n) / tr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return pd.DataFrame({"adx": wilder(dx, n), "plus_di": plus_di, "minus_di": minus_di})


def obv(df: pd.DataFrame) -> pd.Series:
    return (np.sign(df["close"].diff()).fillna(0) * df["volume"]).cumsum()


def vwap(df: pd.DataFrame) -> pd.Series:
    """Session VWAP for intraday bars (resets each calendar day)."""
    typical = (df["high"] + df["low"] + df["close"]) / 3
    day = df.index.normalize()
    pv = (typical * df["volume"]).groupby(day).cumsum()
    vol = df["volume"].groupby(day).cumsum().replace(0, np.nan)
    return pv / vol


def donchian(df: pd.DataFrame, n: int = 20) -> pd.DataFrame:
    return pd.DataFrame({"upper": df["high"].rolling(n).max(), "lower": df["low"].rolling(n).min()})


def keltner(df: pd.DataFrame, n: int = 20, k: float = 2.0) -> pd.DataFrame:
    mid = ema(df["close"], n)
    a = atr(df, n)
    return pd.DataFrame({"mid": mid, "upper": mid + k * a, "lower": mid - k * a})


def cci(df: pd.DataFrame, n: int = 20) -> pd.Series:
    tp = (df["high"] + df["low"] + df["close"]) / 3
    mad = tp.rolling(n).apply(lambda x: np.mean(np.abs(x - x.mean())), raw=True)
    return (tp - tp.rolling(n).mean()) / (0.015 * mad)


def williams_r(df: pd.DataFrame, n: int = 14) -> pd.Series:
    hi, lo = df["high"].rolling(n).max(), df["low"].rolling(n).min()
    return -100 * (hi - df["close"]) / (hi - lo).replace(0, np.nan)


def supertrend(df: pd.DataFrame, n: int = 10, mult: float = 3.0) -> pd.DataFrame:
    """Supertrend: ATR-band trailing stop. direction = +1 (up-trend) / -1 (down-trend)."""
    a = atr(df, n)
    hl2 = (df["high"] + df["low"]) / 2
    upper_basic, lower_basic = (hl2 + mult * a).to_numpy(), (hl2 - mult * a).to_numpy()
    close = df["close"].to_numpy()
    upper, lower = upper_basic.copy(), lower_basic.copy()
    direction = np.ones(len(df))
    for i in range(1, len(df)):
        if np.isnan(upper_basic[i]) or np.isnan(upper[i - 1]):
            continue
        upper[i] = upper_basic[i] if (upper_basic[i] < upper[i - 1] or close[i - 1] > upper[i - 1]) else upper[i - 1]
        lower[i] = lower_basic[i] if (lower_basic[i] > lower[i - 1] or close[i - 1] < lower[i - 1]) else lower[i - 1]
        if close[i] > upper[i - 1]:
            direction[i] = 1
        elif close[i] < lower[i - 1]:
            direction[i] = -1
        else:
            direction[i] = direction[i - 1]
    line = np.where(direction == 1, lower, upper)
    return pd.DataFrame({"supertrend": line, "direction": direction}, index=df.index)


def feature_frame(df: pd.DataFrame) -> pd.DataFrame:
    """A standard, look-ahead-free feature set used by the ML models and the dashboard."""
    c = df["close"]
    r = np.log(c).diff()
    f = pd.DataFrame(index=df.index)
    for n in (1, 5, 10, 21, 63):
        f[f"ret_{n}"] = c.pct_change(n)
    for n in (10, 21, 63):
        f[f"vol_{n}"] = r.rolling(n).std() * np.sqrt(252)
    f["rsi_14"] = rsi(c, 14)
    f["rsi_2"] = rsi(c, 2)
    m = macd(c)
    f["macd_hist_norm"] = m["hist"] / c
    bb = bollinger(c)
    f["bb_pct_b"] = bb["pct_b"]
    f["bb_width"] = bb["width"]
    f["atr_pct"] = atr(df) / c
    f["dist_sma_20"] = c / sma(c, 20) - 1
    f["dist_sma_50"] = c / sma(c, 50) - 1
    f["dist_sma_200"] = c / sma(c, 200) - 1
    ad = adx(df)
    f["adx"] = ad["adx"]
    f["di_spread"] = ad["plus_di"] - ad["minus_di"]
    st = stochastic(df)
    f["stoch_k"] = st["k"]
    if "volume" in df and df["volume"].sum() > 0:
        v = df["volume"].replace(0, np.nan)
        f["vol_z_21"] = (v - v.rolling(21).mean()) / v.rolling(21).std()
        f["obv_slope_21"] = obv(df).diff(21) / v.rolling(21).sum()
    f["dow"] = df.index.dayofweek
    return f.replace([np.inf, -np.inf], np.nan)
