"""The investable universe: every NSE equity (series EQ), with sectors where NSE publishes them."""
from __future__ import annotations

import io
import logging
import time

import pandas as pd
import requests

from quantdesk.config import get_settings

log = logging.getLogger(__name__)

EQUITY_LIST = "https://archives.nseindia.com/content/equities/EQUITY_L.csv"
SECTOR_LISTS = [
    "https://archives.nseindia.com/content/indices/ind_niftytotalmarket_list.csv",
    "https://archives.nseindia.com/content/indices/ind_nifty500list.csv",
]
NIFTY50_LIST = "https://archives.nseindia.com/content/indices/ind_nifty50list.csv"
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/128.0 Safari/537.36", "Accept": "text/csv,*/*"}


def _get_csv(url: str) -> pd.DataFrame:
    r = requests.get(url, headers=_HEADERS, timeout=30)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text))
    df.columns = [c.strip() for c in df.columns]
    return df


def _path():
    return get_settings().path("data/universe.csv")


def refresh_universe() -> pd.DataFrame:
    eq = _get_csv(EQUITY_LIST)
    eq["SERIES"] = eq["SERIES"].astype(str).str.strip()
    eq = eq[eq["SERIES"] == "EQ"]
    uni = pd.DataFrame({"symbol": eq["SYMBOL"].astype(str).str.strip(), "name": eq["NAME OF COMPANY"].astype(str).str.strip(),
                        "isin": eq["ISIN NUMBER"].astype(str).str.strip(),
                        "listed": pd.to_datetime(eq["DATE OF LISTING"].astype(str).str.strip(), format="%d-%b-%Y", errors="coerce")})
    sectors: dict[str, str] = {}
    for url in SECTOR_LISTS:
        try:
            s = _get_csv(url)
            for sym, ind in zip(s["Symbol"].astype(str).str.strip(), s["Industry"].astype(str).str.strip()):
                sectors.setdefault(sym, ind)
        except Exception as exc:
            log.warning("sector list %s failed: %s", url, exc)
    try:
        n50 = set(_get_csv(NIFTY50_LIST)["Symbol"].astype(str).str.strip())
    except Exception:
        n50 = set()
    uni["sector"] = uni["symbol"].map(sectors).fillna("Other")
    uni["nifty50"] = uni["symbol"].isin(n50)
    uni["index_member"] = uni["symbol"].isin(sectors)
    uni = uni.drop_duplicates("symbol").sort_values("symbol").reset_index(drop=True)
    uni.to_csv(_path(), index=False)
    log.info("Universe refreshed: %d EQ stocks (%d with sector data)", len(uni), uni["index_member"].sum())
    return uni


def load_universe(max_age_days: float | None = None) -> pd.DataFrame:
    p = _path()
    max_age = max_age_days if max_age_days is not None else get_settings().universe.refresh_days
    if p.exists() and (time.time() - p.stat().st_mtime) < max_age * 86400:
        return pd.read_csv(p, parse_dates=["listed"])
    try:
        return refresh_universe()
    except Exception as exc:
        if p.exists():
            log.warning("Universe refresh failed (%s); using cached list.", exc)
            return pd.read_csv(p, parse_dates=["listed"])
        raise


def resolve_sectors(symbols: list[str], universe: pd.DataFrame) -> dict[str, str]:
    """Sector for each symbol: NSE index classification when available, else Yahoo's (cached on disk)."""
    import json
    from concurrent.futures import ThreadPoolExecutor

    import yfinance as yf

    path = get_settings().path("data/sectors_extra.json")
    try:
        extra = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (json.JSONDecodeError, OSError):
        extra = {}
    known = universe.set_index("symbol")["sector"].to_dict()
    out, todo = {}, []
    for s in symbols:
        sec = known.get(s, "Other")
        if sec != "Other":
            out[s] = sec
        elif s in extra:
            out[s] = extra[s]
        else:
            todo.append(s)

    def fetch(sym):
        try:
            info = yf.Ticker(sym + ".NS").info or {}
            return sym, info.get("sector") or info.get("industry") or "Other"
        except Exception:
            return sym, "Other"

    if todo:
        with ThreadPoolExecutor(6) as ex:
            for sym, sec in ex.map(fetch, todo):
                out[sym] = sec
                extra[sym] = sec
        import os

        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(extra, indent=0), encoding="utf-8")
        os.replace(tmp, path)  # atomic: concurrent runs never see a half-written file
    return out


def sector_of(symbol: str, universe: pd.DataFrame | None = None) -> str:
    uni = universe if universe is not None else load_universe()
    row = uni.loc[uni["symbol"] == symbol, "sector"]
    return str(row.iloc[0]) if len(row) else "Other"


def agent_universe(max_age_days: float | None = None) -> pd.DataFrame:
    """The universe the agent trades, per ``universe.source`` (shared by the live agent and the backtest)."""
    s = get_settings()
    uni = load_universe(max_age_days)
    if s.universe.source == "index_members":
        return uni[uni["index_member"]]
    if s.universe.source == "watchlist":
        return uni[uni["symbol"].isin(s.watchlist)]
    return uni  # nse_all / top_liquid (top_liquid is narrowed point-in-time inside the screener)
