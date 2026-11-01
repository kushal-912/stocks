"""Historical replay of the agent's policy - with no knowledge of the future.

Uses the same ``screen()`` and ``plan_day()`` functions as the live agent.

How look-ahead is prevented (and tested in tests/test_backtest_causality.py):
* each morning's decisions are computed from ``panel.until(yesterday)`` - a copy of the data that
  physically ends at the previous close; the code asserts this on every step
* orders decided on day t's close execute at day t+1's open; stops trigger on t+1's low
* the universe is point-in-time: the top-N most-traded stocks *as of each day* (not today's index list)
* prices are split-adjusted only (no dividend back-adjustment, which uses later dividends)

What it cannot replay (documented, not hidden): the deep research layer (ML/forecasts are too slow
for hundreds of stocks x hundreds of days), news (no historical archive) and the Claude review.
Remaining bias: stocks that delisted before today are absent from NSE's current list.
"""
from __future__ import annotations

import logging
import math
from typing import Callable

import numpy as np
import pandas as pd

from quantdesk.analytics import risk as rk
from quantdesk.backtest.costs import order_costs
from quantdesk.config import get_settings
from quantdesk.data.panel import MarketPanel
from quantdesk.trader import policy
from quantdesk.trader.policy import Candidate, HoldingState
from quantdesk.trader.screener import market_regime, screen

log = logging.getLogger(__name__)


class BacktestCancelled(Exception):
    pass


def min_viable_capital() -> float:
    """Below this the policy cannot open a single position: the minimum position (charges make
    smaller ones uneconomic) would exceed the per-position concentration limit."""
    s = get_settings()
    return math.ceil(s.agent.min_position_value / (s.risk.max_position_pct / 100) / 1000) * 1000


