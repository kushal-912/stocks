import numpy as np
import pandas as pd
import pytest

from quantdesk.backtest import BacktestConfig, run_backtest
from quantdesk.backtest.costs import order_costs
from quantdesk.strategies import REGISTRY, make_strategy
from quantdesk.strategies.base import Strategy
from quantdesk.strategies.library import BuyAndHold


def test_delivery_costs_breakdown():
    c = order_costs(100_000, "BUY", "CNC", "zerodha")
    assert c.brokerage == 0
    assert c.stt == pytest.approx(100)          # 0.1%
    assert c.stamp == pytest.approx(15)         # 0.015%
    sell = order_costs(100_000, "SELL", "CNC", "zerodha")
    assert sell.dp == pytest.approx(15.34) and sell.stamp == 0
    g = order_costs(100_000, "BUY", "CNC", "groww")
    assert g.brokerage == 20                    # capped
    assert order_costs(1_000, "BUY", "CNC", "groww").brokerage == 5  # floor


def test_buy_hold_matches_price_path(ohlcv):
    res = run_backtest(ohlcv, BuyAndHold(), BacktestConfig(capital=1_000_000, slippage_bps=0), with_benchmark=False)
    shares = res.shares.iloc[-1]
    assert shares > 0
    price_ret = ohlcv["close"].iloc[-1] / ohlcv["open"].iloc[1] - 1
    assert res.metrics["total_return"] == pytest.approx(price_ret, abs=0.01)
    assert (res.equity > 0).all()


class _Peek(Strategy):
    """Cheating strategy: long only on days that close up. Must NOT profit, because the engine executes next open."""
    name = "peek"

    def generate(self, df):
        return (df["close"] > df["open"]).astype(float)


def test_no_lookahead_execution(ohlcv):
    res = run_backtest(ohlcv, _Peek(), BacktestConfig(slippage_bps=0), with_benchmark=False)
    perfect = (ohlcv["close"] / ohlcv["open"]).where(ohlcv["close"] > ohlcv["open"], 1).prod() - 1
    assert res.metrics["total_return"] < perfect * 0.1


def test_cash_never_negative_and_whole_shares(ohlcv):
    res = run_backtest(ohlcv, make_strategy("ema_cross"), BacktestConfig(capital=50_000))
    assert (res.shares == res.shares.round()).all()
    implied_cash = res.equity - res.shares * ohlcv["close"]
    assert implied_cash.min() > -1e-6


def test_stop_loss_exits(ohlcv):
    res = run_backtest(ohlcv, BuyAndHold(), BacktestConfig(stop_atr=1.0), with_benchmark=False)
    assert "stop_loss" in set(res.trades["exit_reason"])


@pytest.mark.parametrize("name", [n for n in REGISTRY if n != "ml_classifier"])
def test_every_strategy_runs(name, ohlcv):
    res = run_backtest(ohlcv, make_strategy(name), BacktestConfig())
    assert np.isfinite(res.metrics["final_value"])
    assert res.benchmark is None or len(res.benchmark) == len(res.equity)


def test_ml_strategy_runs(ohlcv):
    res = run_backtest(ohlcv, make_strategy("ml_classifier"), BacktestConfig(), with_benchmark=False)
    assert np.isfinite(res.metrics["final_value"])


def test_time_machine_offline(ohlcv):
    from quantdesk.playground import time_machine

    tm = time_machine("TEST", 100_000, "2022-06-01", None, ["buy_hold", "composite", "sma_cross"], df=ohlcv)
    table = tm.table()
    assert len(table) == 3
    for r in tm.results.values():
        assert r.equity.iloc[0] == pytest.approx(100_000)
        assert r.equity.index[1] >= pd.Timestamp("2022-06-01")
