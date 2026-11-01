"""Symbol conventions.

Inside QuantDesk a symbol is the exchange trading symbol (e.g. ``RELIANCE``), which is what
Groww and Zerodha use. Yahoo Finance needs a suffix (``RELIANCE.NS``). Anything that already
looks like a Yahoo ticker (contains ``.``, ``^`` or ``=``) is passed through unchanged, so
indices (``^NSEI``) and foreign listings also work.
"""
from __future__ import annotations

from quantdesk.config import get_settings

_SUFFIX = {"NSE": ".NS", "BSE": ".BO", "US": ""}

INDEX_ALIASES = {
    "NIFTY": "^NSEI",
    "NIFTY50": "^NSEI",
    "BANKNIFTY": "^NSEBANK",
    "SENSEX": "^BSESN",
}


def to_yahoo(symbol: str, exchange: str | None = None) -> str:
    s = symbol.strip().upper()
    if s in INDEX_ALIASES:
        return INDEX_ALIASES[s]
    if any(ch in s for ch in ".^="):
        return s
    exch = exchange or get_settings().market.default_exchange
    return s + _SUFFIX.get(exch, "")


def display_symbol(yahoo_symbol: str) -> str:
    s = yahoo_symbol.upper()
    for alias, y in INDEX_ALIASES.items():
        if s == y:
            return alias
    for suf in (".NS", ".BO"):
        if s.endswith(suf):
            return s[: -len(suf)]
    return s
