import numpy as np
import pandas as pd
import pytest

from quantdesk.analytics import indicators as ind
from quantdesk.analytics import options, portfolio, risk, stats
from quantdesk.analytics.simulation import gbm_paths


def test_rsi_bounds_and_extremes():
    up = pd.Series(np.arange(1, 60, dtype=float))
    assert ind.rsi(up).iloc[-1] == pytest.approx(100.0)
    noisy = pd.Series(100 + np.random.default_rng(0).normal(0, 1, 500).cumsum())
    r = ind.rsi(noisy).dropna()
    assert ((r >= 0) & (r <= 100)).all()


def test_sma_ema_basic():
    s = pd.Series([1.0, 2, 3, 4, 5])
    assert ind.sma(s, 5).iloc[-1] == 3
    assert ind.ema(s, 3).iloc[-1] > 3


def test_indicators_have_no_lookahead(ohlcv):
    full = ind.feature_frame(ohlcv)
    partial = ind.feature_frame(ohlcv.iloc[:600])
    pd.testing.assert_frame_equal(full.iloc[:600].dropna(), partial.dropna(), check_freq=False)


def test_max_drawdown_known():
    eq = pd.Series([100, 120, 90, 130, 65, 140.0], index=pd.bdate_range("2024-01-01", periods=6))
    assert risk.max_drawdown(eq) == pytest.approx(65 / 130 - 1)


def test_var_cvar_ordering():
    r = pd.Series(np.random.default_rng(1).standard_t(4, 5000) * 0.01)
    assert risk.cvar(r) >= risk.var_historical(r) > 0


def test_hurst_random_walk_and_trend():
    rw = pd.Series(np.exp(np.random.default_rng(2).normal(0, 0.01, 3000).cumsum()))
    assert 0.4 < stats.hurst_exponent(rw) < 0.6
    ou = [0.0]
    rng = np.random.default_rng(3)
    for _ in range(3000):
        ou.append(ou[-1] * 0.8 + rng.normal(0, 0.01))
    assert stats.hurst_exponent(pd.Series(np.exp(ou))) < 0.4


def test_half_life_of_ou():
    rng = np.random.default_rng(4)
    x = [0.0]
    for _ in range(5000):
        x.append(x[-1] * 0.95 + rng.normal(0, 1))
    assert stats.half_life(pd.Series(x)) == pytest.approx(np.log(2) / -np.log(0.95), rel=0.25)


def test_black_scholes_put_call_parity_and_iv():
    S, K, T, r, sig = 100, 105, 0.5, 0.07, 0.25
    c = options.black_scholes(S, K, T, r, sig, "call").price
    p = options.black_scholes(S, K, T, r, sig, "put").price
    assert c - p == pytest.approx(S - K * np.exp(-r * T), abs=1e-8)
    assert options.implied_vol(c, S, K, T, r, "call") == pytest.approx(sig, abs=1e-6)
    assert options.binomial_crr(S, K, T, r, sig, 800, "call", american=False) == pytest.approx(c, abs=0.02)


def test_portfolio_weights_sum_to_one():
    rng = np.random.default_rng(5)
    rets = pd.DataFrame(rng.normal(0.0005, 0.01, (500, 4)), columns=list("ABCD"))
    cov = portfolio.shrunk_covariance(rets)
    for w in (portfolio.min_variance(cov), portfolio.risk_parity(cov), portfolio.hrp(rets),
              portfolio.max_sharpe(rets.mean() * 252, cov)):
        assert w.sum() == pytest.approx(1.0, abs=1e-6)
        assert (w >= -1e-9).all()


def test_kelly():
    assert portfolio.kelly_fraction(0.6, 1.0) == pytest.approx(0.2)
    assert portfolio.kelly_fraction(0.4, 1.0) == 0.0


def test_gbm_shape_and_drift():
    p = gbm_paths(100, 0.1, 0.2, 252, 4000, seed=0)
    assert p.shape == (253, 4000)
    assert np.mean(p[-1]) == pytest.approx(100 * np.exp(0.1), rel=0.03)
