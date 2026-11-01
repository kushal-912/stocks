"""Intraday market feed (Yahoo 1-minute bars, batched). Used for paper fills and as the price
source for decisions in paper mode. Live mode prices come from the broker's own feed.

Note: free Yahoo intraday data for NSE can lag the exchange by a few minutes.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime

import pandas as pd

from quantdesk.data.panel import BENCHMARKS

log = logging.getLogger(__name__)


def _yahoo(sym: str) -> str:
    return BENCHMARKS.get(sym, sym + ".NS")


class MarketFeed:
    def __init__(self, ttl: float = 20.0):
        self.ttl = ttl
        self._cache: dict[tuple[str, str], tuple[float, pd.DataFrame]] = {}
        self._lock = threading.Lock()

    def bars(self, symbols: list[str], period: str = "1d") -> dict[str, pd.DataFrame]:
        """1-minute OHLCV bars (naive IST timestamps = bar start) for the last ``period`` (<= 7d)."""
        import yfinance as yf

        now = time.monotonic()
        out, todo = {}, []
        with self._lock:
            for s in symbols:
                hit = self._cache.get((s, period))
                if hit and now - hit[0] < self.ttl:
                    out[s] = hit[1]
                else:
                    todo.append(s)
        if todo:
            logging.getLogger("yfinance").setLevel(logging.CRITICAL)
            tick = [_yahoo(s) for s in todo]
            try:
                raw = yf.download(tick, period=period, interval="1m", auto_adjust=False, group_by="ticker",
                                  threads=True, progress=False)
            except Exception as exc:
                log.warning("intraday download failed: %s", exc)
                raw = pd.DataFrame()
            for s, y in zip(todo, tick):
                try:
                    df = raw[y] if isinstance(raw.columns, pd.MultiIndex) else raw
                    df = df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]].dropna(subset=["close"])
                    idx = pd.to_datetime(df.index)
                    if idx.tz is not None:
                        idx = idx.tz_convert("Asia/Kolkata").tz_localize(None)
                    df.index = idx
                except Exception:
                    df = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
                out[s] = df
                with self._lock:
                    self._cache[(s, period)] = (now, df)
        return out

    def bars_since(self, symbol: str, since: datetime) -> pd.DataFrame:
        """Bars that *started after* ``since`` (strictly later minute), across up to 7 days of downtime."""
        days = (datetime.now() - since).days
        period = "1d" if days < 1 else "5d" if days < 5 else "7d"
        df = self.bars([symbol], period)[symbol]
        if df.empty:
            return df
        cutoff = pd.Timestamp(since).floor("min")
        return df[df.index > cutoff]

    def ltp(self, symbols: list[str]) -> dict[str, float]:
        out = {}
        bars = self.bars(symbols, "1d") if symbols else {}
        missing = []
        for s in symbols:
            b = bars.get(s)
            if b is not None and len(b):
                out[s] = float(b["close"].iloc[-1])
            else:
                missing.append(s)
        if missing:  # outside market hours / thin names: fall back to the last daily close
            from quantdesk.data.panel import latest_closes

            out.update(latest_closes(missing))
        return out


_default: MarketFeed | None = None


def default_feed() -> MarketFeed:
    global _default
    if _default is None:
        _default = MarketFeed()
    return _default
