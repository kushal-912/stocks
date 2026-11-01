"""Whole-market daily price panel: one wide parquet file per field (dates x symbols).

Wide panels make cross-sectional screening of ~2,300 stocks a handful of vectorised pandas
operations. Prices are split-adjusted only (never dividend back-adjusted, which would leak future
dividends into past prices). When a split happens, Yahoo rescales that stock's history; the
overlap check notices and re-downloads it.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd

from quantdesk.config import get_settings
from quantdesk.data.calendar import now_ist

log = logging.getLogger(__name__)

FIELDS = ("open", "high", "low", "close", "volume")
BENCHMARKS = {"NIFTY": "^NSEI", "NIFTY500": "^CRSLDX", "BANKNIFTY": "^NSEBANK"}


def _yahoo(sym: str) -> str:
    return BENCHMARKS.get(sym, sym + ".NS")


def _back(yahoo: str) -> str:
    for k, v in BENCHMARKS.items():
        if v == yahoo:
            return k
    return yahoo[:-3] if yahoo.endswith(".NS") else yahoo


def _download(symbols: list[str], period: str, batch: int = 200) -> dict[str, pd.DataFrame]:
    import yfinance as yf

    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    out = {f: [] for f in FIELDS}
    for i in range(0, len(symbols), batch):
        chunk = [_yahoo(s) for s in symbols[i:i + batch]]
        for attempt in range(3):
            try:
                # auto_adjust=False: OHLC are split-adjusted only. Dividend back-adjustment would rescale
                # old prices using dividends paid *later* - a (small) look-ahead we refuse to allow.
                raw = yf.download(chunk, period=period, interval="1d", auto_adjust=False, group_by="column",
                                  threads=True, progress=False)
                break
            except Exception as exc:
                log.warning("batch download failed (%s), retry %d", exc, attempt + 1)
                raw = None
        if raw is None or raw.empty:
            continue
        if not isinstance(raw.columns, pd.MultiIndex):  # single ticker
            raw.columns = pd.MultiIndex.from_product([raw.columns, chunk])
        for f in FIELDS:
            sub = raw[f.capitalize()].copy()
            sub.columns = [_back(c) for c in sub.columns]
            out[f].append(sub)
        log.info("downloaded %d/%d", min(i + batch, len(symbols)), len(symbols))
    res = {}
    for f in FIELDS:
        if out[f]:
            df = pd.concat(out[f], axis=1)
            idx = pd.to_datetime(df.index)
            df.index = idx.tz_localize(None) if idx.tz is not None else idx
            df = df.loc[:, ~df.columns.duplicated()].sort_index()
            res[f] = df.astype("float32")
    return res


@dataclass
class MarketPanel:
    open: pd.DataFrame
    high: pd.DataFrame
    low: pd.DataFrame
    close: pd.DataFrame
    volume: pd.DataFrame
    meta: dict = field(default_factory=dict)

    # ------------------------------------------------------------ io
    @staticmethod
    def _dir():
        return get_settings().path("data/market/x").parent

    @classmethod
    def load(cls) -> "MarketPanel":
        d = cls._dir()
        frames = {}
        for f in FIELDS:
            p = d / f"{f}.parquet"
            frames[f] = pd.read_parquet(p) if p.exists() else pd.DataFrame()
        return cls(**frames)

    def save(self) -> None:
        d = self._dir()
        for f in FIELDS:
            getattr(self, f).to_parquet(d / f"{f}.parquet")

    @property
    def symbols(self) -> list[str]:
        return list(self.close.columns)

    # ------------------------------------------------------------ update
    def update(self, symbols: list[str], years: int = 4) -> dict:
        """Bring the panel up to date for ``symbols`` (+ benchmarks)."""
        want = list(dict.fromkeys(list(symbols) + list(BENCHMARKS)))
        have = set(self.close.columns) if not self.close.empty else set()
        new = [s for s in want if s not in have]
        old = [s for s in want if s in have]
        stats = {"new": len(new), "updated": len(old), "redownloaded": 0}

        if old:
            recent = _download(old, "10d")
            if recent:
                # Overlap check: a changed adjustment factor means a new split/dividend -> full re-download.
                overlap = recent["close"].index.intersection(self.close.index)
                if len(overlap):
                    a = recent["close"].loc[overlap, recent["close"].columns.intersection(self.close.columns)]
                    b = self.close.loc[overlap, a.columns]
                    rel = ((a - b).abs() / b).max()
                    changed = rel[rel > 0.005].index.tolist()
                    new += changed
                    stats["redownloaded"] = len(changed)
                for f in FIELDS:
                    cur = getattr(self, f)
                    upd = recent[f]
                    merged = cur.reindex(index=cur.index.union(upd.index), columns=cur.columns.union(upd.columns))
                    merged.update(upd)
                    setattr(self, f, merged.astype("float32"))
        if new:
            full = _download(new, f"{years}y")
            for f in FIELDS:
                cur = getattr(self, f)
                if f not in full:
                    continue
                upd = full[f]
                if cur.empty:
                    setattr(self, f, upd)
                    continue
                cur = cur.drop(columns=[c for c in upd.columns if c in cur.columns])
                setattr(self, f, cur.join(upd, how="outer").astype("float32"))
        # Drop symbols Yahoo has nothing for.
        empty = self.close.columns[self.close.notna().sum() == 0]
        for f in FIELDS:
            setattr(self, f, getattr(self, f).drop(columns=empty).sort_index())
        self.save()
        stats["symbols"] = len(self.close.columns)
        stats["last_date"] = str(self.close.index[-1].date()) if len(self.close) else None
        return stats

    # ------------------------------------------------------------ views
    def completed(self, now: datetime | None = None) -> "MarketPanel":
        """Drop today's in-progress bar if the session hasn't closed yet (avoid partial-day data)."""
        now = now or now_ist()
        today = pd.Timestamp(now.date())
        if len(self.close) and self.close.index[-1] >= today and (now.hour, now.minute) < (15, 35):
            cut = self.close.index < today
            return MarketPanel(*(getattr(self, f).loc[cut] for f in FIELDS), meta=self.meta)
        return self

    def until(self, day: pd.Timestamp) -> "MarketPanel":
        return MarketPanel(*(getattr(self, f).loc[:day] for f in FIELDS), meta=self.meta)

    def ohlcv(self, symbol: str) -> pd.DataFrame:
        df = pd.DataFrame({f: getattr(self, f)[symbol] for f in FIELDS}).astype("float64")
        return df.dropna(subset=["close"])

    def last_date(self) -> pd.Timestamp | None:
        return self.close.index[-1] if len(self.close) else None


def latest_closes(symbols: list[str]) -> dict[str, float]:
    p = MarketPanel.load()
    out = {}
    for s in symbols:
        if s in p.close.columns:
            v = p.close[s].dropna()
            if len(v):
                out[s] = float(v.iloc[-1])
    return out


def nan_safe(x) -> float | None:
    return None if x is None or (isinstance(x, float) and np.isnan(x)) else float(x)
