"""Order routing: request -> risk guard -> (optional human confirmation) -> broker -> audit log."""
from __future__ import annotations

import logging
from typing import Callable

from quantdesk.broker.base import Broker, OrderRequest, OrderResult
from quantdesk.config import get_settings
from quantdesk.risk.compliance import AuditLog
from quantdesk.risk.guard import RiskGuard

log = logging.getLogger(__name__)


class OrderExecutor:
    def __init__(self, broker: Broker, guard: RiskGuard | None = None, audit: AuditLog | None = None,
                 confirm_fn: Callable[[OrderRequest], bool] | None = None):
        self.broker = broker
        self.guard = guard or RiskGuard()
        self.audit = audit or AuditLog()
        self.confirm_fn = confirm_fn
        self.s = get_settings()

    def submit(self, req: OrderRequest, ltp: float | None = None) -> OrderResult:
        req.tag = req.tag or self.s.execution.algo_tag
        if ltp is None:
            ltp = self.broker.get_ltp([req.symbol]).get(req.symbol)
        self.audit.write("order_request", broker=self.broker.name, order=req.to_dict(), ltp=ltp)
        decision = self.guard.check(req, self.broker, ltp)
        if not decision.approved:
            self.audit.write("order_blocked", broker=self.broker.name, order=req.to_dict(), reasons=decision.reasons)
            log.warning("Blocked %s %s x%d: %s", req.side, req.symbol, req.quantity, "; ".join(decision.reasons))
            return OrderResult("", "BLOCKED", req.symbol, req.side, req.quantity, message="; ".join(decision.reasons))
        if self.broker.is_live and self.s.execution.require_manual_confirmation:
            if self.confirm_fn is None or not self.confirm_fn(req):
                self.audit.write("order_declined", broker=self.broker.name, order=req.to_dict())
                return OrderResult("", "DECLINED", req.symbol, req.side, req.quantity, message="not confirmed by user")
        if not self.guard.limiter.wait_for_slot():
            return OrderResult("", "BLOCKED", req.symbol, req.side, req.quantity, message="rate limit")
        res = self.broker.place_order(req)
        self.guard.record_order()
        self.audit.write("order_result", broker=self.broker.name, order=req.to_dict(), result=res.to_dict(),
                         warnings=decision.warnings)
        log.info("%s %s x%d -> %s %s", req.side, req.symbol, req.quantity, res.status, res.message)
        return res

    def flatten_all(self) -> list[OrderResult]:
        """Emergency exit: sell every long position at market (bypasses the kill switch by design)."""
        out = []
        for p in self.broker.portfolio():
            if p.quantity > 0:
                out.append(self.submit(OrderRequest(p.symbol, "SELL", p.quantity, "MARKET", product=p.product,
                                                    reason="flatten")))
        return out
