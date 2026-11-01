"""Market data via Yahoo Finance with a small on-disk CSV cache.

Yahoo data is free and good for research and daily signals. It can be delayed intraday, so the
continuous engine prefers the connected broker's LTP feed when a live broker is configured.
All frames use columns: open, high, low, close, volume (adjusted for splits/dividends).
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from quantdesk.config import get_settings
from quantdesk.data.symbols import to_yahoo

log = logging.getLogger(__name__)

COLUMNS = ["open", "high", "low", "close", "volume"]


def _cache_path(yahoo_symbol: str, interval: str) -> Path:
    s = get_settings()
    safe = yahoo_symbol.replace("^", "IDX_").replace("=", "_")
    return s.path(f"{s.data.cache_dir}/{safe}_{interval}.csv")


def _normalise(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=COLUMNS)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.rename(columns=str.lower)
    df = df[[c for c in COLUMNS if c in df.columns]].copy()
    idx = pd.to_datetime(df.index)
    if idx.tz is not None:
        idx = idx.tz_convert(get_settings().market.timezone).tz_localize(None)
    df.index = idx
    df.index.name = "date"
    df = df[~df.index.duplicated(keep="last")].sort_index()
    return df.dropna(subset=["close"])


def _download(yahoo_symbol: str, start: datetime | None, end: datetime | None,
              interval: str, period: str | None = None) -> pd.DataFrame:
    import yfinance as yf

    last_err: Exception | None = None
    for attempt in range(3):
        try:
            t = yf.Ticker(yahoo_symbol)
            if period:
                raw = t.history(period=period, interval=interval, auto_adjust=True)
            else:
                raw = t.history(start=start, end=end, interval=interval, auto_adjust=True)
            return _normalise(raw)
        except Exception as exc:  # network hiccups, Yahoo throttling
            last_err = exc
            time.sleep(1.5 * (attempt + 1))
    log.warning("Download failed for %s: %s", yahoo_symbol, last_err)
    return pd.DataFrame(columns=COLUMNS)


def get_history(symbol: str, start: str | datetime | None = None, end: str | datetime | None = None,
                years: int | None = None, use_cache: bool = True) -> pd.DataFrame:
    """Daily OHLCV for ``symbol`` (trading symbol or Yahoo ticker)."""
    s = get_settings()
    ysym = to_yahoo(symbol)
    end_dt = pd.Timestamp(end).to_pydatetime() if end else datetime.now() + timedelta(days=1)
    if start:
        start_dt = pd.Timestamp(start).to_pydatetime()
    else:
        start_dt = end_dt - timedelta(days=365 * (years or s.data.history_years))

    path = _cache_path(ysym, "1d")
    cached = pd.DataFrame(columns=COLUMNS)
    if use_cache and path.exists():
        try:
            cached = pd.read_csv(path, index_col=0, parse_dates=True)
            cached.index.name = "date"
        except Exception:
            cached = pd.DataFrame(columns=COLUMNS)
        fresh = (time.time() - path.stat().st_mtime) < s.data.cache_ttl_hours * 3600
        covers = not cached.empty and cached.index.min() <= pd.Timestamp(start_dt) + timedelta(days=7)
        if fresh and covers:
            return cached.loc[pd.Timestamp(start_dt):pd.Timestamp(end_dt)]

    df = _download(ysym, start_dt, end_dt, "1d")
    if df.empty and not cached.empty:
        log.warning("Using stale cache for %s", ysym)
        return cached.loc[pd.Timestamp(start_dt):pd.Timestamp(end_dt)]
    if not df.empty and use_cache:
        merged = pd.concat([cached, df])
        merged = merged[~merged.index.duplicated(keep="last")].sort_index()
        merged.index.name = "date"
        merged.to_csv(path)
    return df


def get_intraday(symbol: str, interval: str = "5m", period: str = "5d") -> pd.DataFrame:
    """Recent intraday bars (Yahoo keeps ~60 days of 5m bars, ~7 days of 1m bars)."""
    return _download(to_yahoo(symbol), None, None, interval, period=period)


def get_last_price(symbol: str) -> float | None:
    df = get_intraday(symbol, interval="1m", period="1d")
    if df.empty:
        df = get_history(symbol, years=1, use_cache=False)
    return float(df["close"].iloc[-1]) if not df.empty else None


def get_many_closes(symbols: list[str], start: str | None = None, end: str | None = None,
                    years: int | None = None) -> pd.DataFrame:
    """Aligned close-price matrix (columns = symbols)."""
    closes = {}
    for sym in symbols:
        df = get_history(sym, start=start, end=end, years=years)
        if not df.empty:
            closes[sym] = df["close"]
    return pd.DataFrame(closes).dropna(how="all").ffill().dropna()
