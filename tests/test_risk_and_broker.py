from datetime import datetime

import pytest

from fakes import FakeFeed
from quantdesk.broker import OrderRequest
from quantdesk.broker.paper import PaperBroker
from quantdesk.engine.executor import OrderExecutor
from quantdesk.risk import OrderRateLimiter, RiskGuard, position_size, set_kill


@pytest.fixture
def market(isolated_paths, monkeypatch):
    """Paper broker on a fake feed, with the market forced open."""
    import quantdesk.risk.guard as guard_mod

    monkeypatch.setattr(guard_mod, "is_market_open", lambda *a, **k: True)
    feed = FakeFeed({"AAA": 100.0, "BBB": 2500.0})
    clock = {"now": datetime(2026, 10, 9, 9, 30)}
    b = PaperBroker(feed=feed, clock=lambda: clock["now"], market_open=lambda: True)
    b.reset(100_000)
    return b, feed, clock


def test_position_size_respects_all_limits():
    r = position_size(100_000, 100, 95, risk_pct=1, max_position_pct=20)
    assert r["limits"]["risk_per_trade"] == 200 and r["limits"]["concentration"] == 200
    r = position_size(100_000, 100, 98, risk_pct=1, max_position_pct=10)
    assert r["quantity"] == 100 and r["binding"] == "concentration"


def test_market_order_fills_with_slippage_and_charges(market):
    b, feed, _ = market
    res = b.place_order(OrderRequest("AAA", "BUY", 100))
    assert res.status == "COMPLETE"
    assert res.average_price == pytest.approx(100 * 1.0005)
    f = b.get_funds()
    assert f.available_cash < 100_000 - 100 * 100.05      # charges deducted too
    assert b.get_holdings()[0].quantity == 100
    assert b.place_order(OrderRequest("AAA", "SELL", 101)).status == "REJECTED"


def test_insufficient_cash_rejected(market):
    b, _, _ = market
    assert b.place_order(OrderRequest("BBB", "BUY", 100)).status == "REJECTED"


def test_limit_fills_only_when_traded_through(market):
    b, feed, _ = market
    res = b.place_order(OrderRequest("AAA", "BUY", 10, "LIMIT", price=98))
    assert res.status == "OPEN"
    feed.add_bars("AAA", [("2026-10-09 09:31", 99.5, 99.8, 98.5, 99.0)])
    b.process_orders()
    assert b.get_orders()[-1].status == "OPEN"            # low 98.5 never reached 98
    feed.add_bars("AAA", [("2026-10-09 09:32", 97.0, 97.5, 96.8, 97.2)])
    filled = b.process_orders()
    assert filled and filled[0].average_price == pytest.approx(97.0)   # gap-through gets the better open


def test_stop_market_triggers_on_low_and_fills_at_worse_price(market):
    b, feed, _ = market
    b.place_order(OrderRequest("AAA", "BUY", 50))
    res = b.place_order(OrderRequest("AAA", "SELL", 50, "SL-M", trigger_price=95))
    assert res.status == "OPEN"
    feed.add_bars("AAA", [("2026-10-09 09:31", 99, 99.5, 96, 96.5)])
    assert not b.process_orders()
    feed.add_bars("AAA", [("2026-10-09 09:32", 93, 94, 92, 93.5)])   # gaps below the trigger
    filled = b.process_orders()
    assert filled and filled[0].average_price == pytest.approx(93 * (1 - 0.0005))
    assert not b.get_holdings()


def test_orders_placed_while_closed_wait_for_next_session(market):
    b, feed, clock = market
    b.market_open = lambda: False
    clock["now"] = datetime(2026, 10, 9, 18, 0)
    res = b.place_order(OrderRequest("AAA", "BUY", 10))
    assert res.status == "OPEN"
    feed.add_bars("AAA", [("2026-10-12 09:15", 101.0, 102, 100.5, 101.5)])
    filled = b.process_orders()
    assert filled and filled[0].average_price == pytest.approx(101.0 * 1.0005)


def test_day_orders_expire(market):
    b, _, _ = market
    b.place_order(OrderRequest("AAA", "BUY", 10, "LIMIT", price=90))
    assert b.end_of_day()["expired"] == 1
    assert b.get_orders()[-1].status == "CANCELLED"


def test_guard_blocks_oversized_and_short(market):
    b, _, _ = market
    ex = OrderExecutor(b, RiskGuard(enforce_market_hours_for_paper=True))
    big = ex.submit(OrderRequest("BBB", "BUY", 30))      # 75k > 22% of equity
    assert big.status == "BLOCKED" and "equity" in big.message
    short = ex.submit(OrderRequest("AAA", "SELL", 10))
    assert short.status == "BLOCKED" and "short" in short.message


def test_fat_finger_limit(market):
    b, _, _ = market
    res = OrderExecutor(b).submit(OrderRequest("AAA", "BUY", 10, "LIMIT", price=120))
    assert res.status == "BLOCKED" and "fat-finger" in res.message


def test_kill_switch_is_close_only(market):
    b, _, _ = market
    ex = OrderExecutor(b)
    assert ex.submit(OrderRequest("AAA", "BUY", 50)).status == "COMPLETE"
    set_kill(True, "test")
    assert ex.submit(OrderRequest("AAA", "BUY", 10)).status == "BLOCKED"
    assert ex.submit(OrderRequest("AAA", "SELL", 50)).status == "COMPLETE"
    set_kill(False)


def test_rate_limiter_caps_below_sebi_threshold(isolated_paths):
    lim = OrderRateLimiter(per_second=50, per_day=1000)
    assert lim.per_second <= 9
    for _ in range(lim.per_second):
        assert lim.check()[0]
        lim.record()
    ok, msg = lim.check()
    assert not ok and "/sec" in msg


def test_live_broker_requires_interlock(market, monkeypatch):
    b, _, _ = market
    monkeypatch.setattr(type(b), "is_live", True)
    d = RiskGuard().check(OrderRequest("AAA", "BUY", 1), b, 100.0)
    assert not d.approved
    assert any("QD_LIVE_CONFIRM" in r for r in d.reasons)
    assert any("mode" in r for r in d.reasons)


def test_event_log_hash_chain_detects_tampering(isolated_paths):
    from quantdesk.store import db

    for i in range(5):
        db.log_event("paper", "test", f"event {i}", {"i": i})
    assert db.verify_chain()["ok"]
    db.execute("UPDATE events SET message='edited' WHERE id=3")
    v = db.verify_chain()
    assert not v["ok"] and v["broken_at_id"] == 3
