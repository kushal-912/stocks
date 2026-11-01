"""Event-driven daily backtester.

Rules that keep results honest:
* signals computed on bar t's close are executed at bar t+1's open (no look-ahead)
* whole shares only, cash can't go negative, every fill pays slippage + full Indian charges
* optional ATR stop-loss / take-profit checked intrabar, filled at the worse of open and the stop (gaps)
* after a stop-out the strategy must reset (signal <= 0) before it can re-enter
"""
from __future__ import annotations

import itertools
import math
from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

from quantdesk.analytics import risk
from quantdesk.analytics.indicators import atr as atr_fn
from quantdesk.backtest.costs import capital_gains_tax, order_costs
from quantdesk.strategies.base import Strategy


@dataclass
class BacktestConfig:
    capital: float = 100_000.0
    product: str = "CNC"
    cost_profile: str = "groww"
    slippage_bps: float = 5.0
    allocation: float = 1.0               # max fraction of equity committed to this instrument
    vol_target: float | None = None       # annualised; scales exposure down when realised vol is higher
    stop_atr: float | None = None         # stop-loss distance in ATRs from entry
    take_profit_atr: float | None = None
    allow_short: bool = False
    rebalance_band: float = 0.10          # ignore resizes smaller than 10% of the position
    rf: float = 0.065


@dataclass
class BacktestResult:
    symbol: str
    strategy: str
    config: BacktestConfig
    equity: pd.Series
    benchmark: pd.Series | None
    shares: pd.Series
    exposure: pd.Series
    trades: pd.DataFrame
    metrics: dict = field(default_factory=dict)

    def summary_row(self) -> dict:
        m = self.metrics
        return {"strategy": self.strategy, "final_value": m.get("final_value"), "total_return": m.get("total_return"),
                "cagr": m.get("cagr"), "sharpe": m.get("sharpe"), "max_drawdown": m.get("max_drawdown"),
                "trades": m.get("n_trades"), "win_rate": m.get("win_rate"), "costs": m.get("total_costs")}


class _Book:
    """Cash, position and trade bookkeeping."""

    def __init__(self, cfg: BacktestConfig):
        self.cfg = cfg
        self.cash = cfg.capital
        self.shares = 0
        self.avg = 0.0
        self.total_costs = 0.0
        self.trades: list[dict] = []
        self.open: dict | None = None

    def fill(self, qty: int, px: float, when, reason: str) -> None:
        if qty == 0:
            return
        side = "BUY" if qty > 0 else "SELL"
        fill_px = px * (1 + math.copysign(self.cfg.slippage_bps / 1e4, qty))
        value = abs(qty) * fill_px
        cost = order_costs(value, side, self.cfg.product, self.cfg.cost_profile).total
        self.cash -= qty * fill_px + cost
        self.total_costs += cost

        prev = self.shares
        if prev == 0 or np.sign(prev) == np.sign(qty):  # opening or adding
            if prev == 0:
                self.open = {"entry_date": when, "side": "LONG" if qty > 0 else "SHORT", "qty": 0, "realised": 0.0,
                             "costs": 0.0, "max_qty": 0}
            self.avg = (self.avg * abs(prev) + fill_px * abs(qty)) / (abs(prev) + abs(qty))
            self.shares = prev + qty
            self.open["entry_px"] = self.avg
        else:  # reducing / closing / flipping
            closing = min(abs(qty), abs(prev))
            self.open["realised"] += (fill_px - self.avg) * closing * np.sign(prev)
            self.shares = prev + qty
            if abs(qty) > abs(prev):  # flip: close then open the remainder
                self._close(when, fill_px, reason)
                self.open = {"entry_date": when, "side": "LONG" if qty > 0 else "SHORT", "qty": 0, "realised": 0.0,
                             "costs": 0.0, "max_qty": 0, "entry_px": fill_px}
                self.avg = fill_px
        if self.open is not None:
            self.open["costs"] += cost
            self.open["max_qty"] = max(self.open["max_qty"], abs(self.shares))
        if self.shares == 0 and self.open is not None:
            self._close(when, fill_px, reason)

    def _close(self, when, px: float, reason: str) -> None:
        o = self.open
        pnl = o["realised"] - o["costs"]
        basis = o["entry_px"] * o["max_qty"]
        self.trades.append({"entry_date": o["entry_date"], "exit_date": when, "side": o["side"], "qty": o["max_qty"],
                            "entry_price": o["entry_px"], "exit_price": px, "pnl": pnl,
                            "return": pnl / basis if basis else 0.0, "costs": o["costs"], "exit_reason": reason})
        self.open = None
        self.avg = 0.0


