"""Statistical diagnostics: is this series trending, mean-reverting, or a random walk?"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats


def hurst_exponent(prices: pd.Series, max_lag: int = 100) -> float:
    """Hurst exponent from the scaling of lagged log-price differences.

    H ~ 0.5 random walk, H > 0.5 persistent/trending, H < 0.5 anti-persistent/mean-reverting.
    """
    x = np.log(prices.dropna().to_numpy())
    max_lag = min(max_lag, len(x) // 4)
    if max_lag < 10:
        return float("nan")
    lags = np.arange(2, max_lag)
    tau = np.array([np.std(x[lag:] - x[:-lag]) for lag in lags])
    mask = tau > 0
    slope, _ = np.polyfit(np.log(lags[mask]), np.log(tau[mask]), 1)
    return float(slope)


def adf_test(series: pd.Series) -> dict:
    """Augmented Dickey-Fuller: p < 0.05 rejects a unit root (series is stationary)."""
    import warnings

    from statsmodels.tsa.stattools import adfuller

    s = series.dropna()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FutureWarning)
        stat, p, lags, nobs, crit, _ = adfuller(s, autolag="AIC")[:6]
    return {"adf_stat": float(stat), "p_value": float(p), "lags": int(lags), "nobs": int(nobs),
            "crit_5pct": float(crit["5%"]), "stationary": bool(p < 0.05)}


def kpss_test(series: pd.Series) -> dict:
    """KPSS: the null is stationarity (opposite of ADF). Use both together."""
    import warnings

    from statsmodels.tsa.stattools import kpss

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        stat, p, lags, crit = kpss(series.dropna(), regression="c", nlags="auto")
    return {"kpss_stat": float(stat), "p_value": float(p), "stationary": bool(p > 0.05)}


def half_life(series: pd.Series) -> float:
    """Ornstein-Uhlenbeck half-life of mean reversion from dy_t = a + b*y_{t-1} + e.

    Returns days; inf if the series is not mean-reverting (b >= 0).
    """
    y = series.dropna()
    dy = y.diff().dropna()
    lag = y.shift().dropna().loc[dy.index]
    b = np.polyfit(lag.to_numpy(), dy.to_numpy(), 1)[0]
    return float(-np.log(2) / b) if b < 0 else float("inf")


def variance_ratio(prices: pd.Series, k: int = 5) -> dict:
    """Lo-MacKinlay variance ratio. VR>1 trending, VR<1 mean-reverting, z tests VR=1."""
    r = np.log(prices.dropna()).diff().dropna().to_numpy()
    n = len(r)
    if n < 5 * k:
        return {"vr": float("nan"), "z": float("nan"), "p_value": float("nan")}
    mu = r.mean()
    var1 = np.sum((r - mu) ** 2) / (n - 1)
    rk = np.convolve(r, np.ones(k), "valid")
    m = k * (n - k + 1) * (1 - k / n)
    vark = np.sum((rk - k * mu) ** 2) / m
    vr = vark / var1
    phi = 2 * (2 * k - 1) * (k - 1) / (3 * k * n)
    z = (vr - 1) / np.sqrt(phi)
    return {"vr": float(vr), "z": float(z), "p_value": float(2 * (1 - stats.norm.cdf(abs(z))))}


def ljung_box(r: pd.Series, lags: int = 10) -> dict:
    """Autocorrelation test on returns: p < 0.05 means returns are not white noise (some predictability)."""
    from statsmodels.stats.diagnostic import acorr_ljungbox

    res = acorr_ljungbox(r.dropna(), lags=[lags], return_df=True)
    return {"lb_stat": float(res["lb_stat"].iloc[0]), "p_value": float(res["lb_pvalue"].iloc[0])}


def jarque_bera(r: pd.Series) -> dict:
    """Normality of returns. Equity returns almost always fail this (fat tails)."""
    stat, p = stats.jarque_bera(r.dropna())
    return {"jb_stat": float(stat), "p_value": float(p), "normal": bool(p > 0.05)}


def cointegration(a: pd.Series, b: pd.Series) -> dict:
    """Engle-Granger test for a pairs trade. Also returns hedge ratio and spread z-score."""
    from statsmodels.tsa.stattools import coint

    df = pd.concat([np.log(a), np.log(b)], axis=1).dropna()
    t, p, _ = coint(df.iloc[:, 0], df.iloc[:, 1])
    beta = np.polyfit(df.iloc[:, 1], df.iloc[:, 0], 1)[0]
    spread = df.iloc[:, 0] - beta * df.iloc[:, 1]
    z = (spread - spread.rolling(60).mean()) / spread.rolling(60).std()
    return {"t_stat": float(t), "p_value": float(p), "cointegrated": bool(p < 0.05),
            "hedge_ratio": float(beta), "half_life": half_life(spread), "spread_z": float(z.iloc[-1])}


def autocorrelations(r: pd.Series, lags: int = 10) -> pd.Series:
    return pd.Series({lag: r.autocorr(lag) for lag in range(1, lags + 1)})


def diagnose(prices: pd.Series) -> dict:
    """One-stop regime diagnosis for the dashboard and the composite signal."""
    r = np.log(prices).diff().dropna()
    out = {"hurst": hurst_exponent(prices)}
    try:
        out["adf_price"] = adf_test(np.log(prices))
        out["adf_returns"] = adf_test(r)
    except Exception:
        pass
    out["half_life_days"] = half_life(np.log(prices.iloc[-252:])) if len(prices) > 60 else float("nan")
    out["variance_ratio"] = variance_ratio(prices)
    try:
        out["ljung_box"] = ljung_box(r)
    except Exception:
        pass
    out["jarque_bera"] = jarque_bera(r)
    h = out["hurst"]
    out["character"] = ("trending" if h > 0.55 else "mean-reverting" if h < 0.45 else "random-walk-like") if np.isfinite(h) else "unknown"
    return out
