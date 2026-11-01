"""Finance-specific lexicon sentiment (offline fallback when no LLM is available).

Word lists are a compact subset in the spirit of Loughran-McDonald (2011), extended with phrases
common in Indian market news. General-purpose lexicons misread finance text ("liability",
"tax", "crude" are not negative here), which is why a domain list matters.
"""
from __future__ import annotations

import re

POSITIVE_PHRASES = {
    "beats estimates": 2.0, "beat estimates": 2.0, "record high": 1.5, "all-time high": 1.5, "order win": 1.5,
    "bags order": 1.5, "wins order": 1.5, "secures order": 1.5, "rating upgrade": 2.0, "upgraded to buy": 2.0,
    "target raised": 1.5, "raises target": 1.5, "buyback": 1.2, "bonus issue": 1.0, "dividend hike": 1.2,
    "profit jumps": 2.0, "profit rises": 1.5, "net profit up": 1.5, "margin expansion": 1.5, "strong guidance": 1.8,
    "raises guidance": 1.8, "debt free": 1.2, "stake buy": 1.0, "fii buying": 1.0, "52-week high": 1.2,
}
NEGATIVE_PHRASES = {
    "misses estimates": -2.0, "missed estimates": -2.0, "rating downgrade": -2.0, "downgraded to sell": -2.0,
    "target cut": -1.5, "cuts target": -1.5, "profit falls": -2.0, "profit drops": -2.0, "net loss": -1.8,
    "margin pressure": -1.5, "weak guidance": -1.8, "cuts guidance": -1.8, "sebi probe": -2.0, "ed raid": -2.0,
    "income tax raid": -2.0, "show cause notice": -1.5, "promoter pledge": -1.5, "stake sale": -0.8,
    "block deal": -0.3, "fii selling": -1.0, "52-week low": -1.2, "default on": -2.5, "credit rating cut": -2.0,
    "under investigation": -1.8, "class action": -1.5, "recall": -1.2, "plunges": -2.0, "tanks": -2.0,
}
POSITIVE_WORDS = {
    "surge", "surges", "soar", "soars", "rally", "rallies", "gain", "gains", "jump", "jumps", "rise", "rises",
    "strong", "robust", "record", "upgrade", "outperform", "beat", "beats", "growth", "profit", "profitable",
    "expansion", "boost", "boosts", "bullish", "optimistic", "approval", "approved", "breakthrough", "win",
    "wins", "accelerate", "improved", "improves", "recovery", "rebound", "upbeat", "buy", "overweight",
}
NEGATIVE_WORDS = {
    "fall", "falls", "drop", "drops", "decline", "declines", "slump", "slumps", "plunge", "crash", "loss",
    "losses", "weak", "weaker", "downgrade", "underperform", "miss", "misses", "bearish", "concern", "concerns",
    "risk", "risks", "fraud", "penalty", "fine", "lawsuit", "probe", "investigation", "default", "delay",
    "delays", "halt", "halted", "slowdown", "resign", "resigns", "exit", "layoffs", "warning", "sell",
    "underweight", "pressure", "volatile", "uncertainty", "cut", "cuts", "scam", "ban", "banned",
}
NEGATIONS = {"no", "not", "never", "without", "fails", "failed", "despite"}
_TOKEN = re.compile(r"[a-z0-9\-']+")


def score_text(text: str) -> float:
    """Sentiment in [-1, 1]."""
    t = text.lower()
    total, hits = 0.0, 0.0
    for phrase, w in {**POSITIVE_PHRASES, **NEGATIVE_PHRASES}.items():
        if phrase in t:
            total += w
            hits += abs(w)
            t = t.replace(phrase, " ")
    tokens = _TOKEN.findall(t)
    for i, tok in enumerate(tokens):
        val = 1.0 if tok in POSITIVE_WORDS else -1.0 if tok in NEGATIVE_WORDS else 0.0
        if not val:
            continue
        if any(tokens[j] in NEGATIONS for j in range(max(0, i - 3), i)):
            val = -val
        total += val
        hits += 1
    if hits == 0:
        return 0.0
    return max(-1.0, min(1.0, total / (hits + 1)))
