"""The autonomous trading agent.

One class, one code path. It is handed a ``Broker`` - ``PaperBroker`` for the evaluation period,
``GrowwBroker`` / ``ZerodhaBroker`` afterwards - and never checks which one it has. Every order
goes through the same ``OrderExecutor`` -> ``RiskGuard`` -> broker pipeline.

Daily cycle (IST):
    premarket   refresh universe + prices, screen ~2,300 stocks, deep-research the shortlist,
                Claude review, build today's plan (sells, buys, sizes, stops)
    open        confirm the market is trading, execute exits, then entries (limit orders, gap filter)
    tick        every minute: sync fills, keep a protective stop-loss order resting for every holding
    cancel      cancel entry orders that never filled
    eod         reconcile, raise trailing stops, snapshot NAV vs NIFTY, write the journal

Accounting is a virtual sub-account (inception capital + realised P&L of the agent's own trades),
so the agent never touches money or holdings it didn't create - important once it shares a real
Groww account with your personal portfolio.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime

import numpy as np
import pandas as pd

from quantdesk.backtest.costs import order_costs
from quantdesk.broker import Broker, OrderRequest, get_broker
from quantdesk.config import get_settings
from quantdesk.data.calendar import is_market_open, is_trading_day, now_ist
from quantdesk.data.panel import MarketPanel
from quantdesk.engine.executor import OrderExecutor
from quantdesk.risk.guard import RiskGuard
from quantdesk.store import db
from quantdesk.trader import policy
from quantdesk.trader.policy import Candidate, HoldingState

log = logging.getLogger("quantdesk.agent")

OPEN_STATES = ("OPEN", "PENDING", "UNKNOWN")


def _f(x):
    return None if x is None or (isinstance(x, float) and not np.isfinite(x)) else float(x)


def _veto(row) -> str | None:
    v = row.get("veto") if hasattr(row, "get") else None
    return v if isinstance(v, str) and v else None


class TradingAgent:
    def __init__(self, broker: Broker | None = None, confirm_fn=None):
        self.s = get_settings()
        self.cfg = self.s.agent
        self.broker = broker or get_broker()
        self.account = self.broker.name
        self.guard = RiskGuard(enforce_market_hours_for_paper=True)
        self.executor = OrderExecutor(self.broker, self.guard, confirm_fn=confirm_fn)
        self._inception()

    # ================================================================ bookkeeping
    def _inception(self) -> dict:
        key = f"inception:{self.account}"
        inc = db.kv_get(key)
        if inc is None:
            cap = self.cfg.initial_capital
            if self.account == "paper":
                cap = float(self.broker.get_funds().available_cash)
            inc = {"date": now_ist().date().isoformat(), "capital": cap, "ts": db.now()}
            db.kv_set(key, inc)
            self.log("inception", f"Agent account '{self.account}' started with ₹{cap:,.0f}", inc)
        return inc

    def today(self) -> str:
        return now_ist().date().isoformat()

    def log(self, kind: str, message: str, data: dict | None = None, level: str = "info") -> None:
        getattr(log, "warning" if level in ("warning", "error") else "info")("%s: %s", kind, message)
        db.log_event(self.account, kind, message, data, level)

    def managed(self) -> dict[str, dict]:
        return {r["symbol"]: r for r in db.query("SELECT * FROM positions WHERE account=?", (self.account,))}

    def snapshot(self, ltps: dict[str, float] | None = None) -> dict:
        inc = self._inception()
        man = self.managed()
        ltps = ltps if ltps is not None else (self.broker.get_ltp(list(man)) if man else {})
        realised = db.query_one("SELECT COALESCE(SUM(pnl),0) AS p FROM trades WHERE account=?", (self.account,))["p"]
        basis = sum(p["qty"] * p["entry_price"] for p in man.values())
        entry_costs = sum(order_costs(p["qty"] * p["entry_price"], "BUY", self.s.execution.product,
                                      self.s.costs.profile).total for p in man.values())
        virtual_cash = inc["capital"] + realised - basis - entry_costs
        try:
            broker_cash = float(self.broker.get_funds().available_cash)
        except Exception:
            broker_cash = virtual_cash
        positions = []
        value = 0.0
        for sym, p in man.items():
            px = ltps.get(sym, p["highest_close"] or p["entry_price"])
            mv = p["qty"] * px
            value += mv
            positions.append({"symbol": sym, "qty": p["qty"], "entry_price": p["entry_price"], "ltp": px,
                              "value": mv, "pnl": (px - p["entry_price"]) * p["qty"],
                              "pnl_pct": px / p["entry_price"] - 1, "stop": p["stop"], "entry_date": p["entry_date"],
                              "sector": p["sector"], "entry_score": p["entry_score"],
                              "risk_to_stop": (px - p["stop"]) * p["qty"] if p["stop"] else None})
        equity = virtual_cash + value
        return {"ts": db.now(), "account": self.account, "mode": self.s.execution.mode, "equity": equity,
                "cash": min(virtual_cash, broker_cash), "virtual_cash": virtual_cash, "broker_cash": broker_cash,
                "invested": value, "positions": sorted(positions, key=lambda x: -x["value"]),
                "initial_capital": inc["capital"], "inception": inc["date"], "realised_pnl": realised,
                "return_pct": (equity / inc["capital"] - 1) * 100}

    def _record_order(self, res, req: OrderRequest, purpose: str, decision_id: int | None = None) -> None:
        db.execute("INSERT OR REPLACE INTO agent_orders(order_id, account, ts, day, symbol, side, qty, order_type, price, "
                   "trigger_price, purpose, reason, decision_id, status, filled_qty, avg_price, message, updated) "
                   "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (res.order_id or f"NOID-{datetime.now().timestamp()}", self.account, db.now(), self.today(),
                    req.symbol, req.side, req.quantity, req.order_type, req.price, req.trigger_price, purpose,
                    req.reason, decision_id, res.status, res.filled_quantity, res.average_price, res.message, db.now()))

    def _submit(self, req: OrderRequest, purpose: str, decision_id: int | None = None, ltp: float | None = None):
        req.tag = self.s.execution.algo_tag
        req.product = self.s.execution.product
        res = self.executor.submit(req, ltp)
        self._record_order(res, req, purpose, decision_id)
        lvl = "info" if res.status not in ("BLOCKED", "REJECTED") else "warning"
        self.log("order", f"{purpose.upper()} {req.side} {req.quantity} {req.symbol} {req.order_type}"
                          f"{' @ ' + format(req.price, '.2f') if req.price else ''}"
                          f"{' trg ' + format(req.trigger_price, '.2f') if req.trigger_price else ''} -> {res.status}"
                          f"{' (' + res.message + ')' if res.message and res.status != 'COMPLETE' else ''}",
                 {"order_id": res.order_id, "reason": req.reason, "purpose": purpose}, lvl)
        return res

    def _open_agent_orders(self, symbol: str | None = None, purpose: str | None = None) -> list[dict]:
        sql = f"SELECT * FROM agent_orders WHERE account=? AND status IN {OPEN_STATES}"
        args: list = [self.account]
        if symbol:
            sql += " AND symbol=?"
            args.append(symbol)
        if purpose:
            sql += " AND purpose=?"
            args.append(purpose)
        return db.query(sql, tuple(args))

    def _cancel(self, order: dict, why: str) -> None:
        try:
            res = self.broker.cancel_order(order["order_id"])
            db.execute("UPDATE agent_orders SET status=?, message=?, updated=? WHERE order_id=? AND account=?",
                       (res.status if res.status != "OPEN" else "CANCELLED", why, db.now(), order["order_id"], self.account))
        except Exception as exc:
            self.log("cancel_failed", f"cancel {order['order_id']} failed: {exc}", level="warning")

    # ================================================================ phase: premarket
    def premarket(self, progress=None) -> dict:
        from quantdesk.data.universe import agent_universe
        from quantdesk.trader import research, reviewer
        from quantdesk.trader.screener import breadth, market_regime, screen

        day = self.today()
        db.set_phase(day, self.account, "premarket", "running")
        prog = progress or (lambda f, m: None)

        uni = agent_universe()
        man = self.managed()
        prog(0.05, f"updating prices for {len(uni)} stocks")
        panel = MarketPanel.load()
        upd = panel.update(list(uni["symbol"]) + list(man) + list(self.s.watchlist), self.s.universe.history_years)
        panel = panel.completed()

        prog(0.30, "screening")
        scr = screen(panel, uni)
        regime = market_regime(panel)
        regime["breadth"] = breadth(scr)
        shortlist = scr.shortlist(self.cfg.shortlist_size).index.tolist()
        to_research = list(dict.fromkeys(shortlist + list(man) + list(self.s.watchlist)))
        try:
            from quantdesk.data.universe import resolve_sectors

            for sym, sec in resolve_sectors(to_research, uni).items():
                if sym in scr.table.index:
                    scr.table.loc[sym, "sector"] = sec
        except Exception as exc:
            self.log("sectors", f"sector lookup failed: {exc}", level="warning")
        prog(0.35, f"deep research on {len(to_research)} stocks")
        deep = research.deep_research(to_research, panel, scr.table,
                                      progress=lambda f, m: prog(0.35 + 0.5 * f, m))

        # ---- candidates (eligible, researched, not vetoed)
        cands: list[Candidate] = []
        for sym in shortlist:
            if sym not in deep.index or deep.loc[sym, "final_score"] is None or pd.isna(deep.loc[sym, "final_score"]):
                continue
            row, d = scr.table.loc[sym], deep.loc[sym]
            if _veto(d):
                continue
            cands.append(Candidate(sym, float(d["final_score"]), float(row["close"]), float(row["atr"]),
                                   str(row["sector"]), d["rationale"],
                                   {"screen": float(d["screen_score"]), "deep": float(d["deep_score"])}))

        # ---- Claude review of the would-be buys
        review_note = "disabled"
        if self.cfg.llm_review and self.s.llm.enabled:
            top = sorted([c for c in cands if c.symbol not in man and c.score >= self.cfg.min_final_score],
                         key=lambda c: -c.score)[: self.cfg.max_positions * 2]
            proposals = [{"symbol": c.symbol, "name": scr.table.loc[c.symbol, "name"], "sector": c.sector,
                          "score": round(c.score, 3), "evidence": c.rationale,
                          "forecast": (deep.loc[c.symbol, "report"] or {}).get("forecast"),
                          "ml": (deep.loc[c.symbol, "report"] or {}).get("ml"),
                          "headlines": [h["title"] for h in ((deep.loc[c.symbol, "sentiment"] or {}).get("headlines") or [])[:8]]}
                         for c in top]
            prog(0.88, "Claude review")
            verdicts = reviewer.review(proposals)
            if verdicts is None:
                review_note = "skipped (no LLM credentials or API unavailable)"
            else:
                review_note = f"{sum(v['verdict'] == 'reject' for v in verdicts.values())} rejected of {len(verdicts)}"
                for c in cands:
                    v = verdicts.get(c.symbol)
                    if v:
                        c.data["review"] = v
                cands = [c for c in cands if (c.data.get("review") or {}).get("verdict") != "reject"]

        # ---- holdings state
        snap = self.snapshot({s_: float(panel.close[s_].dropna().iloc[-1]) for s_ in man if s_ in panel.close})
        holdings = []
        for sym, p in man.items():
            if sym not in panel.close.columns:
                continue
            c = panel.close[sym].dropna()
            d = deep.loc[sym] if sym in deep.index else None
            holdings.append(HoldingState(
                sym, p["qty"], p["entry_price"], int(np.busday_count(p["entry_date"], day)), float(c.iloc[-1]),
                p["stop"] or 0.0, p["sector"] or "Other",
                score=_f(d["final_score"]) if d is not None else None,
                above_sma200=bool(c.iloc[-1] > c.iloc[-200:].mean()), atr=p["atr"] or 0.0))

        actions = policy.plan_day(holdings, cands, snap["equity"], snap["cash"], regime["risk_on"], self.cfg, self.s.risk)

        # ---- persist
        n_liq = int(scr.table["liquid"].sum())
        summary = {"as_of": str(scr.as_of.date()), "funnel": {**scr.funnel, "researched": len(to_research),
                   "candidates": len(cands)}, "regime": regime, "data_update": upd, "llm_review": review_note,
                   "equity": snap["equity"], "cash": snap["cash"],
                   "plan": [a.to_dict() for a in actions if a.action != "SKIP"]}
        with db.tx() as con:
            con.execute("DELETE FROM candidates WHERE day=? AND account=?", (day, self.account))
            con.execute("DELETE FROM decisions WHERE day=? AND account=? AND status='planned'", (day, self.account))
            con.execute("INSERT OR REPLACE INTO scans(day, account, created, summary) VALUES(?,?,?,?)",
                        (day, self.account, db.now(), json.dumps(summary, default=str)))
            top_table = scr.table[scr.table["liquid"]].head(150)
            for sym, row in top_table.iterrows():
                d = deep.loc[sym] if sym in deep.index else None
                rev = next((c.data.get("review") for c in cands if c.symbol == sym), None)
                con.execute("INSERT OR REPLACE INTO candidates VALUES(?,?,?,?,?,?,?,?,?,?)", (
                    day, self.account, sym, "researched" if d is not None else ("eligible" if row["eligible"] else "screened"),
                    row["sector"], _f(row["screen_score"]), _f(d["deep_score"]) if d is not None else None,
                    _f(d["final_score"]) if d is not None else None,
                    (_veto(d) if d is not None and _veto(d) else (rev or {}).get("verdict")),
                    json.dumps({"rank": _f(row["rank"]), "close": _f(row["close"]), "mom_12_1": _f(row["mom_12_1"]),
                                "mom_6_1": _f(row["mom_6_1"]), "high_52w": _f(row["high_52w"]), "vol_63": _f(row["vol_63"]),
                                "rsi": _f(row["rsi"]), "traded_value_cr": _f(row["traded_value_cr"]),
                                "eligible": bool(row["eligible"]), "name": row["name"],
                                "rationale": d["rationale"] if d is not None else None,
                                "components": d["components"] if d is not None else None,
                                "review": rev}, default=str)))
            for sym in deep.index:  # holdings / watchlist researched but not in the top table
                if sym in top_table.index:
                    continue
                d = deep.loc[sym]
                con.execute("INSERT OR REPLACE INTO candidates VALUES(?,?,?,?,?,?,?,?,?,?)", (
                    day, self.account, sym, "holding" if sym in man else "watchlist", None, _f(d["screen_score"]),
                    _f(d["deep_score"]), _f(d["final_score"]), _veto(d),
                    json.dumps({"rationale": d.get("rationale"), "components": d.get("components")}, default=str)))
            for a in actions:
                con.execute("INSERT INTO decisions(ts, day, account, symbol, action, qty, price, score, reason, data, status) "
                            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                            (db.now(), day, self.account, a.symbol, a.action, a.qty, a.ref_price, a.score, a.reason,
                             json.dumps({"stop": a.stop, **a.data}, default=str),
                             "planned" if a.action != "SKIP" else "skipped"))
        # Full research reports for the UI's stock pages.
        db.kv_set(f"research:{day}", {sym: {"report": deep.loc[sym, "report"], "sentiment": deep.loc[sym, "sentiment"],
                                            "final_score": _f(deep.loc[sym, "final_score"])} for sym in deep.index})
        buys = [a for a in actions if a.action == "BUY"]
        sells = [a for a in actions if a.action == "SELL"]
        self.log("premarket", f"Screened {n_liq} liquid of {scr.funnel['universe']} stocks; researched {len(to_research)}; "
                              f"plan: {len(sells)} sell, {len(buys)} buy; regime {regime['state']}; review {review_note}",
                 {"buys": [a.symbol for a in buys], "sells": [a.symbol for a in sells]})
        db.kv_set(f"snapshot:{self.account}", snap)
        db.set_phase(day, self.account, "premarket", "done", {"buys": len(buys), "sells": len(sells)})
        prog(1.0, "done")
        return summary

    # ================================================================ phase: open
    def confirm_trading_day(self) -> str:
        """'trading' | 'holiday' | 'waiting'. Holiday = no index prints by 09:45 on a weekday."""
        day = self.today()
        st = db.kv_get(f"daystatus:{day}")
        if st in ("trading", "holiday"):
            return st
        if not is_trading_day():
            db.kv_set(f"daystatus:{day}", "holiday")
            return "holiday"
        from quantdesk.data.feed import default_feed

        bars = default_feed().bars(["NIFTY"], "1d").get("NIFTY")
        if bars is not None and len(bars) and bars.index[-1].date() == now_ist().date():
            db.kv_set(f"daystatus:{day}", "trading")
            return "trading"
        t = now_ist()
        if (t.hour, t.minute) >= (9, 45):
            db.kv_set(f"daystatus:{day}", "holiday")
            self.log("holiday", f"No NIFTY prints by {t:%H:%M}; treating {day} as a market holiday")
            return "holiday"
        return "waiting"

    def open_session(self) -> dict:
        day = self.today()
        db.set_phase(day, self.account, "open", "running")
        planned = db.query("SELECT * FROM decisions WHERE day=? AND account=? AND status='planned' ORDER BY id",
                           (day, self.account))
        held = {p.symbol: p for p in self.broker.portfolio()}
        man = self.managed()
        done = {"sold": 0, "bought": 0, "skipped": 0}

        for d in [x for x in planned if x["action"] == "SELL"]:
            sym = d["symbol"]
            if sym not in man or sym not in held:
                db.execute("UPDATE decisions SET status='void' WHERE id=?", (d["id"],))
                continue
            for o in self._open_agent_orders(sym, "stop"):
                self._cancel(o, "replaced by planned exit")
            qty = min(held[sym].quantity, man[sym]["qty"])
            res = self._submit(OrderRequest(sym, "SELL", qty, "MARKET", reason=d["reason"]), "exit", d["id"])
            db.execute("UPDATE decisions SET status=? WHERE id=?",
                       ("submitted" if res.status not in ("BLOCKED", "REJECTED") else "failed", d["id"]))
            done["sold"] += 1

        buys = [x for x in planned if x["action"] == "BUY"]
        if buys:
            snap = self.snapshot()
            cash = snap["cash"]
            ltps = self.broker.get_ltp([b["symbol"] for b in buys])
            for d in buys:
                sym, data = d["symbol"], json.loads(d["data"] or "{}")
                ltp = ltps.get(sym)
                if ltp is None:
                    self._skip(d, "no live price")
                    done["skipped"] += 1
                    continue
                gap = (ltp / d["price"] - 1) * 100
                if gap > self.cfg.max_gap_up_pct:
                    self._skip(d, f"gapped up {gap:.1f}% (> {self.cfg.max_gap_up_pct}%); not chasing")
                    done["skipped"] += 1
                    continue
                limit = policy.round_tick(ltp * (1 + self.cfg.entry_limit_buffer_pct / 100), down=False)
                qty = min(int(d["qty"]), int(max(cash - snap["equity"] * self.cfg.cash_buffer_pct / 100, 0) // limit))
                if qty * limit < self.cfg.min_position_value:
                    self._skip(d, f"not enough cash for a meaningful position (₹{qty * limit:,.0f})")
                    done["skipped"] += 1
                    continue
                res = self._submit(OrderRequest(sym, "BUY", qty, "LIMIT", price=limit, reason=d["reason"][:300]),
                                   "entry", d["id"], ltp)
                ok = res.status not in ("BLOCKED", "REJECTED", "DECLINED")
                db.execute("UPDATE decisions SET status=? WHERE id=?", ("submitted" if ok else "failed", d["id"]))
                if ok:
                    cash -= qty * limit
                    done["bought"] += 1
        db.set_phase(day, self.account, "open", "done", done)
        self.log("open", f"Open: {done['sold']} exits, {done['bought']} entries submitted, {done['skipped']} skipped", done)
        return done

    def _skip(self, d: dict, why: str) -> None:
        db.execute("UPDATE decisions SET status='skipped', reason=? WHERE id=?", (f"{d['reason']} | skipped: {why}", d["id"]))
        self.log("skip", f"{d['symbol']}: {why}")

    # ================================================================ phase: tick
    def tick(self) -> dict:
        if hasattr(self.broker, "process_orders"):
            self.broker.process_orders()      # paper exchange simulation; a live broker matches on the exchange
        changes = self.sync()
        if is_market_open() and self.confirm_trading_day() == "trading":
            self.ensure_stops()
        snap = self.snapshot()
        db.kv_set(f"snapshot:{self.account}", snap)
        t = now_ist()
        if is_market_open() and t.minute % 5 == 0:
            db.execute("INSERT OR REPLACE INTO nav_intraday(account, ts, equity) VALUES(?,?,?)",
                       (self.account, t.strftime("%Y-%m-%dT%H:%M"), snap["equity"]))
        return {"changes": changes, "equity": snap["equity"]}

    def sync(self) -> list[str]:
        """Reconcile our order/position records with the broker."""
        changes = []
        try:
            broker_orders = {o.order_id: o for o in self.broker.get_orders()}
        except Exception as exc:
            self.log("sync_failed", f"could not fetch orders: {exc}", level="warning")
            return changes
        for o in self._open_agent_orders():
            b = broker_orders.get(o["order_id"])
            if b and b.status != o["status"]:
                db.execute("UPDATE agent_orders SET status=?, filled_qty=?, avg_price=?, message=?, updated=? "
                           "WHERE order_id=? AND account=?", (b.status, b.filled_quantity, b.average_price, b.message,
                                                              db.now(), o["order_id"], self.account))
                if b.status == "COMPLETE":
                    changes.append(f"filled {o['purpose']} {o['side']} {o['qty']} {o['symbol']} @ {b.average_price:.2f}")
                    self.log("fill", f"{o['purpose'].upper()} {o['side']} {o['qty']} {o['symbol']} filled @ ₹{b.average_price:,.2f}",
                             {"order_id": o["order_id"], "reason": o["reason"]})

        pf = {p.symbol: p for p in self.broker.portfolio()}
        man = self.managed()
        today = self.today()
        # New positions from our own filled entries.
        for e in db.query("SELECT * FROM agent_orders WHERE account=? AND purpose='entry' AND status='COMPLETE' "
                          "AND day>=date('now','-3 day')", (self.account,)):
            sym = e["symbol"]
            if sym in man or sym not in pf:
                continue
            dec = db.query_one("SELECT * FROM decisions WHERE id=?", (e["decision_id"],)) if e["decision_id"] else None
            ddata = json.loads(dec["data"]) if dec and dec["data"] else {}
            atr = float(ddata.get("atr") or 0) or e["avg_price"] * 0.02
            stop = policy.initial_stop(e["avg_price"], atr, self.cfg)
            db.execute("INSERT OR REPLACE INTO positions VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                       (self.account, sym, pf[sym].quantity, today, e["avg_price"], stop, e["avg_price"], atr,
                        ddata.get("sector", "Other"), dec["score"] if dec else None,
                        json.dumps({"reason": e["reason"], "decision_id": e["decision_id"]}), db.now()))
            changes.append(f"opened {sym}")
            self.log("position_opened", f"Opened {sym}: {pf[sym].quantity} @ ₹{e['avg_price']:,.2f}, stop ₹{stop:,.2f}",
                     {"reason": e["reason"]})
        man = self.managed()
        for sym, m in man.items():
            p = pf.get(sym)
            if p is None or p.quantity <= 0:
                ex = db.query_one("SELECT * FROM agent_orders WHERE account=? AND symbol=? AND side='SELL' AND status='COMPLETE' "
                                  "ORDER BY updated DESC LIMIT 1", (self.account, sym))
                if ex:
                    px = ex["avg_price"]
                    why = ex["reason"] if ex["purpose"] != "stop" else (
                        "trailing stop (profit locked)" if px > m["entry_price"] else "stop-loss hit")
                else:
                    px, why = self.broker.get_ltp([sym]).get(sym, m["entry_price"]), "position closed outside the agent"
                self._close_trade(m, px, why)
                changes.append(f"closed {sym}")
            elif p.quantity != m["qty"] and not self._open_agent_orders(sym, "entry"):
                ratio = p.quantity / m["qty"]  # split / bonus
                db.execute("UPDATE positions SET qty=?, entry_price=entry_price/?, stop=stop/?, highest_close=highest_close/?, "
                           "atr=atr/?, updated=? WHERE account=? AND symbol=?",
                           (p.quantity, ratio, ratio, ratio, ratio, db.now(), self.account, sym))
                self.log("corporate_action", f"{sym}: quantity {m['qty']} -> {p.quantity}; prices adjusted x{1 / ratio:.4f}")
        return changes

    def _close_trade(self, m: dict, exit_px: float, why: str) -> None:
        qty, entry = m["qty"], m["entry_price"]
        prod, prof = self.s.execution.product, self.s.costs.profile
        costs = order_costs(qty * entry, "BUY", prod, prof).total + order_costs(qty * exit_px, "SELL", prod, prof).total
        pnl = (exit_px - entry) * qty - costs
        days = int(np.busday_count(m["entry_date"], self.today()))
        thesis = json.loads(m["thesis"] or "{}")
        with db.tx() as con:
            con.execute("INSERT INTO trades(account, symbol, sector, entry_date, exit_date, qty, entry_price, exit_price, pnl, "
                        "ret, costs, holding_days, entry_reason, exit_reason) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (self.account, m["symbol"], m["sector"], m["entry_date"], self.today(), qty, entry, exit_px, pnl,
                         pnl / (qty * entry), costs, days, thesis.get("reason"), why))
            con.execute("DELETE FROM positions WHERE account=? AND symbol=?", (self.account, m["symbol"]))
        for o in self._open_agent_orders(m["symbol"]):
            self._cancel(o, "position closed")
        self.log("position_closed", f"Closed {m['symbol']}: {qty} @ ₹{exit_px:,.2f}, P&L ₹{pnl:+,.0f} "
                                    f"({pnl / (qty * entry) * 100:+.1f}%) after {days} days - {why}",
                 {"pnl": pnl, "reason": why})

    def ensure_stops(self) -> None:
        """Keep exactly one protective SL-M sell resting for every managed holding (re-placed daily: DAY validity)."""
        pf = {p.symbol: p for p in self.broker.portfolio()}
        for sym, m in self.managed().items():
            if sym not in pf or not m["stop"]:
                continue
            if self._open_agent_orders(sym, "exit"):
                continue  # an exit is already working
            qty = min(pf[sym].quantity, m["qty"])
            stops = self._open_agent_orders(sym, "stop")
            good = [o for o in stops if abs((o["trigger_price"] or 0) - m["stop"]) < 0.01 and o["qty"] == qty]
            for o in stops:
                if o not in good:
                    self._cancel(o, "stop moved")
            if not good:
                self._submit(OrderRequest(sym, "SELL", qty, "SL-M", trigger_price=m["stop"],
                                          reason=f"protective stop ₹{m['stop']:,.2f}"), "stop")

    # ================================================================ phase: cancel / eod
    def cancel_unfilled_entries(self) -> int:
        n = 0
        for o in self._open_agent_orders(purpose="entry"):
            self._cancel(o, "entry not filled by cutoff")
            n += 1
        db.set_phase(self.today(), self.account, "cancel", "done", {"cancelled": n})
        if n:
            self.log("cancel", f"Cancelled {n} unfilled entry orders")
        return n

    def end_of_day(self) -> dict:
        from quantdesk.data.feed import default_feed

        day = self.today()
        db.set_phase(day, self.account, "eod", "running")
        info = {}
        if hasattr(self.broker, "process_orders"):
            self.broker.process_orders()
        if hasattr(self.broker, "end_of_day"):
            info["broker"] = self.broker.end_of_day()
        # Mark our DAY orders that the exchange expired.
        self.sync()
        for o in self._open_agent_orders():
            db.execute("UPDATE agent_orders SET status='EXPIRED', updated=? WHERE order_id=? AND account=?",
                       (db.now(), o["order_id"], self.account))
        man = self.managed()
        closes = default_feed().ltp(list(man) + ["NIFTY", "NIFTY500"])
        raised = []
        for sym, m in man.items():
            c = closes.get(sym)
            if c is None:
                continue
            hi = max(m["highest_close"] or c, c)
            new_stop = policy.trailing_stop(m["stop"] or 0, hi, m["atr"] or c * 0.02, self.cfg)
            if new_stop > (m["stop"] or 0) + 0.01:
                raised.append(f"{sym} {m['stop']:.2f}->{new_stop:.2f}")
            db.execute("UPDATE positions SET highest_close=?, stop=?, updated=? WHERE account=? AND symbol=?",
                       (hi, new_stop, db.now(), self.account, sym))
        snap = self.snapshot({k: v for k, v in closes.items() if k in man})
        db.kv_set(f"snapshot:{self.account}", snap)
        db.execute("INSERT OR REPLACE INTO nav VALUES(?,?,?,?,?,?,?,?)",
                   (self.account, day, snap["equity"], snap["cash"], snap["invested"], len(snap["positions"]),
                    closes.get("NIFTY"), closes.get("NIFTY500")))
        info.update({"equity": snap["equity"], "stops_raised": raised})
        self.log("eod", f"Close: equity ₹{snap['equity']:,.0f} ({snap['return_pct']:+.2f}% since inception), "
                        f"{len(snap['positions'])} positions; stops raised: {', '.join(raised) or 'none'}", info)
        db.set_phase(day, self.account, "eod", "done", info)
        return info
