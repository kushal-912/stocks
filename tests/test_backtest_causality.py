"""The backtest must not know the future.

Strategy: run the replay, then scramble every price after a cutoff date and run it again.
Everything the agent did before the cutoff - its equity, every buy and sell - must be identical.
If any decision depended on future prices, scrambling them would change the past.
"""
import numpy as np
import pandas as pd
import pytest

from quantdesk.analytics.simulation import synthetic_ohlcv
from quantdesk.data.panel import FIELDS, MarketPanel
from quantdesk.trader.backtest import backtest_policy, min_viable_capital


def _panel(n_stocks: int = 40, days: int = 700) -> tuple[MarketPanel, pd.DataFrame]:
    frames = {f: {} for f in FIELDS}
    for k in range(n_stocks):
        df = synthetic_ohlcv(days=days, s0=50 + 10 * k, mu=-0.1 + 0.02 * k, sigma=0.25 + 0.01 * (k % 7), seed=100 + k)
        for f in FIELDS:
            frames[f][f"S{k:02d}"] = df[f]
    nifty = synthetic_ohlcv(days=days, s0=20000, mu=0.08, sigma=0.15, seed=7)
    for f in FIELDS:
        frames[f]["NIFTY"] = nifty[f]
    panel = MarketPanel(*(pd.DataFrame(frames[f]) for f in FIELDS))
    uni = pd.DataFrame({"symbol": [f"S{k:02d}" for k in range(n_stocks)], "name": "x",
                        "sector": [f"Sec{k % 8}" for k in range(n_stocks)]})
    return panel, uni


def _scramble_after(panel: MarketPanel, cutoff: pd.Timestamp, seed: int = 99) -> MarketPanel:
    rng = np.random.default_rng(seed)
    out = {}
    future = panel.close.index > cutoff
    shock = pd.DataFrame(np.exp(np.cumsum(rng.normal(0, 0.08, panel.close.shape), axis=0)),
                         index=panel.close.index, columns=panel.close.columns)
    for f in FIELDS:
        df = getattr(panel, f).copy()
        if f == "volume":
            df.loc[future] = df.loc[future] * rng.uniform(0.1, 10, df.loc[future].shape)
        else:
            df.loc[future] = df.loc[future] * shock.loc[future]
        out[f] = df
    return MarketPanel(*(out[f] for f in FIELDS))


@pytest.fixture
def cfg(isolated_paths):
    s = isolated_paths
    s.universe.source = "nse_all"
    s.universe.min_traded_value_cr = 0.5
    return s


def test_scrambling_the_future_does_not_change_the_past(cfg):
    panel, uni = _panel()
    start = panel.close.index[320]
    cutoff = panel.close.index[520]
    ev_a, ev_b = [], []
    a = backtest_policy(panel, uni, str(start.date()), capital=100_000, on_event=ev_a.append, resolve_sectors=False)
    b = backtest_policy(_scramble_after(panel, cutoff), uni, str(start.date()), capital=100_000,
                        on_event=ev_b.append, resolve_sectors=False)
    before = a["equity"].index <= cutoff
    pd.testing.assert_series_equal(a["equity"][before], b["equity"][before])
    past = lambda evs: [e for e in evs if e.get("day") and pd.Timestamp(e["day"]) <= cutoff and e["type"] in ("buy", "sell")]
    assert past(ev_a) == past(ev_b)
    assert len(past(ev_a)) > 5, "the test needs the agent to actually trade before the cutoff"
    # ...and the future really was different, so the test would have caught a leak.
    assert not a["equity"][~before].equals(b["equity"][~before])


def test_decisions_execute_next_day_at_open(cfg):
    panel, uni = _panel()
    evs = []
    backtest_policy(panel, uni, str(panel.close.index[320].date()), capital=100_000, on_event=evs.append,
                    resolve_sectors=False)
    buys = [e for e in evs if e["type"] == "buy"]
    assert buys
    for e in buys[:20]:
        day = pd.Timestamp(e["day"])
        open_px = float(panel.open.loc[day, e["symbol"]])
        assert e["price"] == pytest.approx(open_px * (1 + cfg.execution.slippage_bps / 1e4))


def test_capital_floor_explains_tiny_accounts(cfg):
    floor = min_viable_capital()
    assert floor >= cfg.agent.min_position_value
    assert floor == pytest.approx(np.ceil(cfg.agent.min_position_value / (cfg.risk.max_position_pct / 100) / 1000) * 1000)
