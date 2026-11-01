"""Headline scoring and recency-weighted aggregation into one sentiment signal per symbol."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from quantdesk.config import get_settings
from quantdesk.data.news import Headline, get_headlines
from quantdesk.nlp.lexicon import score_text
from quantdesk.nlp.llm import ClaudeClient, LLMUnavailable, SentimentCache

log = logging.getLogger(__name__)

_IMPACT_W = {"low": 0.5, "medium": 1.0, "high": 1.6}


@dataclass
class ScoredHeadline:
    headline: Headline
    sentiment: float
    relevance: float
    impact: str
    event_type: str
    rationale: str
    method: str  # "llm" | "lexicon"

    def to_dict(self) -> dict:
        d = self.headline.to_dict()
        d.update(sentiment=self.sentiment, relevance=self.relevance, impact=self.impact,
                 event_type=self.event_type, rationale=self.rationale, method=self.method)
        return d


@dataclass
class SentimentSignal:
    symbol: str
    score: float            # [-1, 1]
    confidence: float       # [0, 1], grows with the number/relevance of fresh headlines
    n_headlines: int
    method: str
    headlines: list[ScoredHeadline] = field(default_factory=list)

    def to_dict(self, with_headlines: bool = False) -> dict:
        d = {"symbol": self.symbol, "score": self.score, "confidence": self.confidence,
             "n_headlines": self.n_headlines, "method": self.method}
        if with_headlines:
            d["headlines"] = [h.to_dict() for h in self.headlines]
        return d


class SentimentEngine:
    def __init__(self, use_llm: bool | None = None):
        cfg = get_settings()
        self.half_life_h = cfg.news.half_life_hours
        self.llm = ClaudeClient() if (use_llm if use_llm is not None else cfg.llm.enabled) else None
        self.cache = SentimentCache()
        self._warned = False

    def score_headlines(self, heads: list[Headline]) -> list[ScoredHeadline]:
        results: dict[str, dict] = {}
        if self.llm is not None and self.llm.available and heads:
            results = self.cache.get_many(h.uid for h in heads)
            todo = [h for h in heads if h.uid not in results]
            if todo:
                try:
                    fresh = self.llm.classify_headlines(
                        [{"id": h.uid, "symbol": h.symbol, "title": h.title, "summary": h.summary[:400]} for h in todo])
                    self.cache.put_many(fresh)
                    results.update(fresh)
                except LLMUnavailable as exc:
                    if not self._warned:
                        log.warning("LLM sentiment unavailable (%s); using finance lexicon.", exc)
                        self._warned = True
        out = []
        for h in heads:
            r = results.get(h.uid)
            if r:
                out.append(ScoredHeadline(h, r["sentiment"], r["relevance"], r["impact"], r["event_type"],
                                          r.get("rationale", ""), "llm"))
            else:
                s = score_text(f"{h.title}. {h.summary}")
                out.append(ScoredHeadline(h, s, 0.6, "medium" if abs(s) > 0.4 else "low", "other", "", "lexicon"))
        return out

    def aggregate(self, symbol: str, scored: list[ScoredHeadline]) -> SentimentSignal:
        now = datetime.now(timezone.utc)
        num = den = 0.0
        for s in scored:
            age_h = max(0.0, (now - s.headline.published).total_seconds() / 3600)
            w = 0.5 ** (age_h / self.half_life_h) * s.relevance * _IMPACT_W.get(s.impact, 1.0)
            num += w * s.sentiment
            den += w
        score = num / den if den > 0 else 0.0
        confidence = min(1.0, den / 3.0)  # ~3 fresh, relevant, medium-impact headlines => full confidence
        methods = {s.method for s in scored}
        method = "llm" if methods == {"llm"} else "mixed" if "llm" in methods else "lexicon"
        return SentimentSignal(symbol, float(score), float(confidence), len(scored), method if scored else "none", scored)

    def for_symbol(self, symbol: str) -> SentimentSignal:
        heads = get_headlines(symbol)
        return self.aggregate(symbol, self.score_headlines(heads))
