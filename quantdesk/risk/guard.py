"""Pre-trade risk checks. Every order - paper or live, manual or automatic - passes through here."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date

from quantdesk.broker.base import Broker, OrderRequest
from quantdesk.config import env, get_settings
from quantdesk.data.calendar import is_market_open
from quantdesk.risk.compliance import OrderRateLimiter, is_killed, kill_reason, set_kill

LIVE_CONFIRM_PHRASE = "I_UNDERSTAND_THE_RISK"


@dataclass
class Decision:
    approved: bool
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        return ("APPROVED" if self.approved else "BLOCKED: " + "; ".join(self.reasons)) + \
            (f" (warnings: {'; '.join(self.warnings)})" if self.warnings else "")


class RiskGuard:
    def __init__(self, rate_limiter: OrderRateLimiter | None = None, enforce_market_hours_for_paper: bool = False):
        self.s = get_settings()
        self.limiter = rate_limiter or OrderRateLimiter()
        self.paper_hours = enforce_market_hours_for_paper
        self._day_path = self.s.path("data/live/day_start_equity.json")

    # ----- daily P&L baseline -----
    def day_start_equity(self, broker: Broker) -> float:
        today = date.today().isoformat()
        data = json.loads(self._day_path.read_text(encoding="utf-8")) if self._day_path.exists() else {}
        key = f"{broker.name}:{today}"
        if key not in data:
            data = {k: v for k, v in data.items() if k.endswith(today)}
            data[key] = broker.equity()
            self._day_path.write_text(json.dumps(data), encoding="utf-8")
        return float(data[key])

    def daily_pnl_pct(self, broker: Broker) -> float:
        start = self.day_start_equity(broker)
        return (broker.equity() / start - 1) * 100 if start > 0 else 0.0

    # ----- main check -----
    def check(self, req: OrderRequest, broker: Broker, ltp: float | None = None) -> Decision:
        r, warn = [], []
        risk, ex = self.s.risk, self.s.execution
        reducing = req.side == "SELL"

        if is_killed() and req.side == "BUY":
            # Close-only mode: no new risk, but exits and protective stops always go through.
            r.append(f"kill switch is ON ({kill_reason() or 'manual'}) - close-only mode")

        if broker.is_live:
            if ex.mode != "live":
                r.append("execution.mode is not 'live'")
            if env("QD_LIVE_CONFIRM") != LIVE_CONFIRM_PHRASE:
                r.append(f"set QD_LIVE_CONFIRM={LIVE_CONFIRM_PHRASE} in .env to enable live orders")
            if not is_market_open():
                r.append("market is closed")
        elif self.paper_hours and not is_market_open():
            r.append("market is closed")

        if req.quantity <= 0:
            r.append("quantity must be positive")
        if req.product == "CNC" and req.side == "SELL" and not risk.allow_short:
            held = sum(p.quantity for p in broker.portfolio() if p.symbol == req.symbol)
            if req.quantity > held:
                r.append(f"sell qty {req.quantity} exceeds holding {held} (no short selling in delivery)")

        if ltp and req.price and req.order_type in ("LIMIT", "SL"):
            dev = abs(req.price / ltp - 1) * 100
            if dev > risk.fat_finger_pct:
                r.append(f"limit price {req.price} is {dev:.1f}% from LTP {ltp} (fat-finger limit {risk.fat_finger_pct}%)")

        if not reducing:
            px = req.price or ltp
            if px is None:
                r.append("no reference price for exposure checks")
            else:
                equity = broker.equity()
                book = broker.portfolio()
                value = req.quantity * px
                existing = sum(p.market_value for p in book if p.symbol == req.symbol)
                if (existing + value) > equity * risk.max_position_pct / 100 * 1.001:
                    r.append(f"position would be {(existing + value) / equity * 100:.1f}% of equity "
                             f"(max {risk.max_position_pct}%)")
                gross = sum(abs(p.market_value) for p in book) + value
                if gross > equity * risk.max_gross_exposure_pct / 100 * 1.001:
                    r.append(f"gross exposure would be {gross / equity * 100:.0f}% (max {risk.max_gross_exposure_pct}%)")
                symbols = {p.symbol for p in book}
                if req.symbol not in symbols and len(symbols) >= risk.max_open_positions:
                    r.append(f"max open positions ({risk.max_open_positions}) reached")
                pnl = self.daily_pnl_pct(broker)
                if pnl <= -risk.max_daily_loss_pct:
                    set_kill(True, f"daily loss {pnl:.2f}% breached limit {risk.max_daily_loss_pct}%")
                    r.append(f"daily loss limit hit ({pnl:.2f}%) - kill switch engaged")

        ok, msg = self.limiter.check()
        if not ok:
            r.append(msg)
        if req.order_type == "MARKET" and broker.is_live:
            warn.append("market order: consider LIMIT orders to control slippage")
        return Decision(not r, r, warn)

    def record_order(self) -> None:
        self.limiter.record()
