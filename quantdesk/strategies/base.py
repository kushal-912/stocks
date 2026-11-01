"""Strategy interface.

A strategy maps a daily OHLCV frame to a *target exposure* series in [-1, 1]
(fraction of the capital allocated to this instrument). The value at bar t is decided using
information up to and including bar t's close; the backtester executes it at bar t+1's open,
so no strategy can accidentally trade on information it would not have had.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd


class Strategy(ABC):
    name: str = "strategy"
    description: str = ""

    def __init__(self, **params):
        self.params = params

    @abstractmethod
    def generate(self, df: pd.DataFrame) -> pd.Series:
        """Return target exposure per bar in [-1, 1] (NaN treated as 0)."""

    def label(self) -> str:
        p = ", ".join(f"{k}={v}" for k, v in self.params.items())
        return f"{self.name}({p})" if p else self.name


def hysteresis(enter: pd.Series, exit_: pd.Series) -> pd.Series:
    """Long-only state machine: go long when ``enter`` is True, flat when ``exit_`` is True."""
    state, out = 0.0, []
    for e, x in zip(enter.fillna(False).to_numpy(), exit_.fillna(False).to_numpy()):
        if state == 0 and e:
            state = 1.0
        elif state == 1 and x:
            state = 0.0
        out.append(state)
    return pd.Series(out, index=enter.index)
