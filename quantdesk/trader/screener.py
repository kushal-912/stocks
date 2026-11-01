"""Stage 1: screen the whole market with vectorised cross-sectional factors.

Pure function of the price panel, so the live agent and the historical backtest call the
exact same code. Gates remove what we never want to own; factor z-scores rank the rest.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from quantdesk.config import get_settings
from quantdesk.data.panel import BENCHMARKS, MarketPanel


@dataclass
class ScreenResult:
    as_of: pd.Timestamp
    table: pd.DataFrame            # one row per stock with data: factors, gates, score, rank
    funnel: dict                   # counts at each stage

    def shortlist(self, n: int) -> pd.DataFrame:
        return self.table[self.table["eligible"]].sort_values("screen_score", ascending=False).head(n)


def _zscore(s: pd.Series) -> pd.Series:
    s = s.replace([np.inf, -np.inf], np.nan)
    z = (s - s.mean()) / s.std(ddof=0)
    return z.clip(-3, 3)


def _rsi_last(close: pd.DataFrame, n: int = 14) -> pd.Series:
    d = close.diff()
    gain = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean().iloc[-1]
    loss = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean().iloc[-1]
    return 100 - 100 / (1 + gain / loss.replace(0, np.nan))


def compute_factors(panel: MarketPanel, lookback: int = 300) -> pd.DataFrame:
    """Factor values as of the panel's last row (uses only the trailing ``lookback`` rows)."""
    with np.errstate(divide="ignore", invalid="ignore"):
        return _compute_factors(panel, lookback)


def _compute_factors(panel: MarketPanel, lookback: int) -> pd.DataFrame:
    c = panel.close.iloc[-lookback:].drop(columns=[k for k in BENCHMARKS if k in panel.close.columns])
    h = panel.high[c.columns].iloc[-lookback:]
    l = panel.low[c.columns].iloc[-lookback:]
    v = panel.volume[c.columns].iloc[-lookback:]
    last = c.iloc[-1]
    f = pd.DataFrame(index=c.columns)
    f["close"] = last
    f["last_date"] = c.apply(lambda s: s.last_valid_index())
    f["history"] = c.notna().sum()
    f["traded_value_cr"] = (c * v).iloc[-20:].median() / 1e7
    f["traded_value_60_cr"] = (c * v).iloc[-60:].median() / 1e7
    f["sma50"] = c.iloc[-50:].mean()
    f["sma200"] = c.iloc[-200:].mean()
    prev = c.shift()
    tr = np.fmax(np.fmax(h - l, (h - prev).abs()), (l - prev).abs())
    f["atr"] = tr.ewm(alpha=1 / 14, adjust=False).mean().iloc[-1]
    f["atr_pct"] = f["atr"] / last * 100
    r = np.log(c).diff()
    f["vol_63"] = r.iloc[-63:].std() * np.sqrt(252)
    f["mom_12_1"] = c.iloc[-22] / c.iloc[-253] - 1 if len(c) > 253 else np.nan
    f["mom_6_1"] = c.iloc[-22] / c.iloc[-127] - 1 if len(c) > 127 else np.nan
    f["ret_5"] = last / c.iloc[-6] - 1
    f["ret_1"] = last / c.iloc[-2] - 1
    f["risk_adj_mom"] = f["mom_12_1"] / f["vol_63"]
    f["high_52w"] = last / h.iloc[-252:].max()
    f["low_vol"] = -f["vol_63"]
    f["volume_trend"] = np.log(v.iloc[-20:].mean() / v.iloc[-120:].mean())
    f["short_reversal"] = -f["ret_5"]
    f["rsi"] = _rsi_last(c)
    f["extension_pct"] = (last / f["sma50"] - 1) * 100
    return f


def screen(panel: MarketPanel, universe: pd.DataFrame | None = None) -> ScreenResult:
    s = get_settings()
    u, sc = s.universe, s.screener
    f = compute_factors(panel)
    as_of = panel.close.index[-1]
    funnel = {"universe": int(len(universe)) if universe is not None else int(len(f)), "with_data": int(len(f))}

    if universe is not None:
        f = f[f.index.isin(universe["symbol"])]
        f["sector"] = f.index.map(universe.set_index("symbol")["sector"]).fillna("Other")
        f["name"] = f.index.map(universe.set_index("symbol")["name"])
    else:
        f["sector"], f["name"] = "Other", f.index

    fresh = f["last_date"] >= as_of - pd.Timedelta(days=4)          # still trading (not suspended)
    liquid = fresh & (f["close"] >= u.min_price) & (f["traded_value_cr"] >= u.min_traded_value_cr) \
        & (f["history"] >= min(u.min_history_days, len(panel.close) - 1))
    if u.source == "top_liquid":
        # Point-in-time universe: rank by trailing traded value measured *on this date only*.
        rank = f.loc[liquid, "traded_value_60_cr"].rank(ascending=False)
        liquid &= f.index.isin(rank[rank <= u.top_n].index)
    funnel["liquid"] = int(liquid.sum())

    trend_ok = (f["close"] > f["sma200"]) & (f["sma50"] > f["sma200"])
    not_extended = (f["extension_pct"] < sc.max_extension_pct) & (f["rsi"] < sc.max_rsi)
    tame = f["atr_pct"] < sc.max_atr_pct
    f["liquid"], f["trend_ok"], f["not_extended"], f["tame"] = liquid, trend_ok, not_extended, tame
    f["eligible"] = liquid & trend_ok & not_extended & tame
    funnel["uptrend"] = int((liquid & trend_ok).sum())
    funnel["eligible"] = int(f["eligible"].sum())

    # Cross-sectional z-scores among liquid names only.
    score = pd.Series(0.0, index=f.index)
    wsum = 0.0
    for name, w in sc.weights.items():
        z = _zscore(f.loc[liquid, name])
        f.loc[liquid, f"z_{name}"] = z
        score = score.add(w * z.reindex(f.index).fillna(0.0), fill_value=0.0)
        wsum += w
    f["raw_score"] = (score / wsum).where(liquid)
    pct = f["raw_score"].rank(pct=True)
    f["screen_score"] = (2 * pct - 1).where(liquid)          # [-1, 1] percentile score
    f["rank"] = f["raw_score"].rank(ascending=False)
    return ScreenResult(as_of, f.sort_values("raw_score", ascending=False), funnel)


def market_regime(panel: MarketPanel, symbol: str = "NIFTY") -> dict:
    """Risk-on when the index is above its 200-day average and the 50-day is above the 200-day."""
    if symbol not in panel.close.columns:
        return {"state": "unknown", "risk_on": True}
    c = panel.close[symbol].dropna()
    sma50, sma200 = c.iloc[-50:].mean(), c.iloc[-200:].mean()
    last = float(c.iloc[-1])
    risk_on = bool(last > sma200 or sma50 > sma200)  # risk-off only when both are broken
    return {"state": "risk-on" if risk_on else "risk-off", "risk_on": risk_on, "index": symbol, "close": last,
            "sma50": float(sma50), "sma200": float(sma200), "pct_vs_sma200": float((last / sma200 - 1) * 100)}


def breadth(screen_res: ScreenResult) -> float:
    """Share of liquid stocks above their 200-day average (market health)."""
    t = screen_res.table
    liq = t[t["liquid"]]
    return float((liq["close"] > liq["sma200"]).mean()) if len(liq) else float("nan")
