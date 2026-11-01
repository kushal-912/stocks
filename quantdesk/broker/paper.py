"""Paper broker: a simulated exchange + broker account backed by SQLite.

It is deliberately strict so paper results transfer to live trading:
* orders only fill during market hours, against real 1-minute bars that trade *after* the order
* MARKET: next available price (+ slippage); placed while closed -> next session's opening print
* LIMIT: fills only if the market trades through the limit (gap-throughs get the better open)
* SL-M: triggers when a bar's low/high crosses the trigger; fills at the worse of trigger and open (+ slippage)
* DAY orders expire at the close; charges follow the configured broker profile (Groww by default)
* cash is checked at fill time; no short selling in delivery
* corporate actions (splits, bonuses, dividends) are applied to holdings at end of day
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Callable

import pandas as pd

from quantdesk.backtest.costs import order_costs
from quantdesk.broker.base import Broker, BrokerError, Funds, OrderRequest, OrderResult, Position
from quantdesk.config import get_settings
from quantdesk.data.calendar import is_market_open
from quantdesk.store import db

log = logging.getLogger(__name__)


class PaperBroker(Broker):
    name = "paper"
    is_live = False

    def __init__(self, feed=None, clock: Callable[[], datetime] | None = None,
                 market_open: Callable[[], bool] | None = None):
        from quantdesk.data.feed import default_feed

        s = get_settings()
        self.feed = feed or default_feed()
        self.clock = clock or datetime.now
        self.market_open = market_open or is_market_open
        self.slippage = s.execution.slippage_bps / 1e4
        self.cost_profile = s.costs.profile
        if db.query_one("SELECT id FROM paper_account WHERE id=1") is None:
            self._create(s.agent.initial_capital)

    # ------------------------------------------------------------ account lifecycle
    def _create(self, capital: float) -> None:
        db.execute("INSERT OR REPLACE INTO paper_account(id, created, initial_capital, cash, realised_pnl, total_costs, "
                   "dividends) VALUES(1,?,?,?,0,0,0)", (db.now(), capital, capital))

    def reset(self, capital: float | None = None) -> None:
        capital = capital or get_settings().agent.initial_capital
        with db.tx() as con:
            for t in ("paper_positions", "paper_orders", "paper_fills", "paper_corporate_actions"):
                con.execute(f"DELETE FROM {t}")
            con.execute("DELETE FROM paper_account")
        self._create(capital)

    def account(self) -> dict:
        return db.query_one("SELECT * FROM paper_account WHERE id=1")

    # ------------------------------------------------------------ prices
    def get_ltp(self, symbols: list[str]) -> dict[str, float]:
        return self.feed.ltp(list(symbols))

    # ------------------------------------------------------------ orders
    def place_order(self, req: OrderRequest) -> OrderResult:
        if req.quantity <= 0:
            raise BrokerError("Quantity must be positive.")
        if req.order_type in ("LIMIT", "SL") and not req.price:
            raise BrokerError("LIMIT/SL orders need a price.")
        if req.order_type in ("SL", "SL-M") and not req.trigger_price:
            raise BrokerError("SL/SL-M orders need a trigger price.")
        oid = "P" + uuid.uuid4().hex[:12].upper()
        ts = self.clock().isoformat(timespec="seconds")
        db.execute("INSERT INTO paper_orders(order_id, ts, symbol, side, qty, order_type, price, trigger_price, product, "
                   "validity, tag, status, filled_qty, avg_price, message, triggered, checked_until, updated) "
                   "VALUES(?,?,?,?,?,?,?,?,?,?,?, 'OPEN', 0, 0, 'accepted', 0, ?, ?)",
                   (oid, ts, req.symbol, req.side, int(req.quantity), req.order_type, req.price, req.trigger_price,
                    req.product, req.validity, req.tag, ts, ts))
        if self.market_open():
            self._try_immediate(oid)
        return self._result(oid)

    def _order(self, oid: str) -> dict:
        return db.query_one("SELECT * FROM paper_orders WHERE order_id=?", (oid,))

    def _result(self, oid: str) -> OrderResult:
        o = self._order(oid)
        return OrderResult(o["order_id"], o["status"], o["symbol"], o["side"], o["qty"], o["filled_qty"] or 0,
                           o["avg_price"] or 0.0, o["message"] or "", o["ts"], raw=o)

    def _try_immediate(self, oid: str) -> None:
        o = self._order(oid)
        ltp = self.get_ltp([o["symbol"]]).get(o["symbol"])
        if ltp is None:
            return
        buy = o["side"] == "BUY"
        typ = o["order_type"]
        if typ == "MARKET":
            self._fill(o, ltp * (1 + self.slippage if buy else 1 - self.slippage))
        elif typ == "LIMIT":
            if (buy and ltp <= o["price"]) or (not buy and ltp >= o["price"]):
                self._fill(o, ltp)
        elif typ in ("SL-M", "SL"):
            if (buy and ltp >= o["trigger_price"]) or (not buy and ltp <= o["trigger_price"]):
                if typ == "SL-M":
                    self._fill(o, ltp * (1 + self.slippage if buy else 1 - self.slippage))
                else:
                    db.execute("UPDATE paper_orders SET triggered=1 WHERE order_id=?", (oid,))

    def process_orders(self) -> list[OrderResult]:
        """Match every open order against the 1-minute bars printed since it was last checked."""
        open_orders = db.query("SELECT * FROM paper_orders WHERE status='OPEN' ORDER BY ts")
        filled = []
        for o in open_orders:
            since = datetime.fromisoformat(o["checked_until"] or o["ts"])
            bars = self.feed.bars_since(o["symbol"], since)
            if bars.empty:
                continue
            px = self._match(o, bars)
            last_bar = bars.index[-1].isoformat(timespec="seconds")
            if px is not None:
                if self._fill(o, px):
                    filled.append(self._result(o["order_id"]))
            db.execute("UPDATE paper_orders SET checked_until=? WHERE order_id=? AND status='OPEN'",
                       (last_bar, o["order_id"]))
        return filled

    def _match(self, o: dict, bars: pd.DataFrame) -> float | None:
        buy = o["side"] == "BUY"
        typ, lim, trig = o["order_type"], o["price"], o["trigger_price"]
        triggered = bool(o["triggered"])
        slip = self.slippage
        for _, b in bars.iterrows():
            if typ == "MARKET":
                return float(b["open"]) * (1 + slip if buy else 1 - slip)
            if typ == "LIMIT" or (typ == "SL" and triggered):
                if buy and b["low"] <= lim:
                    return float(min(lim, b["open"]))
                if not buy and b["high"] >= lim:
                    return float(max(lim, b["open"]))
            elif typ in ("SL-M", "SL"):
                hit = (b["high"] >= trig) if buy else (b["low"] <= trig)
                if hit:
                    if typ == "SL":
                        triggered = True
                        db.execute("UPDATE paper_orders SET triggered=1 WHERE order_id=?", (o["order_id"],))
                        continue
                    base = max(trig, b["open"]) if buy else min(trig, b["open"])
                    return float(base) * (1 + slip if buy else 1 - slip)
        return None

    def _fill(self, o: dict, px: float) -> bool:
        qty, sym, buy = int(o["qty"]), o["symbol"], o["side"] == "BUY"
        value = qty * px
        costs = order_costs(value, o["side"], o["product"], self.cost_profile).total
        ts = self.clock().isoformat(timespec="seconds")
        with db.tx() as con:
            acct = con.execute("SELECT * FROM paper_account WHERE id=1").fetchone()
            pos = con.execute("SELECT * FROM paper_positions WHERE symbol=?", (sym,)).fetchone()
            held = pos["qty"] if pos else 0
            if buy and acct["cash"] < value + costs:
                con.execute("UPDATE paper_orders SET status='REJECTED', message=?, updated=? WHERE order_id=?",
                            (f"insufficient funds: need {value + costs:,.0f}, have {acct['cash']:,.0f}", ts, o["order_id"]))
                return False
            if not buy and held < qty:
                con.execute("UPDATE paper_orders SET status='REJECTED', message=?, updated=? WHERE order_id=?",
                            (f"cannot sell {qty}, holding {held}", ts, o["order_id"]))
                return False
            realised = 0.0
            if buy:
                new_qty = held + qty
                avg = ((pos["avg_price"] * held if pos else 0) + value) / new_qty
                con.execute("INSERT INTO paper_positions(symbol, qty, avg_price, product, opened) VALUES(?,?,?,?,?) "
                            "ON CONFLICT(symbol) DO UPDATE SET qty=excluded.qty, avg_price=excluded.avg_price",
                            (sym, new_qty, avg, o["product"], ts))
                cash = acct["cash"] - value - costs
            else:
                realised = (px - pos["avg_price"]) * qty
                if held - qty == 0:
                    con.execute("DELETE FROM paper_positions WHERE symbol=?", (sym,))
                else:
                    con.execute("UPDATE paper_positions SET qty=? WHERE symbol=?", (held - qty, sym))
                cash = acct["cash"] + value - costs
            con.execute("UPDATE paper_account SET cash=?, realised_pnl=realised_pnl+?, total_costs=total_costs+? WHERE id=1",
                        (cash, realised - costs, costs))
            con.execute("UPDATE paper_orders SET status='COMPLETE', filled_qty=?, avg_price=?, message='filled', updated=? "
                        "WHERE order_id=?", (qty, round(px, 4), ts, o["order_id"]))
            con.execute("INSERT INTO paper_fills(order_id, ts, symbol, side, qty, price, costs, realised_pnl) "
                        "VALUES(?,?,?,?,?,?,?,?)", (o["order_id"], ts, sym, o["side"], qty, px, costs, realised))
        return True

    def cancel_order(self, order_id: str) -> OrderResult:
        n = db.query_one("SELECT status FROM paper_orders WHERE order_id=?", (order_id,))
        if not n:
            raise BrokerError(f"No order {order_id}")
        if n["status"] == "OPEN":
            db.execute("UPDATE paper_orders SET status='CANCELLED', message='cancelled', updated=? WHERE order_id=?",
                       (db.now(), order_id))
        return self._result(order_id)

    def end_of_day(self) -> dict:
        """Expire DAY orders and apply corporate actions to holdings."""
        n = db.query_one("SELECT COUNT(*) AS n FROM paper_orders WHERE status='OPEN' AND validity='DAY'")["n"]
        db.execute("UPDATE paper_orders SET status='CANCELLED', message='DAY validity expired', updated=? "
                   "WHERE status='OPEN' AND validity='DAY'", (db.now(),))
        return {"expired": n, "corporate_actions": self.apply_corporate_actions()}

    def apply_corporate_actions(self) -> list[dict]:
        import yfinance as yf

        applied = []
        acct = self.account()
        since = pd.Timestamp(acct["created"][:10])
        for p in db.query("SELECT * FROM paper_positions"):
            try:
                acts = yf.Ticker(p["symbol"] + ".NS").actions
            except Exception:
                continue
            if acts is None or acts.empty:
                continue
            idx = pd.to_datetime(acts.index)
            acts.index = idx.tz_localize(None) if idx.tz is not None else idx
            opened = pd.Timestamp(p["opened"][:10])
            for ex, row in acts[acts.index > max(since, opened)].iterrows():
                for kind, col in (("dividend", "Dividends"), ("split", "Stock Splits")):
                    val = float(row.get(col, 0) or 0)
                    if val <= 0 or db.query_one("SELECT 1 FROM paper_corporate_actions WHERE symbol=? AND ex_date=? AND kind=?",
                                                (p["symbol"], ex.date().isoformat(), kind)):
                        continue
                    with db.tx() as con:
                        if kind == "dividend":
                            amt = val * p["qty"]
                            con.execute("UPDATE paper_account SET cash=cash+?, dividends=dividends+? WHERE id=1", (amt, amt))
                        else:
                            con.execute("UPDATE paper_positions SET qty=?, avg_price=? WHERE symbol=?",
                                        (int(round(p["qty"] * val)), p["avg_price"] / val, p["symbol"]))
                        con.execute("INSERT INTO paper_corporate_actions VALUES(?,?,?,?,?)",
                                    (p["symbol"], ex.date().isoformat(), kind, val, db.now()))
                    applied.append({"symbol": p["symbol"], "kind": kind, "value": val, "ex_date": str(ex.date())})
        return applied

    # ------------------------------------------------------------ account views
    def get_orders(self) -> list[OrderResult]:
        rows = db.query("SELECT * FROM paper_orders WHERE status='OPEN' OR ts >= date('now','-5 day') ORDER BY ts")
        return [OrderResult(o["order_id"], o["status"], o["symbol"], o["side"], o["qty"], o["filled_qty"] or 0,
                            o["avg_price"] or 0.0, o["message"] or "", o["ts"], raw=o) for o in rows]

    def get_positions(self) -> list[Position]:
        return []  # delivery-only account: everything is a holding

    def get_holdings(self, with_prices: bool = True) -> list[Position]:
        rows = db.query("SELECT * FROM paper_positions ORDER BY symbol")
        ltps = self.get_ltp([r["symbol"] for r in rows]) if (with_prices and rows) else {}
        out = []
        for r in rows:
            last = ltps.get(r["symbol"], r["avg_price"])
            out.append(Position(r["symbol"], r["qty"], r["avg_price"], last, (last - r["avg_price"]) * r["qty"], r["product"]))
        return out

    def get_funds(self) -> Funds:
        a = self.account()
        mv = sum(h.market_value for h in self.get_holdings())
        return Funds(a["cash"], 0.0, a["cash"] + mv, {k: a[k] for k in a.keys()})
