"""Option pricing: Black-Scholes(-Merton) with greeks, implied volatility, CRR binomial trees."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.optimize import brentq
from scipy.stats import norm


@dataclass
class Greeks:
    price: float
    delta: float
    gamma: float
    vega: float    # per 1.00 change in vol (divide by 100 for per vol-point)
    theta: float   # per year (divide by 365 for per day)
    rho: float


def _d1d2(S, K, T, r, sigma, q=0.0):
    d1 = (np.log(S / K) + (r - q + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
    return d1, d1 - sigma * np.sqrt(T)


def black_scholes(S: float, K: float, T: float, r: float, sigma: float, kind: str = "call", q: float = 0.0) -> Greeks:
    """European option under Black-Scholes-Merton with continuous dividend yield q."""
    if T <= 0 or sigma <= 0:
        intrinsic = max(S - K, 0) if kind == "call" else max(K - S, 0)
        return Greeks(intrinsic, float(kind == "call" and S > K) - float(kind == "put" and S < K), 0, 0, 0, 0)
    d1, d2 = _d1d2(S, K, T, r, sigma, q)
    disc_q, disc_r = np.exp(-q * T), np.exp(-r * T)
    gamma = disc_q * norm.pdf(d1) / (S * sigma * np.sqrt(T))
    vega = S * disc_q * norm.pdf(d1) * np.sqrt(T)
    if kind == "call":
        price = S * disc_q * norm.cdf(d1) - K * disc_r * norm.cdf(d2)
        delta = disc_q * norm.cdf(d1)
        theta = -S * disc_q * norm.pdf(d1) * sigma / (2 * np.sqrt(T)) - r * K * disc_r * norm.cdf(d2) + q * S * disc_q * norm.cdf(d1)
        rho = K * T * disc_r * norm.cdf(d2)
    else:
        price = K * disc_r * norm.cdf(-d2) - S * disc_q * norm.cdf(-d1)
        delta = -disc_q * norm.cdf(-d1)
        theta = -S * disc_q * norm.pdf(d1) * sigma / (2 * np.sqrt(T)) + r * K * disc_r * norm.cdf(-d2) - q * S * disc_q * norm.cdf(-d1)
        rho = -K * T * disc_r * norm.cdf(-d2)
    return Greeks(float(price), float(delta), float(gamma), float(vega), float(theta), float(rho))


def implied_vol(price: float, S: float, K: float, T: float, r: float, kind: str = "call", q: float = 0.0) -> float:
    """Invert Black-Scholes for sigma with Brent's method."""
    f = lambda s: black_scholes(S, K, T, r, s, kind, q).price - price
    try:
        return float(brentq(f, 1e-4, 5.0, xtol=1e-8))
    except ValueError:
        return float("nan")


def binomial_crr(S: float, K: float, T: float, r: float, sigma: float, steps: int = 500,
                 kind: str = "call", american: bool = True, q: float = 0.0) -> float:
    """Cox-Ross-Rubinstein tree; handles early exercise (American options)."""
    dt = T / steps
    u = np.exp(sigma * np.sqrt(dt))
    d = 1 / u
    p = (np.exp((r - q) * dt) - d) / (u - d)
    disc = np.exp(-r * dt)
    ST = S * u ** np.arange(steps, -1, -1) * d ** np.arange(0, steps + 1)
    payoff = np.maximum(ST - K, 0) if kind == "call" else np.maximum(K - ST, 0)
    for i in range(steps - 1, -1, -1):
        payoff = disc * (p * payoff[:-1] + (1 - p) * payoff[1:])
        if american:
            ST = ST[:-1] / u
            exercise = np.maximum(ST - K, 0) if kind == "call" else np.maximum(K - ST, 0)
            payoff = np.maximum(payoff, exercise)
    return float(payoff[0])
