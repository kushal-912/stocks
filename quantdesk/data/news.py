"""News headlines from Google News RSS and Yahoo Finance."""
from __future__ import annotations

import hashlib
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus

from quantdesk.config import get_settings
from quantdesk.data.symbols import display_symbol, to_yahoo

log = logging.getLogger(__name__)


@dataclass
class Headline:
    symbol: str
    title: str
    summary: str
    source: str
    url: str
    published: datetime  # UTC

    @property
    def uid(self) -> str:
        return hashlib.sha1(f"{self.symbol}|{self.title}".encode()).hexdigest()[:16]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["published"] = self.published.isoformat()
        d["uid"] = self.uid
        return d


def _google_news(symbol: str, query: str, days: int) -> list[Headline]:
    import feedparser

    url = (f"https://news.google.com/rss/search?q={quote_plus(query)}+when:{days}d"
           f"&hl=en-IN&gl=IN&ceid=IN:en")
    feed = feedparser.parse(url)
    out = []
    for e in feed.entries:
        try:
            pub = datetime(*e.published_parsed[:6], tzinfo=timezone.utc)
        except Exception:
            pub = datetime.now(timezone.utc)
        source = getattr(getattr(e, "source", None), "title", "") or "Google News"
        out.append(Headline(symbol, e.title, "", source, e.link, pub))
    return out


def _yahoo_news(symbol: str) -> list[Headline]:
    import yfinance as yf

    out = []
    try:
        items = yf.Ticker(to_yahoo(symbol)).news or []
    except Exception as exc:
        log.debug("Yahoo news failed for %s: %s", symbol, exc)
        return out
    for it in items:
        c = it.get("content", it)  # newer yfinance nests fields under "content"
        title = c.get("title") or ""
        if not title:
            continue
        summary = c.get("summary") or c.get("description") or ""
        provider = c.get("provider")
        source = provider.get("displayName", "Yahoo") if isinstance(provider, dict) else (c.get("publisher") or "Yahoo")
        link = c.get("canonicalUrl", {}).get("url") if isinstance(c.get("canonicalUrl"), dict) else c.get("link", "")
        if c.get("pubDate"):
            pub = datetime.fromisoformat(c["pubDate"].replace("Z", "+00:00"))
        elif c.get("providerPublishTime"):
            pub = datetime.fromtimestamp(c["providerPublishTime"], tz=timezone.utc)
        else:
            pub = datetime.now(timezone.utc)
        out.append(Headline(symbol, title, summary, source, link or "", pub))
    return out


def _clean_company(name: str) -> str:
    import re

    n = re.sub(r"\b(limited|ltd\.?|the)\b", "", name, flags=re.I)
    return re.sub(r"\s+", " ", n).strip(" .,&")


def company_name(symbol: str) -> str | None:
    try:
        from quantdesk.data.universe import load_universe

        u = load_universe(max_age_days=365)
        row = u.loc[u["symbol"] == symbol, "name"]
        return str(row.iloc[0]) if len(row) else None
    except Exception:
        return None


def get_headlines(symbol: str, company_hint: str | None = None, limit: int | None = None) -> list[Headline]:
    cfg = get_settings().news
    limit = limit or cfg.max_headlines_per_symbol
    company_hint = company_hint or company_name(symbol)
    name = display_symbol(to_yahoo(symbol))
    company = _clean_company(company_hint) if company_hint else None
    query = f'"{company}"' if company else f'"{name}" share price'
    items: list[Headline] = []
    try:
        g = _google_news(symbol, query, cfg.lookback_days)
        # Keep only headlines that actually name the company (search engines match loosely).
        keys = {symbol.lower()} | ({company.split()[0].lower()} if company and len(company.split()[0]) > 3 else set())
        items += [h for h in g if any(k in h.title.lower() for k in keys)] if company else g
    except Exception as exc:
        log.debug("Google News failed for %s: %s", symbol, exc)
    items += _yahoo_news(symbol)

    cutoff = datetime.now(timezone.utc) - timedelta(days=cfg.lookback_days)
    seen, uniq = set(), []
    for h in sorted(items, key=lambda h: h.published, reverse=True):
        key = h.title.rsplit(" - ", 1)[0].lower()[:60]  # Google appends " - Source"; syndicated copies differ only there
        if key in seen or h.published < cutoff:
            continue
        seen.add(key)
        uniq.append(h)
    return uniq[:limit]
