"""Groww Trade API adapter (``pip install growwapi pyotp``). Requires an active Trading API subscription.

Authentication (tokens expire daily, ~6 AM):
    * API key + secret: approve the key on the Groww Cloud "API Keys" page each day, then
      ``qd broker login groww`` (uses GROWW_API_KEY / GROWW_API_SECRET).
    * TOTP: set GROWW_TOTP_TOKEN and GROWW_TOTP_SECRET; login is then fully scripted.
Groww's documented limits: orders 10/sec & 250/min, live data 10/sec, non-trading 20/sec.
"""
from __future__ import annotations

import random
import string
import time

from quantdesk.broker.base import Broker, BrokerError, Funds, OrderRequest, OrderResult, Position
from quantdesk.broker.session import load_token, save_token
from quantdesk.config import env

_STATUS_MAP = {"EXECUTED": "COMPLETE", "COMPLETED": "COMPLETE", "REJECTED": "REJECTED", "CANCELLED": "CANCELLED",
               "OPEN": "OPEN", "NEW": "OPEN", "ACKED": "OPEN", "TRIGGER_PENDING": "OPEN", "APPROVED": "OPEN"}


def _ref_id(tag: str) -> str:
    """order_reference_id: 8-20 alphanumeric characters, unique per order."""
    base36 = ""
    n = int(time.time() * 1000)
    while n:
        n, r = divmod(n, 36)
        base36 = (string.digits + string.ascii_uppercase)[r] + base36
    return (tag[:8] + base36 + "".join(random.choices(string.ascii_uppercase, k=2)))[:20]


class GrowwBroker(Broker):
    name = "groww"
    is_live = True

    def __init__(self):
        try:
            from growwapi import GrowwAPI
        except ImportError as exc:
            raise BrokerError("Install the Groww SDK: pip install growwapi pyotp") from exc
        self._GrowwAPI = GrowwAPI
        self.g = None

    def login(self) -> None:
        G = self._GrowwAPI
        if env("GROWW_TOTP_TOKEN") and env("GROWW_TOTP_SECRET"):
            import pyotp

            token = G.get_access_token(api_key=env("GROWW_TOTP_TOKEN"), totp=pyotp.TOTP(env("GROWW_TOTP_SECRET")).now())
        elif env("GROWW_API_KEY") and env("GROWW_API_SECRET"):
            token = G.get_access_token(api_key=env("GROWW_API_KEY"), secret=env("GROWW_API_SECRET"))
        else:
            raise BrokerError("Set GROWW_API_KEY/GROWW_API_SECRET or GROWW_TOTP_TOKEN/GROWW_TOTP_SECRET in .env")
        save_token(self.name, token)
        self.g = G(token)

    def connect(self) -> None:
        token = load_token(self.name)
        if token:
            self.g = self._GrowwAPI(token)
        else:
            self.login()

    def _c(self, name: str, fallback: str):
        return getattr(self.g, name, fallback)

    def get_ltp(self, symbols: list[str]) -> dict[str, float]:
        out = {}
        for i in range(0, len(symbols), 50):  # API limit: 50 instruments per call
            keys = tuple(f"NSE_{s}" for s in symbols[i:i + 50])
            data = self.g.get_ltp(segment=self._c("SEGMENT_CASH", "CASH"), exchange_trading_symbols=keys)
            for k, v in (data or {}).items():
                out[k.split("_", 1)[1]] = float(v["ltp"] if isinstance(v, dict) else v)
        return out

    def place_order(self, req: OrderRequest) -> OrderResult:
        otype = {"MARKET": self._c("ORDER_TYPE_MARKET", "MARKET"), "LIMIT": self._c("ORDER_TYPE_LIMIT", "LIMIT"),
                 "SL": self._c("ORDER_TYPE_STOP_LOSS", "SL"),
                 "SL-M": self._c("ORDER_TYPE_STOP_LOSS_MARKET", "SL_M")}[req.order_type]
        params = dict(
            trading_symbol=req.symbol, quantity=int(req.quantity), validity=self._c("VALIDITY_DAY", "DAY"),
            exchange=self._c("EXCHANGE_NSE", "NSE") if req.exchange == "NSE" else self._c("EXCHANGE_BSE", "BSE"),
            segment=self._c("SEGMENT_CASH", "CASH"),
            product=self._c("PRODUCT_CNC", "CNC") if req.product == "CNC" else self._c("PRODUCT_MIS", "MIS"),
            order_type=otype,
            transaction_type=self._c("TRANSACTION_TYPE_BUY", "BUY") if req.side == "BUY" else self._c("TRANSACTION_TYPE_SELL", "SELL"),
            order_reference_id=_ref_id(req.tag),
        )
        if req.price is not None and req.order_type in ("LIMIT", "SL"):
            params["price"] = req.price
        if req.trigger_price is not None and req.order_type in ("SL", "SL-M"):
            params["trigger_price"] = req.trigger_price
        try:
            resp = self.g.place_order(**params)
        except Exception as exc:
            return OrderResult("", "REJECTED", req.symbol, req.side, req.quantity, message=str(exc))
        status = _STATUS_MAP.get(str(resp.get("order_status", "")).upper(), "OPEN")
        return OrderResult(str(resp.get("groww_order_id", "")), status, req.symbol, req.side, req.quantity,
                           message=resp.get("remark", "") or "submitted", raw=resp)

    def cancel_order(self, order_id: str) -> OrderResult:
        resp = self.g.cancel_order(segment=self._c("SEGMENT_CASH", "CASH"), groww_order_id=order_id)
        return OrderResult(order_id, _STATUS_MAP.get(str(resp.get("order_status", "")).upper(), "CANCELLED"), raw=resp)

    def get_orders(self) -> list[OrderResult]:
        resp = self.g.get_order_list(segment=self._c("SEGMENT_CASH", "CASH"))
        return [OrderResult(str(o.get("groww_order_id")), _STATUS_MAP.get(str(o.get("order_status", "")).upper(), "UNKNOWN"),
                            o.get("trading_symbol", ""), o.get("transaction_type", ""), int(o.get("quantity") or 0),
                            int(o.get("filled_quantity") or 0), float(o.get("average_fill_price") or 0),
                            o.get("remark") or "", raw=o)
                for o in (resp or {}).get("order_list", [])]

    def get_positions(self) -> list[Position]:
        resp = self.g.get_positions_for_user(segment=self._c("SEGMENT_CASH", "CASH"))
        return [Position(p["trading_symbol"], int(p.get("quantity") or 0), float(p.get("net_price") or 0), 0.0,
                         float(p.get("realised_pnl") or 0), p.get("product", "MIS"))
                for p in (resp or {}).get("positions", []) if p.get("product") != "CNC"]

    def get_holdings(self) -> list[Position]:
        resp = self.g.get_holdings_for_user()
        hold = [Position(h["trading_symbol"], int(float(h.get("quantity") or 0)), float(h.get("average_price") or 0))
                for h in (resp or {}).get("holdings", [])]
        if hold:
            ltp = self.get_ltp([h.symbol for h in hold])
            for h in hold:
                h.last_price = ltp.get(h.symbol, h.average_price)
                h.pnl = (h.last_price - h.average_price) * h.quantity
        return hold

    def get_funds(self) -> Funds:
        m = self.g.get_available_margin_details() or {}
        eq = m.get("equity_margin_details", {})
        return Funds(float(eq.get("cnc_balance_available", m.get("clear_cash", 0.0))),
                     float(m.get("net_margin_used", 0.0)), raw=m)
