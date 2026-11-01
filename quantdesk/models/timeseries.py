"""Classical time-series forecasting on (log) prices and returns.

Each forecaster returns a :class:`Forecast` describing the expected cumulative log return over
``horizon`` trading days and its uncertainty. A useful sanity check: daily equity returns are
close to unpredictable, so honest models usually forecast small drifts with wide bands.
"""
from __future__ import annotations

import logging
import warnings
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)


@dataclass
class Forecast:
    model: str
    horizon: int
    exp_return: float          # expected cumulative log return over the horizon
    std: float                 # std-dev of that cumulative return
    last_price: float
    path: pd.Series = field(default_factory=pd.Series)   # expected price per future day
    lower: pd.Series = field(default_factory=pd.Series)  # ~95% band
    upper: pd.Series = field(default_factory=pd.Series)
    info: dict = field(default_factory=dict)

    @property
    def t_stat(self) -> float:
        return self.exp_return / self.std if self.std > 0 else 0.0

    @property
    def prob_up(self) -> float:
        from scipy.stats import norm
        return float(norm.cdf(self.t_stat))

    def to_dict(self) -> dict:
        return {"model": self.model, "horizon": self.horizon, "exp_return": self.exp_return, "std": self.std,
                "t_stat": self.t_stat, "prob_up": self.prob_up, **self.info}


def _future_index(last: pd.Timestamp, h: int) -> pd.DatetimeIndex:
    return pd.bdate_range(last + pd.Timedelta(days=1), periods=h)


def _bands(last_price: float, idx, mean_cum: np.ndarray, sd_cum: np.ndarray, model: str, h: int, info=None) -> Forecast:
    path = pd.Series(last_price * np.exp(mean_cum), index=idx)
    lower = pd.Series(last_price * np.exp(mean_cum - 1.96 * sd_cum), index=idx)
    upper = pd.Series(last_price * np.exp(mean_cum + 1.96 * sd_cum), index=idx)
    return Forecast(model, h, float(mean_cum[-1]), float(sd_cum[-1]), last_price, path, lower, upper, info or {})


def arima_forecast(close: pd.Series, horizon: int = 5, max_p: int = 2, max_q: int = 2, window: int = 750) -> Forecast:
    """ARMA(p,q) on daily log returns (== ARIMA(p,1,q) on log price), order chosen by AIC."""
    from statsmodels.tsa.arima.model import ARIMA

    r = np.log(close).diff().dropna().iloc[-window:]
    r = pd.Series(r.to_numpy(), index=pd.RangeIndex(len(r)))
    best, best_aic, best_order = None, np.inf, (0, 0)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for p in range(max_p + 1):
            for q in range(max_q + 1):
                try:
                    res = ARIMA(r, order=(p, 0, q), trend="c").fit()
                except Exception:
                    continue
                if res.aic < best_aic:
                    best, best_aic, best_order = res, res.aic, (p, q)
    if best is None:
        raise RuntimeError("ARIMA failed to fit")
    fc = best.get_forecast(horizon)
    mean = np.asarray(fc.predicted_mean)
    var = np.asarray(fc.var_pred_mean)
    return _bands(float(close.iloc[-1]), _future_index(close.index[-1], horizon), np.cumsum(mean),
                  np.sqrt(np.cumsum(var)), "ARIMA", horizon, {"order": f"({best_order[0]},1,{best_order[1]})", "aic": float(best_aic)})


def ets_forecast(close: pd.Series, horizon: int = 5, window: int = 500) -> Forecast:
    """Holt's damped-trend exponential smoothing on log price."""
    from statsmodels.tsa.holtwinters import ExponentialSmoothing

    y = np.log(close.iloc[-window:]).reset_index(drop=True)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        res = ExponentialSmoothing(y, trend="add", damped_trend=True).fit()
    mean = np.asarray(res.forecast(horizon)) - y.iloc[-1]
    sd1 = float(np.std(res.resid, ddof=1))
    sd = sd1 * np.sqrt(np.arange(1, horizon + 1))
    return _bands(float(close.iloc[-1]), _future_index(close.index[-1], horizon), mean, sd, "ETS", horizon,
                  {"alpha": float(res.params.get("smoothing_level", np.nan))})


