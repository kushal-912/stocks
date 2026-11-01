"""Stage 2: deep research on the shortlist (and every current holding).

Per stock: technical composite, ARIMA/ETS/Kalman forecast ensemble, GARCH volatility, purged
walk-forward ML, statistical diagnostics, regime - plus news sentiment, scored in one batch.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

from quantdesk.config import get_settings
from quantdesk.data.panel import MarketPanel
from quantdesk.strategies.composite import combine

log = logging.getLogger(__name__)


def _analyze(symbol: str, df: pd.DataFrame) -> dict | None:
    from quantdesk.engine.analyzer import analyze_symbol

    try:
        return analyze_symbol(symbol, df=df, with_ml=True, with_forecast=True, sentiment_engine=None).to_dict()
    except Exception as exc:
        log.warning("deep research failed for %s: %s", symbol, exc)
        return None


def _news(symbols: list[str], use_llm: bool) -> dict[str, dict]:
    from quantdesk.data.news import get_headlines
    from quantdesk.nlp.sentiment import SentimentEngine

    eng = SentimentEngine(use_llm=use_llm)
    with ThreadPoolExecutor(8) as ex:
        heads = dict(zip(symbols, ex.map(lambda s: _safe_headlines(get_headlines, s), symbols)))
    flat = [h for hs in heads.values() for h in hs]
    scored = eng.score_headlines(flat)  # one batched LLM pass (cached per headline)
    by_sym: dict[str, list] = {s: [] for s in symbols}
    for sh in scored:
        by_sym.setdefault(sh.headline.symbol, []).append(sh)
    return {s: eng.aggregate(s, by_sym.get(s, [])).to_dict(with_headlines=True) for s in symbols}


def _safe_headlines(fn, s):
    try:
        return fn(s)
    except Exception:
        return []


def _rationale(sym: str, row: pd.Series, rep: dict, sent: dict | None, n_liquid: int) -> str:
    parts = [f"#{int(row['rank'])} of {n_liquid} liquid stocks"]
    if pd.notna(row.get("mom_12_1")):
        parts.append(f"12-1m momentum {row['mom_12_1'] * 100:+.0f}%")
    if pd.notna(row.get("high_52w")):
        parts.append(f"{row['high_52w'] * 100:.0f}% of 52w high")
    c = rep.get("components", {})
    deep = ", ".join(f"{k.replace('_', ' ')} {v:+.2f}" for k, v in c.items() if v is not None)
    parts.append(f"deep: {deep}")
    ml = rep.get("ml") or {}
    if ml:
        parts.append(f"ML P(up {ml.get('horizon')}d) {ml.get('prob_up', 0):.2f} (AUC {ml.get('oos_auc') or 0:.2f})")
    if sent and sent.get("n_headlines"):
        parts.append(f"news {sent['score']:+.2f} over {sent['n_headlines']} headlines")
    return "; ".join(parts)


def deep_research(symbols: list[str], panel: MarketPanel, screen_table: pd.DataFrame, use_llm: bool | None = None,
                  workers: int = 4, progress=None) -> pd.DataFrame:
    s = get_settings()
    use_llm = s.llm.enabled if use_llm is None else use_llm
    symbols = [x for x in symbols if x in panel.close.columns]
    jobs = {x: panel.ohlcv(x) for x in symbols}
    reports: dict[str, dict | None] = {}
    with ThreadPoolExecutor(workers) as ex:
        futs = {sym: ex.submit(_analyze, sym, df) for sym, df in jobs.items()}
        for i, (sym, f) in enumerate(futs.items()):
            reports[sym] = f.result()
            if progress:
                progress((i + 1) / max(1, len(futs)), f"researched {sym}")
    news = _news(symbols, use_llm)
    n_liquid = int(screen_table["liquid"].sum())
    w = s.agent.screen_weight
    rows = []
    for sym in symbols:
        rep = reports.get(sym)
        row = screen_table.loc[sym] if sym in screen_table.index else pd.Series(dtype=object)
        sent = news.get(sym)
        screen_score = float(row.get("screen_score")) if pd.notna(row.get("screen_score", np.nan)) else -1.0
        if rep is None:
            rows.append({"symbol": sym, "screen_score": screen_score, "deep_score": None, "final_score": None,
                         "veto": "research failed", "report": None, "sentiment": sent})
            continue
        comps = dict(rep["components"])
        if sent and sent.get("n_headlines"):
            comps["sentiment"] = sent["score"] * sent["confidence"]
        deep = combine(pd.Series(comps, dtype=float), s.signals.weights)
        final = w * screen_score + (1 - w) * deep
        veto = None
        if sent and sent.get("n_headlines") and sent["score"] * sent["confidence"] <= -0.4:
            veto = f"strongly negative news ({sent['score']:+.2f})"
        rows.append({"symbol": sym, "screen_score": screen_score, "deep_score": float(deep), "final_score": float(final),
                     "veto": veto, "components": comps, "report": rep, "sentiment": sent,
                     "rationale": _rationale(sym, row, rep, sent, n_liquid) if len(row) else ""})
    return pd.DataFrame(rows).set_index("symbol") if rows else pd.DataFrame()
