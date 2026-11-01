"""Indian cash-equity transaction costs and capital-gains tax estimates.

Statutory rates below are the commonly published NSE values as of 2025-26. Brokers revise their
own fees, so check your broker's pricing page and override via ``costs.custom`` in settings.

    STT              delivery 0.1% on buy and sell; intraday 0.025% on sell only
    Exchange txn     NSE 0.00297% of turnover
    SEBI fee         Rs 10 per crore (0.0001%)
    Stamp duty       delivery 0.015% on buy; intraday 0.003% on buy
    GST              18% on (brokerage + exchange + SEBI fees)
    DP charge        flat per scrip on delivery sells (depository fee incl. GST)
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

STT = {"CNC": {"BUY": 0.001, "SELL": 0.001}, "MIS": {"BUY": 0.0, "SELL": 0.00025}}
STAMP = {"CNC": 0.00015, "MIS": 0.00003}
EXCHANGE_TXN = 0.0000297
SEBI_FEE = 0.000001
GST = 0.18

BROKER_PROFILES = {
    # pct is of order value; result clipped to [min, max] per executed order
    "zerodha": {"CNC": {"pct": 0.0, "min": 0.0, "max": 0.0}, "MIS": {"pct": 0.0003, "min": 0.0, "max": 20.0}, "dp": 15.34},
    "groww": {"CNC": {"pct": 0.001, "min": 5.0, "max": 20.0}, "MIS": {"pct": 0.001, "min": 5.0, "max": 20.0}, "dp": 16.52},
}


@dataclass
class CostBreakdown:
    brokerage: float = 0.0
    stt: float = 0.0
    exchange: float = 0.0
    sebi: float = 0.0
    stamp: float = 0.0
    gst: float = 0.0
    dp: float = 0.0

    @property
    def total(self) -> float:
        return self.brokerage + self.stt + self.exchange + self.sebi + self.stamp + self.gst + self.dp

    def to_dict(self) -> dict:
        return {**asdict(self), "total": self.total}


def _profile(profile: str, custom: dict | None = None) -> dict:
    if profile == "custom":
        c = custom or {}
        b = {"pct": c.get("brokerage_pct", 0.0) / 100, "min": c.get("brokerage_min", 0.0), "max": c.get("brokerage_max", 20.0)}
        return {"CNC": b, "MIS": b, "dp": c.get("dp", 15.34)}
    return BROKER_PROFILES[profile]


def order_costs(value: float, side: str, product: str = "CNC", profile: str = "groww",
                custom: dict | None = None) -> CostBreakdown:
    """All-in charges for one executed order of ``value`` rupees."""
    if value <= 0:
        return CostBreakdown()
    side, product = side.upper(), product.upper()
    prof = _profile(profile, custom)
    b = prof[product]
    brokerage = min(max(value * b["pct"], b["min"]), b["max"]) if b["max"] > 0 else 0.0
    stt = value * STT[product][side]
    exch = value * EXCHANGE_TXN
    sebi = value * SEBI_FEE
    stamp = value * STAMP[product] if side == "BUY" else 0.0
    gst = GST * (brokerage + exch + sebi)
    dp = prof["dp"] if (side == "SELL" and product == "CNC") else 0.0
    return CostBreakdown(brokerage, stt, exch, sebi, stamp, gst, dp)


def round_trip_cost_pct(value: float, product: str = "CNC", profile: str = "groww") -> float:
    c = order_costs(value, "BUY", product, profile).total + order_costs(value, "SELL", product, profile).total
    return c / value


def capital_gains_tax(trades, ltcg_exemption: float = 125_000.0) -> dict:
    """Rough Indian equity capital-gains estimate for delivery trades (post 23-Jul-2024 rates).

    STCG (held <= 12 months) 20%; LTCG (> 12 months) 12.5% above the annual exemption.
    Losses are netted within each bucket only (simplified). Intraday profits are business
    income taxed at slab rates and are reported separately, untaxed here. Not tax advice.
    """
    import pandas as pd

    if trades is None or len(trades) == 0:
        return {"stcg": 0.0, "ltcg": 0.0, "tax": 0.0}
    t = pd.DataFrame(trades)
    held = (pd.to_datetime(t["exit_date"]) - pd.to_datetime(t["entry_date"])).dt.days
    st = float(t.loc[held <= 365, "pnl"].sum())
    lt = float(t.loc[held > 365, "pnl"].sum())
    tax = max(st, 0) * 0.20 + max(lt - ltcg_exemption, 0) * 0.125
    return {"stcg": st, "ltcg": lt, "tax": tax}
