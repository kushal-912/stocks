import numpy as np
import pytest

from quantdesk.models import ml, timeseries
from quantdesk.nlp.lexicon import score_text


def test_forecasts_produce_finite_bands(ohlcv):
    combo, parts = timeseries.ensemble_forecast(ohlcv["close"], 5)
    assert combo is not None and len(parts) >= 2
    assert np.isfinite(combo.exp_return) and combo.std > 0
    assert (combo.lower < combo.upper).all()


def test_garch(ohlcv):
    v = timeseries.garch_volatility(ohlcv["close"])
    assert 0.05 < v["forecast_vol"] < 1.0


def test_kalman_tracks_trend():
    import pandas as pd

    up = pd.Series(np.exp(np.linspace(0, 1, 500) + np.random.default_rng(0).normal(0, 0.005, 500)),
                   index=pd.bdate_range("2020-01-01", periods=500))
    kf = timeseries.kalman_trend(up)
    assert kf["slope"].iloc[-1] > 0


def test_ml_walk_forward_is_honest_on_random_walk(ohlcv):
    res = ml.evaluate(ohlcv, horizon=5, min_train=400, step=63)
    assert 0 <= res.prob_up <= 1
    assert res.n_oos > 100
    assert abs(res.oos_auc - 0.5) < 0.15  # a random walk should not look predictable


def test_lexicon_directions():
    assert score_text("Reliance profit jumps, beats estimates; brokerages raise target") > 0.3
    assert score_text("SEBI probe into promoter; shares plunge on fraud concerns") < -0.3
    assert score_text("Company not facing any fraud probe") > score_text("Company facing fraud probe")
