"""Policy logic and a full agent day on the paper broker (offline)."""
import json
from datetime import datetime

import pytest

from fakes import FakeFeed
from quantdesk.config import get_settings
from quantdesk.trader import policy
from quantdesk.trader.policy import Candidate, HoldingState


def cfgs():
    s = get_settings()
    return s.agent.model_copy(), s.risk.model_copy()


def test_plan_buys_best_within_slots_and_sector_cap():
    cfg, risk = cfgs()
    cfg.max_positions, cfg.max_per_sector = 3, 1
    cands = [Candidate("A", 0.9, 100, 2, "Tech"), Candidate("B", 0.8, 100, 2, "Tech"),
             Candidate("C", 0.7, 100, 2, "Bank"), Candidate("D", 0.6, 100, 2, "Pharma"), Candidate("E", 0.5, 100, 2, "Auto")]
    acts = policy.plan_day([], cands, 60_000, 60_000, True, cfg, risk)
    buys = [a.symbol for a in acts if a.action == "BUY"]
    assert buys == ["A", "C", "D"]
    assert any(a.symbol == "B" and "sector cap" in a.reason for a in acts if a.action == "SKIP")
    for a in acts:
        if a.action == "BUY":
            assert a.qty * a.ref_price >= cfg.min_position_value
            assert a.stop < a.ref_price


def test_risk_per_trade_limits_size():
    cfg, risk = cfgs()
    cfg.stop_atr, risk.risk_per_trade_pct = 2.5, 1.0
    qty, binding = policy.size_position(50_000, 50_000, 50_000, 100, 10, cfg, risk)
    # 1% of 50k = 500 risk; stop distance 2.5 * 10 = 25 => 20 shares
    assert qty == 20 and binding == "risk-per-trade"


def test_exits_respect_min_hold_and_score():
    cfg, risk = cfgs()
    young = HoldingState("Y", 10, 100, 2, 95, 90, score=-0.5)
    old = HoldingState("O", 10, 100, 10, 95, 90, score=-0.5)
    broken = HoldingState("T", 10, 100, 10, 95, 90, score=0.5, above_sma200=False)
    acts = policy.plan_day([young, old, broken], [], 50_000, 10_000, True, cfg, risk)
    sold = {a.symbol: a.reason for a in acts if a.action == "SELL"}
    assert "Y" not in sold and "decayed" in sold["O"] and "trend broken" in sold["T"]


def test_swap_needs_margin_and_valid_replacement():
    cfg, risk = cfgs()
    cfg.max_positions = 1
    weak = HoldingState("W", 50, 100, 20, 100, 90, "Bank", score=0.4, atr=2)
    acts = policy.plan_day([weak], [Candidate("N", 0.5, 100, 2, "Tech")], 50_000, 45_000, True, cfg, risk)
    assert not [a for a in acts if a.action == "SELL"]                   # 0.5 < 0.4 + 0.25
    acts = policy.plan_day([weak], [Candidate("N", 0.9, 100, 2, "Tech")], 50_000, 45_000, True, cfg, risk)
    assert {a.action for a in acts if a.symbol in ("W", "N")} == {"SELL", "BUY"}
    # replacement too expensive to size -> no swap at all
    acts = policy.plan_day([weak], [Candidate("X", 0.9, 60_000, 2, "Tech")], 50_000, 0, True, cfg, risk)
    assert not [a for a in acts if a.action == "SELL"]


def test_risk_off_caps_exposure():
    cfg, risk = cfgs()
    cfg.regime_filter = True
    cands = [Candidate(c, 0.9 - i * 0.01, 100, 2, f"S{i}") for i, c in enumerate("ABCDEF")]
    acts = policy.plan_day([], cands, 60_000, 60_000, False, cfg, risk)
    invested = sum(a.qty * a.ref_price for a in acts if a.action == "BUY")
    assert invested <= 60_000 * cfg.risk_off_max_exposure_pct / 100 + 1


