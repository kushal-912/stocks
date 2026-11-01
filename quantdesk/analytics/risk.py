"""Return and risk statistics used across backtests, the dashboard and position sizing."""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

TRADING_DAYS = 252


def returns(prices: pd.Series | pd.DataFrame, log: bool = False):
    return np.log(prices).diff().dropna() if log else prices.pct_change().dropna()


def cagr(equity: pd.Series) -> float:
    equity = equity.dropna()
    if len(equity) < 2 or equity.iloc[0] <= 0:
        return float("nan")
    years = (equity.index[-1] - equity.index[0]).days / 365.25
    return float((equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1) if years > 0 else float("nan")


def annual_vol(r: pd.Series, periods: int = TRADING_DAYS) -> float:
    return float(r.std(ddof=1) * np.sqrt(periods))


def sharpe(r: pd.Series, rf_annual: float = 0.065, periods: int = TRADING_DAYS) -> float:
    """Annualised Sharpe ratio. Default risk-free ~ Indian 91-day T-bill yield; adjust as needed."""
    ex = r - rf_annual / periods
    sd = ex.std(ddof=1)
    return float(ex.mean() / sd * np.sqrt(periods)) if sd > 0 else float("nan")


def sortino(r: pd.Series, rf_annual: float = 0.065, periods: int = TRADING_DAYS) -> float:
    ex = r - rf_annual / periods
    downside = np.sqrt((np.minimum(ex, 0) ** 2).mean())
    return float(ex.mean() / downside * np.sqrt(periods)) if downside > 0 else float("nan")


def drawdown(equity: pd.Series) -> pd.Series:
    return equity / equity.cummax() - 1


def max_drawdown(equity: pd.Series) -> float:
    return float(drawdown(equity).min())


def max_drawdown_duration(equity: pd.Series) -> int:
    """Longest stretch (in bars) spent below a previous peak."""
    under = drawdown(equity) < 0
    longest = run = 0
    for flag in under:
        run = run + 1 if flag else 0
        longest = max(longest, run)
    return longest


def calmar(equity: pd.Series) -> float:
    mdd = max_drawdown(equity)
    return float(cagr(equity) / abs(mdd)) if mdd < 0 else float("nan")


def var_historical(r: pd.Series, level: float = 0.95) -> float:
    """One-period Value-at-Risk (positive number = loss) from the empirical distribution."""
    return float(-np.percentile(r.dropna(), 100 * (1 - level)))


def var_parametric(r: pd.Series, level: float = 0.95) -> float:
    return float(-(r.mean() + stats.norm.ppf(1 - level) * r.std(ddof=1)))


def var_cornish_fisher(r: pd.Series, level: float = 0.95) -> float:
    """Gaussian VaR corrected for skew and excess kurtosis (fat tails)."""
    z = stats.norm.ppf(1 - level)
    s, k = stats.skew(r.dropna()), stats.kurtosis(r.dropna())
    z_cf = z + (z**2 - 1) * s / 6 + (z**3 - 3 * z) * k / 24 - (2 * z**3 - 5 * z) * s**2 / 36
    return float(-(r.mean() + z_cf * r.std(ddof=1)))


def cvar(r: pd.Series, level: float = 0.95) -> float:
    """Expected Shortfall: mean loss beyond the VaR threshold."""
    r = r.dropna()
    cutoff = np.percentile(r, 100 * (1 - level))
    tail = r[r <= cutoff]
    return float(-tail.mean()) if len(tail) else float("nan")


def beta_alpha(r: pd.Series, bench: pd.Series, periods: int = TRADING_DAYS) -> tuple[float, float]:
    """CAPM beta and annualised Jensen's alpha versus a benchmark."""
    df = pd.concat([r, bench], axis=1, join="inner").dropna()
    if len(df) < 20:
        return float("nan"), float("nan")
    cov = np.cov(df.iloc[:, 0], df.iloc[:, 1], ddof=1)
    beta = cov[0, 1] / cov[1, 1]
    alpha = (df.iloc[:, 0].mean() - beta * df.iloc[:, 1].mean()) * periods
    return float(beta), float(alpha)


def information_ratio(r: pd.Series, bench: pd.Series, periods: int = TRADING_DAYS) -> float:
    active = (r - bench).dropna()
    sd = active.std(ddof=1)
    return float(active.mean() / sd * np.sqrt(periods)) if sd > 0 else float("nan")


def omega_ratio(r: pd.Series, threshold: float = 0.0) -> float:
    gains = (r - threshold).clip(lower=0).sum()
    losses = (threshold - r).clip(lower=0).sum()
    return float(gains / losses) if losses > 0 else float("nan")


def ulcer_index(equity: pd.Series) -> float:
    dd = drawdown(equity) * 100
    return float(np.sqrt((dd**2).mean()))


def ewma_vol(r: pd.Series, lam: float = 0.94, periods: int = TRADING_DAYS) -> pd.Series:
    """RiskMetrics exponentially weighted volatility (annualised)."""
    return np.sqrt((r**2).ewm(alpha=1 - lam, adjust=False).mean() * periods)


def summary(equity: pd.Series, bench_equity: pd.Series | None = None, rf_annual: float = 0.065) -> dict:
    r = equity.pct_change().dropna()
    out = {
        "total_return": float(equity.iloc[-1] / equity.iloc[0] - 1) if len(equity) > 1 else float("nan"),
        "cagr": cagr(equity),
        "annual_vol": annual_vol(r) if len(r) > 1 else float("nan"),
        "sharpe": sharpe(r, rf_annual) if len(r) > 1 else float("nan"),
        "sortino": sortino(r, rf_annual) if len(r) > 1 else float("nan"),
        "max_drawdown": max_drawdown(equity),
        "max_dd_days": max_drawdown_duration(equity),
        "calmar": calmar(equity),
        "var_95": var_historical(r) if len(r) > 20 else float("nan"),
        "cvar_95": cvar(r) if len(r) > 20 else float("nan"),
        "skew": float(stats.skew(r)) if len(r) > 20 else float("nan"),
        "excess_kurtosis": float(stats.kurtosis(r)) if len(r) > 20 else float("nan"),
        "omega": omega_ratio(r) if len(r) > 1 else float("nan"),
        "ulcer_index": ulcer_index(equity),
    }
    if bench_equity is not None and len(bench_equity) > 20:
        br = bench_equity.pct_change().dropna()
        out["beta"], out["alpha"] = beta_alpha(r, br)
        out["information_ratio"] = information_ratio(r, br)
    return out
