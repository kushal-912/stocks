"""Zerodha Kite Connect adapter (``pip install kiteconnect``).

Daily login flow (Kite access tokens expire every morning):
    1. ``qd broker login zerodha`` prints the Kite login URL.
    2. Log in in the browser; you are redirected to your app's redirect URL with ``request_token=...``.
    3. Paste that URL/token back; the access token is saved for the day under data/session/.
Register the static IP of the machine running QuantDesk in the Kite developer console.
"""
from __future__ import annotations

from quantdesk.broker.base import Broker, BrokerError, Funds, OrderRequest, OrderResult, Position
from quantdesk.broker.session import load_token, save_token
from quantdesk.config import env

_STATUS = {"COMPLETE": "COMPLETE", "REJECTED": "REJECTED", "CANCELLED": "CANCELLED", "OPEN": "OPEN",
           "TRIGGER PENDING": "OPEN", "AMO REQ RECEIVED": "PENDING"}


class ZerodhaBroker(Broker):
    name = "zerodha"
    is_live = True

    def __init__(self):
        try:
            from kiteconnect import KiteConnect
        except ImportError as exc:
            raise BrokerError("Install the Zerodha SDK: pip install kiteconnect") from exc
        self.api_key = env("KITE_API_KEY")
        self.api_secret = env("KITE_API_SECRET")
        if not self.api_key:
            raise BrokerError("Set KITE_API_KEY and KITE_API_SECRET in .env")
        self.kite = KiteConnect(api_key=self.api_key)

    # ---------- auth ----------
    def login_url(self) -> str:
        return self.kite.login_url()

    def complete_login(self, request_token_or_url: str) -> None:
        tok = request_token_or_url.strip()
        if "request_token=" in tok:
            tok = tok.split("request_token=")[1].split("&")[0]
        data = self.kite.generate_session(tok, api_secret=self.api_secret)
        save_token(self.name, data["access_token"], {"user_id": data.get("user_id")})
        self.kite.set_access_token(data["access_token"])

    def connect(self) -> None:
        token = load_token(self.name) or env("KITE_ACCESS_TOKEN")
        if not token:
            raise BrokerError("No Zerodha session for today. Run: qd broker login zerodha")
        self.kite.set_access_token(token)

    # ---------- market data ----------
    def get_ltp(self, symbols: list[str]) -> dict[str, float]:
        keys = [f"NSE:{s}" for s in symbols]
        data = self.kite.ltp(keys)
        return {k.split(":", 1)[1]: float(v["last_price"]) for k, v in data.items()}

    # ---------- orders ----------
    def place_order(self, req: OrderRequest) -> OrderResult:
        k = self.kite
        otype = {"MARKET": k.ORDER_TYPE_MARKET, "LIMIT": k.ORDER_TYPE_LIMIT, "SL": k.ORDER_TYPE_SL,
                 "SL-M": k.ORDER_TYPE_SLM}[req.order_type]
        params = dict(variety=k.VARIETY_REGULAR, exchange=req.exchange, tradingsymbol=req.symbol,
                      transaction_type=k.TRANSACTION_TYPE_BUY if req.side == "BUY" else k.TRANSACTION_TYPE_SELL,
                      quantity=int(req.quantity), product=k.PRODUCT_CNC if req.product == "CNC" else k.PRODUCT_MIS,
                      order_type=otype, validity=k.VALIDITY_DAY, tag=req.tag[:20])
        if req.price is not None and req.order_type in ("LIMIT", "SL"):
            params["price"] = req.price
        if req.trigger_price is not None and req.order_type in ("SL", "SL-M"):
            params["trigger_price"] = req.trigger_price
        try:
            oid = k.place_order(**params)
        except Exception as exc:
            return OrderResult("", "REJECTED", req.symbol, req.side, req.quantity, message=str(exc))
        return OrderResult(str(oid), "OPEN", req.symbol, req.side, req.quantity, message="submitted")

    def cancel_order(self, order_id: str) -> OrderResult:
        self.kite.cancel_order(variety=self.kite.VARIETY_REGULAR, order_id=order_id)
        return OrderResult(order_id, "CANCELLED")

    def get_orders(self) -> list[OrderResult]:
        return [OrderResult(str(o["order_id"]), _STATUS.get(o["status"], o["status"]), o["tradingsymbol"],
                            o["transaction_type"], int(o["quantity"]), int(o.get("filled_quantity") or 0),
                            float(o.get("average_price") or 0), o.get("status_message") or "", raw=o)
                for o in self.kite.orders()]

    # ---------- account ----------
    def get_positions(self) -> list[Position]:
        return [Position(p["tradingsymbol"], int(p["quantity"]), float(p["average_price"]), float(p["last_price"]),
                         float(p["pnl"]), p.get("product", "MIS"))
                for p in self.kite.positions().get("net", []) if p.get("product") != "CNC"]

    def get_holdings(self) -> list[Position]:
        return [Position(h["tradingsymbol"], int(h["quantity"]) + int(h.get("t1_quantity") or 0),
                         float(h["average_price"]), float(h["last_price"]), float(h["pnl"]), "CNC")
                for h in self.kite.holdings()]

    def get_funds(self) -> Funds:
        m = self.kite.margins("equity")
        avail = m.get("available", {})
        return Funds(float(avail.get("live_balance", avail.get("cash", 0.0))),
                     float(m.get("utilised", {}).get("debits", 0.0)), raw=m)
