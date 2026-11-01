"""Full multi-model analysis of one symbol -> explainable composite score + trade plan."""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from quantdesk.analytics import indicators as ind
from quantdesk.analytics import risk as rk
from quantdesk.analytics.portfolio import kelly_fraction
from quantdesk.analytics.stats import diagnose
from quantdesk.config import get_settings
from quantdesk.data.market import get_history
from quantdesk.risk.sizing import position_size
from quantdesk.strategies.composite import combine, technical_components

log = logging.getLogger(__name__)


@dataclass
class SymbolReport:
    symbol: str
    last_close: float
    last_date: str
    score: float
    action: str                          # BUY | HOLD | SELL
    components: dict[str, float]
    plan: dict
    forecast: dict | None = None
    forecasts: list[dict] = field(default_factory=list)
    volatility: dict | None = None
    ml: dict | None = None
    sentiment: dict | None = None
    risk: dict = field(default_factory=dict)
    diagnostics: dict = field(default_factory=dict)
    regime: str | None = None
    notes: list[str] = field(default_factory=list)
    # Heavy objects for the UI (not serialised)
    frames: dict = field(default_factory=dict, repr=False)

    def to_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if k != "frames"}
        return _jsonable(d)


def _jsonable(o):
    if isinstance(o, dict):
        return {str(k): _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    if isinstance(o, (np.floating, float)):
        return None if not math.isfinite(float(o)) else round(float(o), 6)
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, pd.Timestamp):
        return o.isoformat()
    return o


def analyze_symbol(symbol: str, df: pd.DataFrame | None = None, with_ml: bool = True, with_forecast: bool = True,
                   sentiment_engine=None, capital: float | None = None) -> SymbolReport:
    s = get_settings()
    sig = s.signals
    df = df if df is not None else get_history(symbol)
    if df is None or len(df) < 260:
        raise ValueError(f"{symbol}: need at least ~1 year of daily data (got {0 if df is None else len(df)} bars)")
    close = df["close"]
    notes: list[str] = []

    comps_ts = technical_components(df)
    comps = comps_ts.iloc[-1].to_dict()

    fc_main, fc_list, vol = None, [], None
    if with_forecast:
        from quantdesk.models.timeseries import ensemble_forecast, garch_volatility

        try:
            fc_main, fc_list = ensemble_forecast(close, sig.forecast_horizon_days)
            if fc_main is not None:
                comps["forecast"] = 0.5 * comps["forecast"] + 0.5 * float(np.tanh(2 * fc_main.t_stat))
        except Exception as exc:
            notes.append(f"forecast failed: {exc}")
        try:
            vol = garch_volatility(close, sig.forecast_horizon_days)
        except Exception as exc:
            notes.append(f"GARCH failed: {exc}")

    ml_res = None
    if with_ml:
        from quantdesk.models.ml import evaluate

        try:
            ml_res = evaluate(df, horizon=sig.forecast_horizon_days)
            skill = float(np.clip((ml_res.oos_auc - 0.5) / 0.05, 0, 1)) if np.isfinite(ml_res.oos_auc) else 0.0
            comps["ml"] = (2 * ml_res.prob_up - 1) * skill
            if skill == 0:
                notes.append(f"ML has no out-of-sample skill (AUC {ml_res.oos_auc:.3f}); its vote is zeroed.")
        except Exception as exc:
            notes.append(f"ML failed: {exc}")

    sent = None
    if sentiment_engine is not None:
        try:
            sent = sentiment_engine.for_symbol(symbol)
            comps["sentiment"] = sent.score * sent.confidence if sent.n_headlines else np.nan
        except Exception as exc:
            notes.append(f"sentiment failed: {exc}")

    score = combine(pd.Series(comps), sig.weights)
    action = "BUY" if score >= sig.buy_threshold else "SELL" if score <= sig.sell_threshold else "HOLD"

    # ----- trade plan -----
    last = float(close.iloc[-1])
    a = float(ind.atr(df).iloc[-1])
    stop = last - sig.atr_stop_multiple * a
    target = last + sig.reward_risk * (last - stop)
    ann_vol = (vol or {}).get("forecast_vol") or float(np.log(close).diff().iloc[-63:].std() * np.sqrt(252))
    p_heur = float(np.clip(0.5 + 0.15 * score, 0.35, 0.65))
    p_win = 0.5 * p_heur + 0.5 * ml_res.prob_up if ml_res is not None and comps.get("ml", 0) != 0 else p_heur
    kelly_cap = s.risk.kelly_fraction * kelly_fraction(p_win, sig.reward_risk)
    size = position_size(capital or s.risk.capital, last, stop, s.risk.risk_per_trade_pct, s.risk.max_position_pct,
                         ann_vol, s.risk.vol_target_annual, kelly_cap)
    plan = {"entry": round(last, 2), "entry_limit": round(last * 1.003, 2), "stop": round(stop, 2),
            "target": round(target, 2), "atr": round(a, 2), "reward_risk": sig.reward_risk,
            "quantity": size["quantity"] if action == "BUY" else 0, "sizing_binding": size["binding"],
            "sizing_limits": size["limits"], "p_win_est": round(p_win, 3), "kelly_cap": round(kelly_cap, 4),
            "ann_vol": round(ann_vol, 4)}

    year = close.iloc[-252:]
    risk_stats = rk.summary(year)
    try:
        diag = diagnose(close.iloc[-756:])
    except Exception as exc:
        diag = {"error": str(exc)}

    regime = None
    try:
        from quantdesk.models.regime import detect_regimes

        reg = detect_regimes(close.iloc[-1260:])
        regime = str(reg["label"].iloc[-1]) if len(reg) and reg["label"].notna().any() else None
    except Exception:
        pass

    return SymbolReport(
        symbol=symbol, last_close=last, last_date=str(close.index[-1].date()), score=float(score), action=action,
        components={k: (None if v is None or (isinstance(v, float) and np.isnan(v)) else float(v)) for k, v in comps.items()},
        plan=plan, forecast=fc_main.to_dict() if fc_main else None, forecasts=[f.to_dict() for f in fc_list],
        volatility={k: v for k, v in (vol or {}).items() if k != "path"} or None,
        ml=ml_res.to_dict() | {"top_features": ml_res.importances} if ml_res else None,
        sentiment=sent.to_dict(with_headlines=True) if sent else None,
        risk=risk_stats, diagnostics=diag, regime=regime, notes=notes,
        frames={"df": df, "components": comps_ts, "forecast": fc_main, "forecasts": fc_list,
                "ml_proba": ml_res.oos_proba if ml_res else None, "garch": vol},
    )