def test_trailing_stop_only_rises():
    cfg, _ = cfgs()
    cfg.trail_atr = 3.0
    assert policy.trailing_stop(90, 120, 5, cfg) == pytest.approx(105)
    assert policy.trailing_stop(110, 100, 5, cfg) == 110


# ------------------------------------------------------------------ full agent cycle on paper
@pytest.fixture
def agent_env(isolated_paths, monkeypatch):
    import quantdesk.risk.guard as guard_mod
    import quantdesk.trader.agent as agent_mod
    from quantdesk.broker.paper import PaperBroker
    from quantdesk.store import db

    monkeypatch.setattr(guard_mod, "is_market_open", lambda *a, **k: True)
    monkeypatch.setattr(agent_mod, "is_market_open", lambda *a, **k: True)
    feed = FakeFeed({"ACME": 200.0, "NIFTY": 22000.0})
    clock = {"now": datetime(2026, 10, 9, 9, 20)}
    broker = PaperBroker(feed=feed, clock=lambda: clock["now"], market_open=lambda: True)
    broker.reset(50_000)
    agent = agent_mod.TradingAgent(broker=broker)
    monkeypatch.setattr(agent, "today", lambda: "2026-10-09")
    monkeypatch.setattr(agent, "confirm_trading_day", lambda: "trading")
    db.kv_set("daystatus:2026-10-09", "trading")
    return agent, broker, feed, clock


def test_agent_entry_stop_and_exit_cycle(agent_env):
    from quantdesk.store import db

    agent, broker, feed, clock = agent_env
    # A planned BUY as premarket would write it.
    db.execute("INSERT INTO decisions(ts, day, account, symbol, action, qty, price, score, reason, data, status) "
               "VALUES(?,?,?,?,?,?,?,?,?,?, 'planned')",
               (db.now(), "2026-10-09", "paper", "ACME", "BUY", 40, 200.0, 0.8, "test thesis",
                json.dumps({"atr": 4.0, "sector": "Tech", "stop": 190.0})))
    done = agent.open_session()
    assert done["bought"] == 1
    # Marketable limit fills immediately on the paper exchange; the tick turns it into a managed position + stop.
    agent.tick()
    pos = agent.managed()["ACME"]
    assert pos["qty"] == 40 and pos["stop"] == pytest.approx(policy.initial_stop(pos["entry_price"], 4.0, agent.cfg))
    stops = agent._open_agent_orders("ACME", "stop")
    assert len(stops) == 1 and stops[0]["order_type"] == "SL-M"
    # Price collapses through the stop -> the resting stop fills, the trade is closed with reason.
    feed.add_bars("ACME", [("2026-10-09 10:01", 195, 195, 185, 186)])
    clock["now"] = datetime(2026, 10, 9, 10, 2)
    agent.tick()
    assert "ACME" not in agent.managed()
    t = db.query("SELECT * FROM trades")[0]
    assert t["exit_reason"] == "stop-loss hit" and t["pnl"] < 0
    snap = agent.snapshot()
    assert snap["equity"] == pytest.approx(broker.get_funds().total_equity, rel=1e-6)   # virtual == broker on paper
    assert db.verify_chain()["ok"]


def test_agent_skips_gap_ups(agent_env):
    from quantdesk.store import db

    agent, broker, feed, _ = agent_env
    feed.prices["ACME"] = 210.0   # +5% vs the 200 reference
    db.execute("INSERT INTO decisions(ts, day, account, symbol, action, qty, price, score, reason, data, status) "
               "VALUES(?,?,?,?,?,?,?,?,?,?, 'planned')",
               (db.now(), "2026-10-09", "paper", "ACME", "BUY", 40, 200.0, 0.8, "t", json.dumps({"atr": 4.0})))
    assert agent.open_session()["skipped"] == 1
    assert not broker.get_holdings()
