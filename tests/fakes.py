"""Offline stand-ins for the market feed."""
from datetime import datetime

import pandas as pd


class FakeFeed:
    def __init__(self, prices: dict[str, float] | None = None):
        self.prices = dict(prices or {})
        self.bar_data: dict[str, pd.DataFrame] = {}

    def add_bars(self, symbol: str, rows: list[tuple[str, float, float, float, float]]) -> None:
        """rows: (timestamp, open, high, low, close)"""
        df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close"]).set_index("ts")
        df.index = pd.to_datetime(df.index)
        df["volume"] = 1000
        self.bar_data[symbol] = pd.concat([self.bar_data.get(symbol, pd.DataFrame()), df]).sort_index()
        self.prices[symbol] = float(df["close"].iloc[-1])

    def ltp(self, symbols):
        return {s: self.prices[s] for s in symbols if s in self.prices}

    def bars(self, symbols, period="1d"):
        return {s: self.bar_data.get(s, pd.DataFrame(columns=["open", "high", "low", "close", "volume"])) for s in symbols}

    def bars_since(self, symbol, since: datetime):
        df = self.bar_data.get(symbol)
        if df is None:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        return df[df.index > pd.Timestamp(since).floor("min")]