def kalman_trend(close: pd.Series, q_level: float | None = None, q_slope: float | None = None) -> pd.DataFrame:
    """Local-linear-trend Kalman filter on log price.

    State x = [level, slope]; level_t = level_{t-1} + slope_{t-1} + w1; slope_t = slope_{t-1} + w2.
    Returns filtered level, slope (daily log drift) and slope std. A positive, significant slope
    is a noise-robust trend signal.
    """
    y = np.log(close.dropna().to_numpy())
    n = len(y)
    r_var = float(np.var(np.diff(y))) if n > 2 else 1e-4
    q_level = q_level if q_level is not None else r_var * 0.01
    q_slope = q_slope if q_slope is not None else r_var * 1e-4
    F = np.array([[1.0, 1.0], [0.0, 1.0]])
    H = np.array([[1.0, 0.0]])
    Q = np.diag([q_level, q_slope])
    x = np.array([y[0], 0.0])
    P = np.eye(2) * r_var * 10
    lev, slo, slo_sd = np.empty(n), np.empty(n), np.empty(n)
    for t in range(n):
        x = F @ x
        P = F @ P @ F.T + Q
        S = (H @ P @ H.T)[0, 0] + r_var
        K = (P @ H.T / S).ravel()
        x = x + K * (y[t] - x[0])
        P = (np.eye(2) - np.outer(K, H)) @ P
        lev[t], slo[t], slo_sd[t] = x[0], x[1], np.sqrt(max(P[1, 1], 0))
    idx = close.dropna().index
    return pd.DataFrame({"level": np.exp(lev), "slope": slo, "slope_sd": slo_sd}, index=idx)


def kalman_forecast(close: pd.Series, horizon: int = 5) -> Forecast:
    kf = kalman_trend(close)
    slope, sd = float(kf["slope"].iloc[-1]), float(kf["slope_sd"].iloc[-1])
    r_sd = float(np.log(close).diff().std())
    steps = np.arange(1, horizon + 1)
    mean = slope * steps
    total_sd = np.sqrt((sd * steps) ** 2 + r_sd**2 * steps)
    return _bands(float(close.iloc[-1]), _future_index(close.index[-1], horizon), mean, total_sd, "Kalman", horizon,
                  {"daily_drift": slope, "drift_t": slope / sd if sd > 0 else 0.0})


def garch_volatility(close: pd.Series, horizon: int = 5, window: int = 1000) -> dict:
    """GARCH(1,1) with Student-t errors on daily returns; EWMA fallback if `arch` is unavailable.

    Returns annualised current and forecast volatility and the model's persistence (alpha+beta).
    """
    r = np.log(close).diff().dropna().iloc[-window:] * 100
    try:
        from arch import arch_model

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            res = arch_model(r, vol="GARCH", p=1, q=1, dist="t", mean="Constant").fit(disp="off")
        var_path = res.forecast(horizon=horizon, reindex=False).variance.to_numpy()[-1]
        cond = float(res.conditional_volatility.iloc[-1])
        params = res.params
        persistence = float(params.get("alpha[1]", 0) + params.get("beta[1]", 0))
        return {"model": "GARCH(1,1)-t", "current_vol": cond / 100 * np.sqrt(252),
                "forecast_vol": float(np.sqrt(var_path.mean())) / 100 * np.sqrt(252),
                "horizon_std": float(np.sqrt(var_path.sum())) / 100,
                "persistence": persistence, "path": (np.sqrt(var_path) / 100 * np.sqrt(252)).tolist()}
    except ImportError:
        lam = 0.94
        v = float(((r / 100) ** 2).ewm(alpha=1 - lam, adjust=False).mean().iloc[-1])
        return {"model": "EWMA(0.94)", "current_vol": np.sqrt(v * 252), "forecast_vol": np.sqrt(v * 252),
                "horizon_std": np.sqrt(v * horizon), "persistence": 1.0, "path": [np.sqrt(v * 252)] * horizon}


def ensemble_forecast(close: pd.Series, horizon: int = 5) -> tuple[Forecast | None, list[Forecast]]:
    """Inverse-variance weighted combination of the individual forecasts (forecast combination puzzle:
    simple combinations usually beat any single model out-of-sample)."""
    fcs: list[Forecast] = []
    for fn in (arima_forecast, ets_forecast, kalman_forecast):
        try:
            fcs.append(fn(close, horizon))
        except Exception as exc:
            log.debug("%s failed: %s", fn.__name__, exc)
    if not fcs:
        return None, []
    w = np.array([1 / max(f.std, 1e-9) ** 2 for f in fcs])
    w /= w.sum()
    mean = float(sum(wi * f.exp_return for wi, f in zip(w, fcs)))
    sd = float(np.sqrt(sum(wi * f.std**2 for wi, f in zip(w, fcs))))
    idx = fcs[0].path.index
    steps = np.arange(1, horizon + 1) / horizon
    combo = _bands(float(close.iloc[-1]), idx, mean * steps, sd * np.sqrt(steps), "Ensemble", horizon,
                   {"weights": {f.model: float(wi) for wi, f in zip(w, fcs)}})
    return combo, fcs
