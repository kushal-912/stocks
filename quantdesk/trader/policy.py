"""Portfolio policy: what to sell, what to buy, how much. Pure functions, no I/O.

The live agent, the paper agent and the historical backtest all call ``plan_day`` - so the
decision logic being evaluated on paper is literally the logic that would trade real money.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

from quantdesk.config import AgentCfg, RiskCfg


@dataclass
class HoldingState:
    symbol: str
    qty: int
    entry_price: float
    days_held: int
    last_close: float
    stop: float
    sector: str = "Other"
    score: float | None = None          # today's final conviction score
    above_sma200: bool = True
    atr: float = 0.0


@dataclass
class Candidate:
    symbol: str
    score: float                        # final conviction score
    close: float
    atr: float
    sector: str = "Other"
    rationale: str = ""
    data: dict = field(default_factory=dict)


@dataclass
class Action:
    action: str                         # BUY | SELL | SKIP
    symbol: str
    qty: int = 0
    ref_price: float = 0.0
    stop: float | None = None
    score: float | None = None
    reason: str = ""
    data: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def round_tick(px: float, tick: float = 0.05, down: bool = True) -> float:
    f = math.floor if down else math.ceil
    return round(f(px / tick) * tick, 2)


def initial_stop(entry: float, atr: float, cfg: AgentCfg) -> float:
    return round_tick(entry - cfg.stop_atr * atr)


def trailing_stop(current_stop: float, highest_close: float, atr: float, cfg: AgentCfg) -> float:
    """Stops only ever move up."""
    return max(current_stop, round_tick(highest_close - cfg.trail_atr * atr))


def size_position(equity: float, cash_available: float, exposure_room: float, price: float, atr: float,
                  cfg: AgentCfg, risk: RiskCfg) -> tuple[int, str]:
    target_value = min(equity / cfg.max_positions, equity * risk.max_position_pct / 100 * 0.98)
    value_cap = min(target_value, cash_available, exposure_room)
    qty_value = math.floor(max(value_cap, 0) / price) if price > 0 else 0
    stop_dist = cfg.stop_atr * atr
    qty_risk = math.floor(equity * risk.risk_per_trade_pct / 100 / stop_dist) if stop_dist > 0 else qty_value
    qty = min(qty_value, qty_risk)
    binding = "risk-per-trade" if qty_risk < qty_value else "allocation/cash"
    return max(qty, 0), binding


def plan_day(holdings: list[HoldingState], candidates: list[Candidate], equity: float, cash: float,
             risk_on: bool, cfg: AgentCfg, risk: RiskCfg) -> list[Action]:
    actions: list[Action] = []
    sells: dict[str, Action] = {}

    # ---------------- 1. exits decided on yesterday's close (stops are handled intraday by resting orders)
    for h in holdings:
        reason = None
        if h.days_held >= cfg.min_hold_days:
            if not h.above_sma200:
                reason = "trend broken: closed below the 200-day average"
            elif h.score is not None and h.score < cfg.exit_score:
                reason = f"conviction decayed to {h.score:+.2f} (exit below {cfg.exit_score:+.2f})"
        if reason is None and h.days_held >= cfg.max_hold_days and h.last_close < h.entry_price:
            reason = f"time stop: {h.days_held} days held and still below entry"
        if reason:
            sells[h.symbol] = Action("SELL", h.symbol, h.qty, h.last_close, score=h.score, reason=reason)

    remaining = [h for h in holdings if h.symbol not in sells]
    est_cash = cash + sum(a.qty * a.ref_price for a in sells.values())
    buffer = equity * cfg.cash_buffer_pct / 100
    max_exposure = equity * (1.0 if (risk_on or not cfg.regime_filter) else cfg.risk_off_max_exposure_pct / 100)
    invested = sum(h.qty * h.last_close for h in remaining)
    sector_count: dict[str, int] = {}
    for h in remaining:
        sector_count[h.sector] = sector_count.get(h.sector, 0) + 1
    held = {h.symbol for h in holdings}
    slots = cfg.max_positions - len(remaining)
    swaps = 0

    # ---------------- 2. entries (and at most N swaps when full)
    buys: list[Action] = []
    skipped: list[Action] = []
    for c in sorted(candidates, key=lambda c: c.score, reverse=True):
        if c.symbol in held or c.score < cfg.min_final_score:
            continue
        if not risk_on and cfg.regime_filter and invested >= max_exposure:
            skipped.append(Action("SKIP", c.symbol, score=c.score, reason="risk-off regime: exposure cap reached"))
            continue
        weakest = None
        if slots <= 0:
            swappable = [h for h in remaining if h.days_held >= cfg.min_hold_days and h.score is not None]
            weakest = min(swappable, key=lambda h: h.score, default=None)
            if weakest is None or swaps >= cfg.max_swaps_per_day or c.score < weakest.score + cfg.swap_margin:
                skipped.append(Action("SKIP", c.symbol, score=c.score, reason="portfolio full; not strong enough to replace a holding"))
                continue
        # Evaluate the buy as if the swap (if any) had happened; only commit when everything passes.
        freed_value = weakest.qty * weakest.last_close if weakest else 0.0
        sec_after = sector_count.get(c.sector, 0) - (1 if weakest and weakest.sector == c.sector else 0)
        if sec_after >= cfg.max_per_sector:
            skipped.append(Action("SKIP", c.symbol, score=c.score, reason=f"sector cap: already {cfg.max_per_sector} in {c.sector}"))
            continue
        qty, binding = size_position(equity, est_cash + freed_value - buffer, max_exposure - invested + freed_value,
                                     c.close, c.atr, cfg, risk)
        if qty * c.close < cfg.min_position_value:
            skipped.append(Action("SKIP", c.symbol, score=c.score,
                                  reason=f"position too small after sizing (₹{qty * c.close:,.0f} < ₹{cfg.min_position_value:,.0f})"))
            continue
        if weakest is not None:
            sells[weakest.symbol] = Action("SELL", weakest.symbol, weakest.qty, weakest.last_close, score=weakest.score,
                                           reason=f"replaced by {c.symbol} ({c.score:+.2f} vs {weakest.score:+.2f})")
            remaining.remove(weakest)
            est_cash += freed_value
            invested -= freed_value
            sector_count[weakest.sector] -= 1
            slots += 1
            swaps += 1
        stop = initial_stop(c.close, c.atr, cfg)
        buys.append(Action("BUY", c.symbol, qty, c.close, stop, c.score,
                           reason=c.rationale or f"conviction {c.score:+.2f}",
                           data={"sizing": binding, "sector": c.sector, "atr": c.atr, **c.data}))
        sector_count[c.sector] = sector_count.get(c.sector, 0) + 1
        est_cash -= qty * c.close
        invested += qty * c.close
        slots -= 1
        if slots <= 0 and swaps >= cfg.max_swaps_per_day:
            break

    actions = list(sells.values()) + buys + skipped[:15]
    return actions
