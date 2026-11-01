"""HTTP API for the web UI.

Read-mostly: it serves what the autopilot recorded in the ledger. It never talks to the broker,
so the UI cannot interfere with trading. The only writes are the kill switch and starting a
backtest job.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from quantdesk.config import ROOT, env, get_settings
from quantdesk.data.calendar import is_market_open, now_ist
from quantdesk.risk.compliance import is_killed, kill_reason, set_kill
from quantdesk.store import db

log = logging.getLogger("quantdesk.server")
app = FastAPI(title="QuantDesk", docs_url="/api/docs", openapi_url="/api/openapi.json")
app.add_middleware(GZipMiddleware, minimum_size=2000)
_pool = ThreadPoolExecutor(2)
_research_cache: dict[str, tuple[float, dict]] = {}
_bt_state: dict = {"status": "idle"}


def _clean(o):
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (float, np.floating)):
        return None if not np.isfinite(o) else float(o)
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, (pd.Timestamp, datetime)):
        return o.isoformat()
    return o


def ok(data) -> JSONResponse:
    return JSONResponse(_clean(data))


def account() -> str:
    s = get_settings()
    return s.execution.broker if s.execution.mode == "live" else "paper"


def _json(s):
    try:
        return json.loads(s) if s else None
    except (TypeError, json.JSONDecodeError):
        return s


# ------------------------------------------------------------------ status / overview
@app.get("/api/status")
def status():
    s = get_settings()
    acct = account()
    hb = db.kv_get(f"heartbeat:{acct}")
    age = (datetime.now() - datetime.fromisoformat(hb["ts"])).total_seconds() if hb else None
    day = now_ist().date().isoformat()
    phases = {r["phase"]: {"status": r["status"], "finished": r["finished"], "info": _json(r["info"])}
              for r in db.query("SELECT * FROM runs WHERE day=? AND account=?", (day, acct))}
    return ok({"account": acct, "mode": s.execution.mode, "broker": s.execution.broker if s.execution.mode == "live" else "paper",
               "now": now_ist().isoformat(timespec="seconds"), "market_open": is_market_open(),
               "day_status": db.kv_get(f"daystatus:{day}"), "phases": phases,
               "autopilot": {"alive": age is not None and age < 180, "last_heartbeat": hb["ts"] if hb else None,
                             "age_seconds": age},
               "kill_switch": {"on": is_killed(), "reason": kill_reason()},
               "llm": {"configured": bool(env("ANTHROPIC_API_KEY")), "model": s.llm.model, "review": s.agent.llm_review},
               "inception": db.kv_get(f"inception:{acct}"),
               "schedule": s.agent.schedule.model_dump()})


@app.get("/api/overview")
def overview():
    from quantdesk.trader.evaluation import evaluate

    acct = account()
    snap = db.kv_get(f"snapshot:{acct}") or {}
    ev = evaluate(acct)
    prev = db.query("SELECT equity FROM nav WHERE account=? AND day<? ORDER BY day DESC LIMIT 1",
                    (acct, now_ist().date().isoformat()))
    base = prev[0]["equity"] if prev else (snap.get("initial_capital") or None)
    today_pnl = (snap["equity"] - base) if snap.get("equity") and base else None
    intraday = db.query("SELECT ts, equity FROM nav_intraday WHERE account=? AND ts>=? ORDER BY ts",
                        (acct, now_ist().date().isoformat()))
    plan = db.query("SELECT * FROM decisions WHERE account=? AND action IN ('BUY','SELL') ORDER BY id DESC LIMIT 12", (acct,))
    return ok({"snapshot": snap, "today_pnl": today_pnl, "today_base": base, "metrics": ev.get("metrics"),
               "curve": ev.get("curve", []), "verdict": ev.get("verdict"), "intraday": intraday,
               "recent_decisions": [{**d, "data": _json(d["data"])} for d in plan],
               "events": db.query("SELECT id, ts, level, kind, message FROM events WHERE account IN (?, 'system') "
                                  "ORDER BY id DESC LIMIT 25", (acct,))})


# ------------------------------------------------------------------ portfolio
@app.get("/api/positions")
def positions():
    acct = account()
    snap = db.kv_get(f"snapshot:{acct}") or {}
    meta = {r["symbol"]: r for r in db.query("SELECT * FROM positions WHERE account=?", (acct,))}
    out = []
    for p in snap.get("positions", []):
        m = meta.get(p["symbol"], {})
        out.append({**p, "highest_close": m.get("highest_close"), "atr": m.get("atr"), "thesis": _json(m.get("thesis"))})
    return ok({"positions": out, "snapshot_ts": snap.get("ts"), "cash": snap.get("cash"), "equity": snap.get("equity")})


@app.get("/api/trades")
def trades():
    return ok(db.query("SELECT * FROM trades WHERE account=? ORDER BY exit_date DESC, id DESC", (account(),)))


@app.get("/api/orders")
def orders(limit: int = 300):
    return ok(db.query("SELECT * FROM agent_orders WHERE account=? ORDER BY ts DESC LIMIT ?", (account(), limit)))


@app.get("/api/events")
def events(limit: int = 300, kind: str | None = None):
    if kind:
        rows = db.query("SELECT * FROM events WHERE kind=? ORDER BY id DESC LIMIT ?", (kind, limit))
    else:
        rows = db.query("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))
    for r in rows:
        r["data"] = _json(r["data"])
    return ok(rows)


@app.get("/api/decisions")
def decisions(day: str | None = None):
    acct = account()
    if day is None:
        row = db.query_one("SELECT MAX(day) AS d FROM decisions WHERE account=?", (acct,))
        day = row["d"] if row else None
    rows = db.query("SELECT * FROM decisions WHERE account=? AND day=? ORDER BY id", (acct, day)) if day else []
    for r in rows:
        r["data"] = _json(r["data"])
    return ok({"day": day, "decisions": rows})


# ------------------------------------------------------------------ research
@app.get("/api/scan/days")
def scan_days():
    return ok([r["day"] for r in db.query("SELECT day FROM scans WHERE account=? ORDER BY day DESC LIMIT 120", (account(),))])


@app.get("/api/scan")
def scan(day: str | None = None):
    acct = account()
    row = (db.query_one("SELECT * FROM scans WHERE account=? AND day=?", (acct, day)) if day else
           db.query_one("SELECT * FROM scans WHERE account=? ORDER BY day DESC LIMIT 1", (acct,)))
    if not row:
        return ok({"day": None, "summary": None, "candidates": []})
    cands = db.query("SELECT * FROM candidates WHERE account=? AND day=? ORDER BY COALESCE(final_score, -9) DESC, "
                     "screen_score DESC", (acct, row["day"]))
    for c in cands:
        c["data"] = _json(c["data"])
    return ok({"day": row["day"], "created": row["created"], "summary": _json(row["summary"]), "candidates": cands})


def _universe_row(symbol: str) -> dict:
    try:
        from quantdesk.data.universe import load_universe

        u = load_universe(max_age_days=365)
        r = u[u["symbol"] == symbol]
        info = r.iloc[0].to_dict() if len(r) else {}
        if info.get("sector", "Other") == "Other":
            extra = get_settings().path("data/sectors_extra.json")
            if extra.exists():
                info["sector"] = json.loads(extra.read_text(encoding="utf-8")).get(symbol, "Other")
        return info
    except Exception:
        return {}


@app.get("/api/stock/{symbol}")
def stock(symbol: str, days: int = 500):
    from quantdesk.analytics import indicators as ind
    from quantdesk.data.panel import MarketPanel

    symbol = symbol.upper()
    panel = MarketPanel.load()
    if symbol not in panel.close.columns:
        from quantdesk.data.market import get_history

        df = get_history(symbol, years=3)
        if df.empty:
            raise HTTPException(404, f"No data for {symbol}")
    else:
        df = panel.ohlcv(symbol)
    df = df.iloc[-max(days, 260):]
    sma50, sma200 = ind.sma(df["close"], 50), ind.sma(df["close"], 200)
    view = df.iloc[-days:]
    bars = [{"time": d.strftime("%Y-%m-%d"), "open": r.open, "high": r.high, "low": r.low, "close": r.close,
             "volume": r.volume} for d, r in view.iterrows()]
    acct = account()
    research = None
    for d in db.query("SELECT key FROM kv WHERE key LIKE 'research:%' ORDER BY key DESC LIMIT 10"):
        rs = db.kv_get(d["key"]) or {}
        if symbol in rs:
            research = {"day": d["key"].split(":", 1)[1], **rs[symbol]}
            break
    cand = db.query_one("SELECT * FROM candidates WHERE account=? AND symbol=? ORDER BY day DESC LIMIT 1", (acct, symbol))
    if cand:
        cand["data"] = _json(cand["data"])
    return ok({"symbol": symbol, "info": _universe_row(symbol), "bars": bars,
               "sma50": [{"time": d.strftime("%Y-%m-%d"), "value": v} for d, v in sma50.loc[view.index].dropna().items()],
               "sma200": [{"time": d.strftime("%Y-%m-%d"), "value": v} for d, v in sma200.loc[view.index].dropna().items()],
               "orders": db.query("SELECT * FROM agent_orders WHERE account=? AND symbol=? AND status='COMPLETE' ORDER BY ts",
                                  (acct, symbol)),
               "trades": db.query("SELECT * FROM trades WHERE account=? AND symbol=? ORDER BY exit_date", (acct, symbol)),
               "position": db.query_one("SELECT * FROM positions WHERE account=? AND symbol=?", (acct, symbol)),
               "research": research, "candidate": cand})


@app.post("/api/stock/{symbol}/research")
def research_now(symbol: str):
    """Run the full deep research on any stock on demand (cached 30 minutes)."""
    symbol = symbol.upper()
    hit = _research_cache.get(symbol)
    if hit and time.time() - hit[0] < 1800:
        return ok(hit[1])
    from quantdesk.engine.analyzer import analyze_symbol
    from quantdesk.nlp.sentiment import SentimentEngine

    try:
        rep = analyze_symbol(symbol, sentiment_engine=SentimentEngine()).to_dict()
    except Exception as exc:
        raise HTTPException(400, str(exc))
    out = {"day": now_ist().date().isoformat(), "report": rep, "sentiment": rep.get("sentiment"), "on_demand": True}
    _research_cache[symbol] = (time.time(), out)
    return ok(out)


@app.get("/api/search")
def search(q: str):
    from quantdesk.data.universe import load_universe

    q = q.strip().upper()
    if not q:
        return ok([])
    u = load_universe(max_age_days=365)
    m = u[u["symbol"].str.startswith(q) | u["name"].str.upper().str.contains(q, regex=False)]
    return ok(m.head(12)[["symbol", "name", "sector"]].to_dict("records"))


# ------------------------------------------------------------------ evaluation / backtest
@app.get("/api/evaluation")
def evaluation():
    from quantdesk.trader.evaluation import evaluate

    return ok(evaluate(account()))


def _bt_path() -> Path:
    return get_settings().path("data/backtest/latest.json")


_bt_lock = threading.Lock()
_bt_events: list[dict] = []
_bt_cancel = threading.Event()


def _bt_emit(evt: dict) -> None:
    with _bt_lock:
        evt["seq"] = len(_bt_events)
        _bt_events.append(_clean(evt))


def run_backtest_job(start: str, capital: float, end: str | None = None, echo=None) -> None:
    from quantdesk.data.panel import MarketPanel
    from quantdesk.data.universe import agent_universe
    from quantdesk.trader.backtest import BacktestCancelled, backtest_policy

    with _bt_lock:
        _bt_events.clear()
    _bt_cancel.clear()
    _bt_state.clear()
    _bt_state.update(status="running", started=db.now(), progress=0.0, message="loading price data",
                     params={"start": start, "end": end, "capital": capital})
    try:
        panel = MarketPanel.load().completed()
        res = backtest_policy(panel, agent_universe(max_age_days=365), start, end, capital=capital,
                              progress=lambda f, m: _bt_state.update(progress=f, message=m),
                              on_event=(lambda e: (_bt_emit(e), echo(e))) if echo else _bt_emit,
                              should_stop=_bt_cancel.is_set)
        tr = res["trades"]
        out = {"params": {"start": start, "end": end, "capital": capital}, "finished": db.now(), "metrics": res["metrics"],
               "curve": [{"day": d.strftime("%Y-%m-%d"), "equity": float(v),
                          "benchmark": float(res["benchmark"].loc[d]) if res["benchmark"] is not None else None}
                         for d, v in res["equity"].items()],
               "trades": [] if tr.empty else tr.assign(entry_date=tr.entry_date.astype(str).str[:10],
                                                       exit_date=tr.exit_date.astype(str).str[:10]).to_dict("records"),
               "open_positions": res["open_positions"],
               "log": [e for e in _bt_events if e["type"] in ("buy", "sell", "skip", "note", "start")]}
        _bt_path().write_text(json.dumps(_clean(out)), encoding="utf-8")
        _bt_state.update(status="done", progress=1.0, message="done", finished=db.now())
    except BacktestCancelled:
        _bt_state.update(status="cancelled", message="cancelled by user")
    except Exception as exc:
        log.exception("backtest failed")
        _bt_state.update(status="failed", message=str(exc))


class BacktestReq(BaseModel):
    start: str = "2024-01-01"
    end: str | None = None
    capital: float = 50_000


@app.post("/api/backtest")
def start_backtest(req: BacktestReq):
    from quantdesk.trader.backtest import min_viable_capital

    if _bt_state.get("status") == "running":
        raise HTTPException(409, "A backtest is already running")
    floor = min_viable_capital()
    if req.capital < floor:
        s = get_settings()
        raise HTTPException(400, f"Capital too small: the agent needs at least ₹{floor:,.0f}. Each position must be at least "
                                 f"₹{s.agent.min_position_value:,.0f} (fixed charges make smaller trades uneconomic) and at most "
                                 f"{s.risk.max_position_pct:.0f}% of the account.")
    threading.Thread(target=run_backtest_job, args=(req.start, req.capital, req.end or None), daemon=True).start()
    return ok({"started": True})


@app.post("/api/backtest/cancel")
def cancel_backtest():
    _bt_cancel.set()
    return ok({"cancelling": True})


@app.get("/api/backtest/stream")
def backtest_stream(since: int = 0):
    """Incremental feed of backtest events (buys, sells, daily equity) for the live view."""
    with _bt_lock:
        evts = _bt_events[since:]
        total = len(_bt_events)
    return ok({"state": _bt_state, "events": evts, "next": total})


@app.get("/api/backtest")
def get_backtest():
    from quantdesk.trader.backtest import min_viable_capital

    p = _bt_path()
    return ok({"state": _bt_state, "min_capital": min_viable_capital(),
               "result": json.loads(p.read_text(encoding="utf-8")) if p.exists() else None})


# ------------------------------------------------------------------ controls
class KillReq(BaseModel):
    on: bool
    reason: str = "manual (web)"


@app.post("/api/kill")
def kill(req: KillReq):
    set_kill(req.on, req.reason)
    db.log_event(account(), "kill_switch", f"Kill switch {'ON' if req.on else 'OFF'} from web UI: {req.reason}",
                 level="warning" if req.on else "info")
    return ok({"on": is_killed(), "reason": kill_reason()})


@app.get("/api/verify")
def verify():
    return ok(db.verify_chain())


@app.get("/api/settings")
def settings_view():
    s = get_settings()
    return ok({"agent": s.agent.model_dump(), "risk": s.risk.model_dump(), "universe": s.universe.model_dump(),
               "execution": s.execution.model_dump(), "costs": {"profile": s.costs.profile},
               "llm": {"enabled": s.llm.enabled, "model": s.llm.model}})


# ------------------------------------------------------------------ static web app
_dist = ROOT / "web" / "dist"
if (_dist / "assets").exists():
    app.mount("/assets", StaticFiles(directory=_dist / "assets"), name="assets")


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str):
    if path.startswith("api/"):
        raise HTTPException(404)
    f = _dist / path
    if path and f.is_file():
        return FileResponse(f)
    index = _dist / "index.html"
    if index.exists():
        return FileResponse(index)
    return JSONResponse({"message": "Web UI not built. Run: cd web && npm install && npm run build"}, status_code=503)