def backtest_policy(panel: MarketPanel, universe: pd.DataFrame | None, start: str, end: str | None = None,
                    capital: float = 50_000, progress: Callable | None = None,
                    on_event: Callable[[dict], None] | None = None, should_stop: Callable[[], bool] | None = None,
                    resolve_sectors: bool = True) -> dict:
    s = get_settings()
    cfg, rcfg = s.agent, s.risk
    prof, slip = s.costs.profile, s.execution.slippage_bps / 1e4
    emit = on_event or (lambda e: None)
    dates = panel.close.loc[start:end].index if end else panel.close.loc[start:].index
    if len(dates) < 2:
        raise ValueError("Not enough trading days in that range.")
    all_idx = panel.close.index
    bench_series = panel.close["NIFTY"] if "NIFTY" in panel.close.columns else None
    b0 = float(bench_series.loc[:dates[0]].dropna().iloc[-1]) if bench_series is not None else None

    cash = capital
    book: dict[str, dict] = {}
    trades, curve = [], []
    costs_total = 0.0
    pending: list[policy.Action] = []
    last_close: dict[str, float] = {}
    sectors: dict[str, str] = {}
    last_note: dict[str, int] = {}

    def cost(value, side):
        return order_costs(value, side, "CNC", prof).total

    def bench_at(day) -> float | None:
        if bench_series is None:
            return None
        v = bench_series.loc[:day].dropna()
        return float(v.iloc[-1]) if len(v) else None

    def close_position(sym, pos, day, px, reason):
        nonlocal cash, costs_total
        fee = cost(pos["qty"] * px, "SELL")
        cash += pos["qty"] * px - fee
        costs_total += fee
        t = _trade(sym, pos, day, px, fee, reason)
        b_in, b_out = pos.get("bench_in"), bench_at(day)
        t["nifty_ret"] = (b_out / b_in - 1) if b_in and b_out else None
        t["edge"] = t["ret"] - t["nifty_ret"] if t["nifty_ret"] is not None else None
        trades.append(t)
        emit({"type": "sell", "day": str(day.date()), "symbol": sym, "qty": pos["qty"],
              "entry_day": str(pos["entry_day"].date()), "entry_price": pos["entry"], "exit_price": px,
              "pnl": t["pnl"], "ret": t["ret"], "nifty_ret": t["nifty_ret"], "holding_days": t["holding_days"],
              "reason": reason})

    emit({"type": "start", "start": str(dates[0].date()), "end": str(dates[-1].date()), "days": len(dates),
          "capital": capital, "universe": s.universe.source, "top_n": s.universe.top_n, "charges": prof})

    for k, day in enumerate(dates):
        if should_stop and should_stop():
            raise BacktestCancelled()
        i = all_idx.get_loc(day)
        o, l, c = (getattr(panel, f).iloc[i] for f in ("open", "low", "close"))

        # ---- 1. execute yesterday's plan at today's open (prices of today are only used for fills)
        for a in [x for x in pending if x.action == "SELL"]:
            pos = book.get(a.symbol)
            op = o.get(a.symbol, np.nan)
            if pos is None or np.isnan(op):
                continue
            del book[a.symbol]
            close_position(a.symbol, pos, day, float(op) * (1 - slip), a.reason)
        for a in [x for x in pending if x.action == "BUY"]:
            op = o.get(a.symbol, np.nan)
            if np.isnan(op) or a.symbol in book:
                continue
            if op > a.ref_price * (1 + cfg.max_gap_up_pct / 100):
                emit({"type": "skip", "day": str(day.date()), "symbol": a.symbol,
                      "reason": f"opened {(op / a.ref_price - 1) * 100:+.1f}% above yesterday's close - not chasing"})
                continue
            px = float(op) * (1 + slip)
            qty = min(a.qty, int(max(cash - cost(a.qty * px, "BUY"), 0) // px))
            if qty * px < cfg.min_position_value:
                emit({"type": "skip", "day": str(day.date()), "symbol": a.symbol, "reason": "not enough cash at the open"})
                continue
            fee = cost(qty * px, "BUY")
            cash -= qty * px + fee
            costs_total += fee
            atr = float(a.data.get("atr") or px * 0.02)
            book[a.symbol] = {"qty": qty, "entry": px, "entry_i": i, "entry_day": day, "stop": policy.initial_stop(px, atr, cfg),
                              "hi": px, "atr": atr, "sector": a.data.get("sector", "Other"), "fee_in": fee,
                              "reason": a.reason, "bench_in": bench_at(day)}
            emit({"type": "buy", "day": str(day.date()), "symbol": a.symbol, "qty": qty, "price": px, "value": qty * px,
                  "stop": book[a.symbol]["stop"], "score": a.score, "sector": book[a.symbol]["sector"], "reason": a.reason})
        pending = []

        # ---- 2. protective stops against today's range
        for sym in list(book):
            pos = book[sym]
            lo, op = l.get(sym, np.nan), o.get(sym, np.nan)
            if not np.isnan(lo) and lo <= pos["stop"]:
                px = float(min(op, pos["stop"]) if not np.isnan(op) else pos["stop"]) * (1 - slip)
                del book[sym]
                close_position(sym, pos, day, px, "trailing stop (profit locked)" if px > pos["entry"] else "stop-loss hit")

        # ---- 3. mark to market on today's close, trail stops
        for sym, pos in book.items():
            cl = c.get(sym, np.nan)
            if not np.isnan(cl):
                last_close[sym] = float(cl)
                pos["hi"] = max(pos["hi"], float(cl))
                pos["stop"] = policy.trailing_stop(pos["stop"], pos["hi"], pos["atr"], cfg)
        equity = cash + sum(p["qty"] * last_close.get(s_, p["entry"]) for s_, p in book.items())
        bv = bench_at(day)
        curve.append((day, equity, cash, len(book)))
        emit({"type": "day", "day": str(day.date()), "equity": equity, "cash": cash, "positions": len(book),
              "benchmark": (bv / b0 * capital) if bv and b0 else None})
        if k == len(dates) - 1:
            break

        # ---- 4. decide for tomorrow, seeing ONLY data up to today's close
        view = panel.until(day)
        assert view.close.index[-1] == day, "point-in-time view leaked future rows"
        scr = screen(view, universe)
        reg = market_regime(view)
        t = scr.table
        shortlist = scr.shortlist(cfg.shortlist_size)
        if resolve_sectors:
            unknown = [x for x in list(shortlist.index) if x not in sectors]
            if unknown and universe is not None:
                try:
                    from quantdesk.data.universe import resolve_sectors as _rs

                    sectors.update(_rs(unknown, universe))
                except Exception:
                    sectors.update({x: str(t.loc[x, "sector"]) for x in unknown})
        for sym, pos in book.items():
            if sym in t.index and not np.isnan(t.loc[sym, "atr"]):
                pos["atr"] = float(t.loc[sym, "atr"])
        cands = [Candidate(sym, float(r["screen_score"]), float(r["close"]), float(r["atr"]),
                           sectors.get(sym, str(r["sector"])),
                           f"#{int(r['rank'])} by momentum screen; 12-1m {r['mom_12_1'] * 100:+.0f}%, "
                           f"{r['high_52w'] * 100:.0f}% of 52w high")
                 for sym, r in shortlist.iterrows()]
        holds = []
        for sym, pos in book.items():
            r = t.loc[sym] if sym in t.index else None
            score = float(r["screen_score"]) if r is not None and pd.notna(r["screen_score"]) else -1.0
            holds.append(HoldingState(sym, pos["qty"], pos["entry"], i - pos["entry_i"], last_close.get(sym, pos["entry"]),
                                      pos["stop"], pos["sector"], score,
                                      bool(r is not None and r["close"] > r["sma200"]), pos["atr"]))
        pending = policy.plan_day(holds, cands, equity, cash, reg["risk_on"], cfg, rcfg)
        for a in pending:
            if a.action == "BUY":
                a.data.setdefault("atr", next(x.atr for x in cands if x.symbol == a.symbol))
        # Explain idle days (e.g. why nothing is being bought) without flooding the log.
        if not any(a.action == "BUY" for a in pending):
            skips = [a for a in pending if a.action == "SKIP"]
            reason = skips[0].reason if skips else ("no stock passed the screen" if not cands else None)
            if reason and len(book) < cfg.max_positions and k - last_note.get(reason, -99) >= 20:
                last_note[reason] = k
                emit({"type": "note", "day": str(day.date()), "message": f"No buys: {reason}"})
        if progress and k % 5 == 0:
            progress(k / len(dates), f"{day.date()} · equity ₹{equity:,.0f}")

    # ---- results
    eq = pd.Series([x[1] for x in curve], index=[x[0] for x in curve], name="agent")
    bench = None
    if bench_series is not None and b0:
        bench = (bench_series.reindex(eq.index).ffill() / b0 * capital).rename("NIFTY")
    tr = pd.DataFrame(trades)
    m = rk.summary(eq, bench)
    m["final_value"] = float(eq.iloc[-1])
    m["total_costs"] = costs_total
    m["n_trades"] = int(len(tr))
    if len(tr):
        m["win_rate"] = float((tr.pnl > 0).mean())
        m["avg_holding_days"] = float(tr.holding_days.mean())
        m["profit_factor"] = float(tr.loc[tr.pnl > 0, "pnl"].sum() / -tr.loc[tr.pnl <= 0, "pnl"].sum()) if (tr.pnl <= 0).any() else None
        m["realised_pnl"] = float(tr.pnl.sum())
        m["beat_nifty_pct"] = float((tr.edge > 0).mean()) if tr["edge"].notna().any() else None
    if bench is not None:
        m["benchmark_final"] = float(bench.iloc[-1])
        m["benchmark_return"] = float(bench.iloc[-1] / capital - 1)
        m["benchmark_max_drawdown"] = rk.max_drawdown(bench)
    open_pos = [{"symbol": s_, "qty": p["qty"], "entry_day": str(p["entry_day"].date()), "entry_price": p["entry"],
                 "last_price": last_close.get(s_, p["entry"]), "stop": p["stop"],
                 "unrealised": (last_close.get(s_, p["entry"]) - p["entry"]) * p["qty"],
                 "ret": last_close.get(s_, p["entry"]) / p["entry"] - 1} for s_, p in book.items()]
    m["unrealised_pnl"] = float(sum(p["unrealised"] for p in open_pos))
    emit({"type": "end", "metrics": m})
    return {"equity": eq, "benchmark": bench, "trades": tr, "metrics": m, "open_positions": open_pos,
            "positions": pd.Series([x[3] for x in curve], index=eq.index)}


def _trade(sym, pos, day, px, fee_out, reason) -> dict:
    pnl = (px - pos["entry"]) * pos["qty"] - pos["fee_in"] - fee_out
    return {"symbol": sym, "entry_date": pos["entry_day"], "exit_date": day, "qty": pos["qty"], "entry_price": pos["entry"],
            "exit_price": px, "pnl": pnl, "ret": pnl / (pos["entry"] * pos["qty"]), "costs": pos["fee_in"] + fee_out,
            "holding_days": int(np.busday_count(pos["entry_day"].date(), day.date())), "exit_reason": reason,
            "entry_reason": pos.get("reason"), "sector": pos["sector"]}
