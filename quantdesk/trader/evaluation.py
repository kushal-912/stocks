"""The evaluation scorecard: how is the agent doing versus simply holding the index?"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from quantdesk.analytics import risk as rk
from quantdesk.store import db


def nav_frame(account: str) -> pd.DataFrame:
    rows = db.query("SELECT * FROM nav WHERE account=? ORDER BY day", (account,))
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["day"] = pd.to_datetime(df["day"])
    return df.set_index("day")


def _clean(o):
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_clean(v) for v in o]
    if isinstance(o, (float, np.floating)):
        return None if not np.isfinite(o) else float(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    return o


def evaluate(account: str) -> dict:
    inc = db.kv_get(f"inception:{account}") or {}
    cap = inc.get("capital")
    nav = nav_frame(account)
    trades = pd.DataFrame(db.query("SELECT * FROM trades WHERE account=? ORDER BY exit_date", (account,)))
    out: dict = {"account": account, "inception": inc, "days": int(len(nav))}
    if cap and len(nav):
        start = pd.Timestamp(inc["date"]) - pd.Timedelta(days=1)
        eq = pd.concat([pd.Series([cap], index=[start]), nav["equity"]])
        eq = eq[~eq.index.duplicated(keep="last")]
        b = nav["bench_close"].dropna()
        bench = None
        if len(b):
            first_b = b.iloc[0]
            bench = pd.concat([pd.Series([first_b], index=[start]), b]) / first_b * cap
        r = eq.pct_change().dropna()
        m = {
            "equity": float(eq.iloc[-1]), "total_return": float(eq.iloc[-1] / cap - 1),
            "max_drawdown": rk.max_drawdown(eq), "annual_vol": rk.annual_vol(r) if len(r) > 2 else None,
            "sharpe": rk.sharpe(r) if len(r) > 2 else None, "sortino": rk.sortino(r) if len(r) > 2 else None,
            "best_day": float(r.max()) if len(r) else None, "worst_day": float(r.min()) if len(r) else None,
            "avg_exposure": float((nav["invested"] / nav["equity"]).mean()),
            "cagr": rk.cagr(eq) if len(nav) >= 60 else None,
        }
        if bench is not None:
            br = bench.pct_change().dropna()
            m.update({"benchmark_return": float(bench.iloc[-1] / cap - 1), "excess_return": float(eq.iloc[-1] / bench.iloc[-1] - 1),
                      "benchmark_max_drawdown": rk.max_drawdown(bench)})
            if len(r) > 10:
                beta, alpha = rk.beta_alpha(r, br)
                active = (r - br).dropna()
                t = active.mean() / active.std(ddof=1) * np.sqrt(len(active)) if active.std(ddof=1) > 0 else 0.0
                m.update({"beta": beta, "alpha": alpha, "information_ratio": rk.information_ratio(r, br),
                          "alpha_t_stat": float(t)})
        out["metrics"] = m
        out["curve"] = [{"day": d.strftime("%Y-%m-%d"), "equity": float(v),
                         "benchmark": float(bench.loc[d]) if bench is not None and d in bench.index else None}
                        for d, v in eq.items()]
        monthly = eq.resample("ME").last()
        prev = pd.concat([pd.Series([cap]), monthly.iloc[:-1]]).to_numpy()
        mret = monthly.to_numpy() / prev - 1
        bm = None
        if bench is not None:
            bmon = bench.resample("ME").last()
            bm = bmon.to_numpy() / np.r_[cap, bmon.iloc[:-1].to_numpy()] - 1
        out["monthly"] = [{"month": d.strftime("%Y-%m"), "return": float(x), "benchmark": float(bm[i]) if bm is not None else None}
                          for i, (d, x) in enumerate(zip(monthly.index, mret))]
    if len(trades):
        wins, losses = trades[trades.pnl > 0], trades[trades.pnl <= 0]
        out["trades"] = {
            "count": int(len(trades)), "win_rate": float(len(wins) / len(trades)),
            "avg_win": float(wins.pnl.mean()) if len(wins) else 0.0, "avg_loss": float(losses.pnl.mean()) if len(losses) else 0.0,
            "avg_win_pct": float(wins.ret.mean()) if len(wins) else 0.0, "avg_loss_pct": float(losses.ret.mean()) if len(losses) else 0.0,
            "profit_factor": float(wins.pnl.sum() / -losses.pnl.sum()) if losses.pnl.sum() < 0 else None,
            "expectancy": float(trades.pnl.mean()), "total_pnl": float(trades.pnl.sum()), "total_costs": float(trades.costs.sum()),
            "avg_holding_days": float(trades.holding_days.mean()),
            "by_exit_reason": trades.assign(reason=trades.exit_reason.str.split(":").str[0].str.split(" \\(").str[0])
                              .groupby("reason")["pnl"].agg(["count", "sum"]).reset_index().to_dict("records"),
        }
    runs = db.query("SELECT phase, status, COUNT(*) AS n FROM runs WHERE account=? GROUP BY phase, status", (account,))
    out["reliability"] = runs
    out["verdict"] = verdict(out)
    return _clean(out)


def verdict(ev: dict) -> list[dict]:
    m = ev.get("metrics") or {}
    days = ev.get("days", 0)
    checks = []
    if not m:
        return [{"check": "Track record", "status": "pending", "detail": "No completed trading days yet."}]
    ex = m.get("excess_return")
    if ex is not None:
        checks.append({"check": "Beating NIFTY 50", "status": "pass" if ex > 0 else "fail",
                       "detail": f"{ex * 100:+.2f}% vs buy-and-hold NIFTY"})
    dd, bdd = m.get("max_drawdown"), m.get("benchmark_max_drawdown")
    if dd is not None:
        checks.append({"check": "Drawdown control", "status": "pass" if dd > -0.15 else "warn" if dd > -0.25 else "fail",
                       "detail": f"max drawdown {dd * 100:.1f}%" + (f" (NIFTY {bdd * 100:.1f}%)" if bdd is not None else "")})
    if m.get("sharpe") is not None and days >= 20:
        s = m["sharpe"]
        checks.append({"check": "Risk-adjusted return", "status": "pass" if s > 1 else "warn" if s > 0.5 else "fail",
                       "detail": f"Sharpe {s:.2f}"})
    t = m.get("alpha_t_stat")
    checks.append({"check": "Statistical evidence", "status": "pass" if (t or 0) > 2 else "pending" if days < 120 else "warn",
                   "detail": (f"alpha t-stat {t:.2f}; " if t is not None else "") +
                             f"{days} trading days recorded - roughly 120+ are needed before any edge is distinguishable from luck"})
    return checks
