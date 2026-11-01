"""Position sizing: how many shares, given an entry, a stop and the account's risk budget."""
from __future__ import annotations

import math


def position_size(capital: float, entry: float, stop: float | None, risk_pct: float = 1.0,
                  max_position_pct: float = 20.0, ann_vol: float | None = None, vol_target: float | None = None,
                  kelly_cap: float | None = None) -> dict:
    """Smallest quantity allowed by every constraint:

    * fixed-fractional risk: lose at most ``risk_pct`` % of capital if the stop is hit
    * concentration: position value <= ``max_position_pct`` % of capital
    * volatility targeting: capital * min(1, vol_target / ann_vol)
    * fractional Kelly: capital * kelly_cap
    """
    if entry <= 0 or capital <= 0:
        return {"quantity": 0, "binding": "invalid input", "limits": {}}
    limits: dict[str, int] = {"concentration": math.floor(capital * max_position_pct / 100 / entry)}
    if stop is not None and abs(entry - stop) > 0:
        limits["risk_per_trade"] = math.floor(capital * risk_pct / 100 / abs(entry - stop))
    if ann_vol and vol_target:
        limits["vol_target"] = math.floor(capital * min(1.0, vol_target / ann_vol) / entry)
    if kelly_cap is not None:
        limits["kelly"] = math.floor(capital * max(0.0, kelly_cap) / entry)
    binding = min(limits, key=limits.get)
    qty = max(0, limits[binding])
    return {"quantity": qty, "binding": binding, "limits": limits, "value": qty * entry,
            "risk_amount": qty * abs(entry - stop) if stop else None}
