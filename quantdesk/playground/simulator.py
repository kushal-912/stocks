"""The playground: 'what if I had invested Rs X on date D?' using real historical prices.

* time_machine        lump sum into one stock, any strategy vs buy-and-hold, with full charges and tax estimate
* sip                 monthly SIP into a stock/index with XIRR
* portfolio_machine   several stocks with equal / min-variance / risk-parity / HRP / max-Sharpe weights
* forward_simulation  Monte Carlo cone of possible futures (GBM or bootstrap of real returns)
Indicators need history before the start date, so ~15 months of warm-up data is loaded and the
strategy is only allowed to trade from the chosen start.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.optimize import brentq

from quantdesk.analytics import risk as rk
from quantdesk.analytics.simulation import bootstrap_paths, gbm_paths, outcome_table
from quantdesk.backtest.engine import BacktestConfig, BacktestResult, run_backtest, run_portfolio_backtest, trade_stats
from quantdesk.data.market import get_history, get_many_closes
from quantdesk.strategies import make_strategy
from quantdesk.strategies.base import Strategy
from quantdesk.strategies.library import BuyAndHold

WARMUP_DAYS = 450


class _StartGate(Strategy):
    """Wraps a strategy so it holds no position before ``start``."""

    def __init__(self, inner: Strategy, start: pd.Timestamp):
        super().__init__(**inner.params)
        self.inner, self.start = inner, start
        self.name = inner.name

    def generate(self, df):
        sig = self.inner.generate(df)
        return sig.where(sig.index >= self.start, 0.0)

    def label(self):
        return self.inner.label()


def _trim(res: BacktestResult, start: pd.Timestamp, capital: float, rf: float) -> BacktestResult:
    # Keep the bar before `start` so the first trade's open-to-close move is included.
    pos = max(0, res.equity.index.searchsorted(start) - 1)
    eq = res.equity.iloc[pos:]
    eq = eq * capital / eq.iloc[0]
    trades = res.trades[pd.to_datetime(res.trades["entry_date"]) >= start] if len(res.trades) else res.trades
    m = rk.summary(eq, rf_annual=rf)
    m.update(trade_stats(trades, res.shares.iloc[pos:], eq))
    m["final_value"] = float(eq.iloc[-1])
    m["total_costs"] = float(trades["costs"].sum()) if len(trades) else res.metrics.get("total_costs", 0.0)
    for k in ("tax_stcg", "tax_ltcg", "tax_tax", "open_position_shares"):
        if k in res.metrics:
            m[k] = res.metrics[k]
    return BacktestResult(res.symbol, res.strategy, res.config, eq, None, res.shares.iloc[pos:],
                          res.exposure.iloc[pos:], trades, m)


@dataclass
class TimeMachineResult:
    symbol: str
    amount: float
    start: pd.Timestamp
    end: pd.Timestamp
    results: dict[str, BacktestResult]
    prices: pd.DataFrame

    def table(self) -> pd.DataFrame:
        rows = []
        for name, r in self.results.items():
            m = r.metrics
            rows.append({"strategy": name, "final_value": m["final_value"], "profit": m["final_value"] - self.amount,
                         "total_return": m["total_return"], "cagr": m["cagr"], "max_drawdown": m["max_drawdown"],
                         "sharpe": m["sharpe"], "trades": m.get("n_trades", 0), "win_rate": m.get("win_rate"),
                         "charges": m.get("total_costs", 0.0), "est_tax": m.get("tax_tax", 0.0)})
        return pd.DataFrame(rows).set_index("strategy").sort_values("final_value", ascending=False)


def time_machine(symbol: str, amount: float, start: str, end: str | None = None,
                 strategies: list[str | tuple[str, dict]] = ("buy_hold", "composite"),
                 cfg: BacktestConfig | None = None, df: pd.DataFrame | None = None) -> TimeMachineResult:
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end) if end else pd.Timestamp.today().normalize()
    if df is None:
        df = get_history(symbol, start=start_ts - pd.Timedelta(days=WARMUP_DAYS), end=end_ts + pd.Timedelta(days=1))
    df = df.loc[:end_ts]
    if df.empty or df.index[-1] < start_ts:
        raise ValueError(f"No price data for {symbol} in that window.")
    cfg = cfg or BacktestConfig()
    cfg.capital = amount
    out = {}
    for spec in strategies:
        name, params = (spec, {}) if isinstance(spec, str) else spec
        strat = BuyAndHold() if name == "buy_hold" else make_strategy(name, **params)
        res = run_backtest(df, _StartGate(strat, start_ts), cfg, symbol, with_benchmark=False)
        out[strat.label()] = _trim(res, start_ts, amount, cfg.rf)
    return TimeMachineResult(symbol, amount, start_ts, end_ts, out, df.loc[start_ts:])


def xirr(cashflows: list[tuple[pd.Timestamp, float]]) -> float:
    """Annualised internal rate of return for irregular cash flows (negative = invested)."""
    t0 = cashflows[0][0]
    years = np.array([(d - t0).days / 365.0 for d, _ in cashflows])
    amts = np.array([a for _, a in cashflows])
    f = lambda r: np.sum(amts / (1 + r) ** years)
    try:
        return float(brentq(f, -0.99, 10))
    except ValueError:
        return float("nan")


def sip(symbol: str, monthly: float, start: str, end: str | None = None, day_of_month: int = 1) -> dict:
    """Systematic Investment Plan: buy ``monthly`` rupees of the stock each month (fractional units,
    like a mutual-fund SIP, to isolate the timing effect)."""
    end_ts = pd.Timestamp(end) if end else pd.Timestamp.today().normalize()
    df = get_history(symbol, start=start, end=end_ts + pd.Timedelta(days=1))
    if df.empty:
        raise ValueError(f"No data for {symbol}")
    close = df["close"]
    months = pd.date_range(pd.Timestamp(start), end_ts, freq="MS") + pd.Timedelta(days=day_of_month - 1)
    units, invested, flows, rows = 0.0, 0.0, [], []
    for d in months:
        i = close.index.searchsorted(d)
        if i >= len(close):
            break
        px, when = float(close.iloc[i]), close.index[i]
        units += monthly / px
        invested += monthly
        flows.append((when, -monthly))
        rows.append({"date": when, "price": px, "units": units, "invested": invested, "value": units * px})
    value = units * float(close.iloc[-1])
    flows.append((close.index[-1], value))
    ledger = pd.DataFrame(rows).set_index("date")
    curve = ledger["units"].reindex(close.index, method="ffill").fillna(0) * close
    inv_curve = ledger["invested"].reindex(close.index, method="ffill").fillna(0)
    return {"invested": invested, "value": value, "profit": value - invested, "xirr": xirr(flows),
            "units": units, "ledger": ledger, "value_curve": curve, "invested_curve": inv_curve}


def portfolio_machine(symbols: list[str], amount: float, start: str, end: str | None = None,
                      methods: tuple[str, ...] = ("equal", "min_variance", "risk_parity", "hrp")) -> dict:
    start_ts = pd.Timestamp(start)
    closes = get_many_closes(symbols, start=(start_ts - pd.Timedelta(days=WARMUP_DAYS)).date().isoformat(), end=end)
    out = {}
    for m in methods:
        res = run_portfolio_backtest(closes, m, capital=amount)
        eq = res["equity"].loc[start_ts:]
        eq = eq * amount / eq.iloc[0]
        out[m] = {"equity": eq, "weights": res["weights"].loc[start_ts:] if len(res["weights"]) else res["weights"],
                  "metrics": rk.summary(eq)}
    return out


def forward_simulation(symbol: str, amount: float, days: int = 252, n_paths: int = 2000,
                       method: str = "bootstrap", seed: int | None = 42) -> dict:
    df = get_history(symbol, years=10)
    r = df["close"].pct_change().dropna()
    s0 = float(df["close"].iloc[-1])
    if method == "gbm":
        lr = np.log(1 + r)
        paths = gbm_paths(s0, float(lr.mean() * 252 + 0.5 * lr.var() * 252), float(lr.std() * np.sqrt(252)),
                          days, n_paths, seed)
    else:
        paths = bootstrap_paths(r, s0, days, n_paths, seed=seed)
    pct = np.percentile(paths / s0 * amount, [5, 25, 50, 75, 95], axis=1)
    idx = pd.bdate_range(df.index[-1] + pd.Timedelta(days=1), periods=days + 1)
    cone = pd.DataFrame(pct.T, index=idx, columns=["p05", "p25", "p50", "p75", "p95"])
    return {"cone": cone, "outcomes": outcome_table(paths, amount), "sample_paths": paths[:, :50] / s0 * amount,
            "index": idx}