def run_backtest(df: pd.DataFrame, strategy: Strategy, cfg: BacktestConfig | None = None, symbol: str = "",
                 with_benchmark: bool = True) -> BacktestResult:
    cfg = cfg or BacktestConfig()
    df = df.dropna(subset=["open", "high", "low", "close"])
    if len(df) < 3:
        raise ValueError("Not enough data to backtest.")
    idx = df.index
    o, h, l, c = (df[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
    target = strategy.generate(df).reindex(idx).fillna(0.0).clip(-1 if cfg.allow_short else 0, 1)
    if cfg.vol_target:
        rv = np.log(df["close"]).diff().rolling(21).std() * np.sqrt(252)
        target = target * (cfg.vol_target / rv).clip(upper=1.0).fillna(0.0)
    tgt = target.to_numpy()
    a = atr_fn(df).to_numpy()

    book = _Book(cfg)
    equity, shares_hist = np.empty(len(df)), np.empty(len(df))
    stop = take = None
    blocked = False
    for i in range(len(df)):
        if i > 0:
            desired = tgt[i - 1]
            if blocked and desired <= 0:
                blocked = False
            if blocked:
                desired = 0.0
            eq_open = book.cash + book.shares * o[i]
            want = int(desired * cfg.allocation * eq_open / o[i]) if o[i] > 0 else 0
            delta = want - book.shares
            if delta > 0:  # can't spend cash we don't have
                buy_px = o[i] * (1 + cfg.slippage_bps / 1e4)
                affordable = int(max(book.cash - 25, 0) / (buy_px * 1.0025))
                delta = min(delta, affordable) if book.shares >= 0 else delta
            small = book.shares != 0 and want != 0 and np.sign(want) == np.sign(book.shares) \
                and abs(delta) < cfg.rebalance_band * abs(book.shares)
            if delta != 0 and not small:
                book.fill(delta, o[i], idx[i], "signal")
            # Attach stop/target to a new position as soon as ATR is available.
            if book.shares != 0 and not np.isnan(a[i - 1]):
                d = np.sign(book.shares)
                if stop is None and cfg.stop_atr:
                    stop = book.avg - d * cfg.stop_atr * a[i - 1]
                if take is None and cfg.take_profit_atr:
                    take = book.avg + d * cfg.take_profit_atr * a[i - 1]
        if book.shares > 0:
            if stop is not None and l[i] <= stop:
                book.fill(-book.shares, min(o[i], stop), idx[i], "stop_loss")
                blocked = True
            elif take is not None and h[i] >= take:
                book.fill(-book.shares, max(o[i], take), idx[i], "take_profit")
                blocked = True
        elif book.shares < 0:
            if stop is not None and h[i] >= stop:
                book.fill(-book.shares, max(o[i], stop), idx[i], "stop_loss")
                blocked = True
            elif take is not None and l[i] <= take:
                book.fill(-book.shares, min(o[i], take), idx[i], "take_profit")
                blocked = True
        if book.shares == 0:
            stop = take = None
        equity[i] = book.cash + book.shares * c[i]
        shares_hist[i] = book.shares

    eq = pd.Series(equity, index=idx, name="equity")
    sh = pd.Series(shares_hist, index=idx, name="shares")
    exposure = (sh * df["close"] / eq).rename("exposure")
    trades = pd.DataFrame(book.trades)

    bench = None
    if with_benchmark and strategy.name != "buy_hold":
        from quantdesk.strategies.library import BuyAndHold

        bench = run_backtest(df, BuyAndHold(), BacktestConfig(capital=cfg.capital, product="CNC",
                             cost_profile=cfg.cost_profile, slippage_bps=cfg.slippage_bps),
                             symbol, with_benchmark=False).equity.rename("buy_hold")

    m = risk.summary(eq, bench, cfg.rf)
    m.update(trade_stats(trades, sh, eq))
    m["final_value"] = float(eq.iloc[-1])
    m["total_costs"] = book.total_costs
    m["open_position_shares"] = int(book.shares)
    if cfg.product == "CNC":
        m.update({f"tax_{k}": v for k, v in capital_gains_tax(trades.to_dict("records") if len(trades) else []).items()})
    if bench is not None:
        m["benchmark_final_value"] = float(bench.iloc[-1])
        m["benchmark_cagr"] = risk.cagr(bench)
        m["benchmark_max_drawdown"] = risk.max_drawdown(bench)
    return BacktestResult(symbol, strategy.label(), cfg, eq, bench, sh, exposure, trades, m)


def trade_stats(trades: pd.DataFrame, shares: pd.Series, equity: pd.Series) -> dict:
    out = {"n_trades": int(len(trades)), "exposure_pct": float((shares != 0).mean())}
    if len(trades) == 0:
        return out | {"win_rate": float("nan"), "profit_factor": float("nan"), "expectancy": float("nan")}
    wins, losses = trades.loc[trades.pnl > 0, "pnl"], trades.loc[trades.pnl <= 0, "pnl"]
    hold = (pd.to_datetime(trades.exit_date) - pd.to_datetime(trades.entry_date)).dt.days
    out.update({
        "win_rate": float(len(wins) / len(trades)),
        "avg_win": float(wins.mean()) if len(wins) else 0.0,
        "avg_loss": float(losses.mean()) if len(losses) else 0.0,
        "profit_factor": float(wins.sum() / -losses.sum()) if losses.sum() < 0 else float("inf"),
        "expectancy": float(trades.pnl.mean()),
        "avg_hold_days": float(hold.mean()),
        "best_trade": float(trades.pnl.max()),
        "worst_trade": float(trades.pnl.min()),
    })
    return out


def parameter_sweep(df: pd.DataFrame, strategy_cls, grid: dict[str, list], cfg: BacktestConfig | None = None,
                    train_frac: float = 0.7) -> pd.DataFrame:
    """Grid search on the first ``train_frac`` of history, then re-run each setting out-of-sample.

    Compare in-sample vs out-of-sample Sharpe: a big drop means the parameters were curve-fit.
    """
    cut = int(len(df) * train_frac)
    train, test = df.iloc[:cut], df.iloc[max(0, cut - 260):]  # warm-up for indicators
    rows = []
    keys = list(grid)
    for combo in itertools.product(*grid.values()):
        params = dict(zip(keys, combo))
        strat = strategy_cls(**params)
        try:
            ins = run_backtest(train, strat, cfg, with_benchmark=False).metrics
            oos_res = run_backtest(test, strat, cfg, with_benchmark=False)
            oos_eq = oos_res.equity.iloc[min(260, len(oos_res.equity) - 2):]
            oos = risk.summary(oos_eq)
        except Exception:
            continue
        rows.append({**params, "is_sharpe": ins["sharpe"], "is_cagr": ins["cagr"],
                     "oos_sharpe": oos["sharpe"], "oos_cagr": oos["cagr"], "oos_max_dd": oos["max_drawdown"]})
    return pd.DataFrame(rows).sort_values("is_sharpe", ascending=False)


def run_portfolio_backtest(closes: pd.DataFrame, method: str = "equal", capital: float = 100_000.0,
                           rebalance: str = "ME", lookback: int = 252, cost_rate: float = 0.0025) -> dict:
    """Multi-asset backtest with periodic rebalancing to weights from ``method``
    (equal | min_variance | risk_parity | hrp | max_sharpe). Costs approximated as turnover * cost_rate."""
    from quantdesk.analytics import portfolio as pf

    rets = closes.pct_change().fillna(0.0)
    dates = closes.resample(rebalance).last().index
    rebal_days = set(closes.index[closes.index.get_indexer(dates, method="pad")])
    w = pd.Series(1 / closes.shape[1], index=closes.columns)
    weights_hist, equity, val = {}, [], capital
    cur = pd.Series(0.0, index=closes.columns)
    total_cost = 0.0
    for i, d in enumerate(closes.index):
        if i > 0:
            val *= 1 + float((cur * rets.loc[d]).sum())
            cur = cur * (1 + rets.loc[d])
            cur = cur / cur.sum() if cur.sum() > 0 else cur
        if d in rebal_days or i == 0:
            hist = rets.iloc[max(0, i - lookback):i + 1]
            if method != "equal" and len(hist) > 60:
                try:
                    if method == "min_variance":
                        w = pf.min_variance(pf.shrunk_covariance(hist))
                    elif method == "risk_parity":
                        w = pf.risk_parity(pf.shrunk_covariance(hist))
                    elif method == "hrp":
                        w = pf.hrp(hist)
                    elif method == "max_sharpe":
                        w = pf.max_sharpe(hist.mean() * 252, pf.shrunk_covariance(hist), max_weight=0.4)
                except Exception:
                    pass
            turnover = float((w - cur).abs().sum())
            cost = turnover * cost_rate * val
            val -= cost
            total_cost += cost
            cur = w.copy()
            weights_hist[d] = w
        equity.append(val)
    eq = pd.Series(equity, index=closes.index, name=method)
    return {"equity": eq, "weights": pd.DataFrame(weights_hist).T, "metrics": risk.summary(eq) | {"total_costs": total_cost}}


def config_dict(cfg: BacktestConfig) -> dict:
    return asdict(cfg)
