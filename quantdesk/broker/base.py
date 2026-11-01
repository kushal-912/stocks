"""Broker-neutral order and account model. Strategy/engine code only ever talks to ``Broker``."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Literal

Side = Literal["BUY", "SELL"]
OrderType = Literal["MARKET", "LIMIT", "SL", "SL-M"]
Product = Literal["CNC", "MIS"]


@dataclass
class OrderRequest:
    symbol: str
    side: Side
    quantity: int
    order_type: OrderType = "MARKET"
    price: float | None = None           # LIMIT / SL limit price
    trigger_price: float | None = None   # SL / SL-M trigger
    product: Product = "CNC"
    exchange: str = "NSE"
    validity: str = "DAY"
    tag: str = "QDPERSONAL"
    reason: str = ""                      # why the engine wants this order (for the audit log)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class OrderResult:
    order_id: str
    status: str                 # OPEN | COMPLETE | REJECTED | CANCELLED | PENDING | UNKNOWN
    symbol: str = ""
    side: str = ""
    quantity: int = 0
    filled_quantity: int = 0
    average_price: float = 0.0
    message: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    raw: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Position:
    symbol: str
    quantity: int
    average_price: float
    last_price: float = 0.0
    pnl: float = 0.0
    product: str = "CNC"

    @property
    def market_value(self) -> float:
        return self.quantity * (self.last_price or self.average_price)

    def to_dict(self) -> dict:
        return asdict(self) | {"market_value": self.market_value}


@dataclass
class Funds:
    available_cash: float
    used_margin: float = 0.0
    total_equity: float | None = None
    raw: dict = field(default_factory=dict)


class BrokerError(RuntimeError):
    pass


class Broker(ABC):
    name: str = "base"
    is_live: bool = False

    def connect(self) -> None:
        """Authenticate / restore today's session. Raise BrokerError with guidance if login is needed."""

    @abstractmethod
    def get_ltp(self, symbols: list[str]) -> dict[str, float]: ...

    @abstractmethod
    def place_order(self, req: OrderRequest) -> OrderResult: ...

    @abstractmethod
    def cancel_order(self, order_id: str) -> OrderResult: ...

    @abstractmethod
    def get_orders(self) -> list[OrderResult]: ...

    @abstractmethod
    def get_positions(self) -> list[Position]: ...

    @abstractmethod
    def get_holdings(self) -> list[Position]: ...

    @abstractmethod
    def get_funds(self) -> Funds: ...

    def portfolio(self) -> list[Position]:
        """Holdings + intraday/net positions merged by symbol."""
        merged: dict[str, Position] = {}
        for p in self.get_holdings() + self.get_positions():
            if p.quantity == 0:
                continue
            if p.symbol in merged:
                m = merged[p.symbol]
                qty = m.quantity + p.quantity
                avg = (m.average_price * m.quantity + p.average_price * p.quantity) / qty if qty else 0.0
                merged[p.symbol] = Position(p.symbol, qty, avg, p.last_price or m.last_price, m.pnl + p.pnl, m.product)
            else:
                merged[p.symbol] = p
        return list(merged.values())

    def equity(self) -> float:
        f = self.get_funds()
        if f.total_equity is not None:
            return f.total_equity
        return f.available_cash + f.used_margin + sum(p.market_value for p in self.get_holdings())
