"""SQLite ledger shared by the autopilot (writer) and the API server (reader).

Everything the agent sees, decides and does is recorded here. The ``events`` table is a
hash chain (each row commits to the previous row's hash), so the evaluation record can be
verified as untampered with ``verify_chain()``.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from quantdesk.config import get_settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, account TEXT NOT NULL, level TEXT NOT NULL,
    kind TEXT NOT NULL, message TEXT NOT NULL, data TEXT, prev_hash TEXT NOT NULL, hash TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_events_acct ON events(account, id);

CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT, updated TEXT);

CREATE TABLE IF NOT EXISTS runs (
    day TEXT, account TEXT, phase TEXT, status TEXT, started TEXT, finished TEXT, info TEXT,
    PRIMARY KEY (day, account, phase));

CREATE TABLE IF NOT EXISTS scans (
    day TEXT, account TEXT, created TEXT, summary TEXT, PRIMARY KEY (day, account));

CREATE TABLE IF NOT EXISTS candidates (
    day TEXT, account TEXT, symbol TEXT, stage TEXT, sector TEXT, screen_score REAL, deep_score REAL,
    final_score REAL, verdict TEXT, data TEXT, PRIMARY KEY (day, account, symbol));

CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, day TEXT, account TEXT, symbol TEXT, action TEXT,
    qty INTEGER, price REAL, score REAL, reason TEXT, data TEXT, status TEXT);

CREATE TABLE IF NOT EXISTS agent_orders (
    order_id TEXT, account TEXT, ts TEXT, day TEXT, symbol TEXT, side TEXT, qty INTEGER, order_type TEXT,
    price REAL, trigger_price REAL, purpose TEXT, reason TEXT, decision_id INTEGER, status TEXT,
    filled_qty INTEGER, avg_price REAL, message TEXT, updated TEXT, PRIMARY KEY (order_id, account));

CREATE TABLE IF NOT EXISTS positions (
    account TEXT, symbol TEXT, qty INTEGER, entry_date TEXT, entry_price REAL, stop REAL, highest_close REAL,
    atr REAL, sector TEXT, entry_score REAL, thesis TEXT, updated TEXT, PRIMARY KEY (account, symbol));

CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT, account TEXT, symbol TEXT, sector TEXT, entry_date TEXT, exit_date TEXT,
    qty INTEGER, entry_price REAL, exit_price REAL, pnl REAL, ret REAL, costs REAL, holding_days INTEGER,
    entry_reason TEXT, exit_reason TEXT);

CREATE TABLE IF NOT EXISTS nav (
    account TEXT, day TEXT, equity REAL, cash REAL, invested REAL, n_positions INTEGER,
    bench_close REAL, bench2_close REAL, PRIMARY KEY (account, day));

CREATE TABLE IF NOT EXISTS nav_intraday (account TEXT, ts TEXT, equity REAL, PRIMARY KEY (account, ts));

CREATE TABLE IF NOT EXISTS paper_account (
    id INTEGER PRIMARY KEY CHECK (id = 1), created TEXT, initial_capital REAL, cash REAL,
    realised_pnl REAL, total_costs REAL, dividends REAL DEFAULT 0);
CREATE TABLE IF NOT EXISTS paper_positions (
    symbol TEXT PRIMARY KEY, qty INTEGER, avg_price REAL, product TEXT, opened TEXT);
CREATE TABLE IF NOT EXISTS paper_orders (
    order_id TEXT PRIMARY KEY, ts TEXT, symbol TEXT, side TEXT, qty INTEGER, order_type TEXT, price REAL,
    trigger_price REAL, product TEXT, validity TEXT, tag TEXT, status TEXT, filled_qty INTEGER, avg_price REAL,
    message TEXT, triggered INTEGER DEFAULT 0, checked_until TEXT, updated TEXT);
CREATE TABLE IF NOT EXISTS paper_fills (
    id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT, ts TEXT, symbol TEXT, side TEXT, qty INTEGER,
    price REAL, costs REAL, realised_pnl REAL);
CREATE TABLE IF NOT EXISTS paper_corporate_actions (
    symbol TEXT, ex_date TEXT, kind TEXT, value REAL, applied TEXT, PRIMARY KEY (symbol, ex_date, kind));
"""

_lock = threading.Lock()
_initialised: set[str] = set()


def db_path() -> Path:
    return get_settings().path("data/quantdesk.db")


def connect(path: Path | None = None) -> sqlite3.Connection:
    p = str(path or db_path())
    con = sqlite3.connect(p, timeout=30, isolation_level=None)  # autocommit; use tx() for transactions
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA busy_timeout=30000")
    if p not in _initialised:
        with _lock:
            con.executescript(SCHEMA)
            _initialised.add(p)
    return con


@contextmanager
def tx():
    con = connect()
    try:
        con.execute("BEGIN IMMEDIATE")
        yield con
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    finally:
        con.close()


def query(sql: str, params: tuple | dict = ()) -> list[dict]:
    con = connect()
    try:
        return [dict(r) for r in con.execute(sql, params).fetchall()]
    finally:
        con.close()


def query_one(sql: str, params: tuple | dict = ()) -> dict | None:
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql: str, params: tuple | dict = ()) -> int:
    con = connect()
    try:
        cur = con.execute(sql, params)
        return cur.lastrowid
    finally:
        con.close()


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------- key/value
def kv_get(key: str, default=None):
    row = query_one("SELECT value FROM kv WHERE key=?", (key,))
    return json.loads(row["value"]) if row else default


def kv_set(key: str, value) -> None:
    execute("INSERT INTO kv(key, value, updated) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
            "updated=excluded.updated", (key, json.dumps(value, default=str), now()))


# ---------------------------------------------------------------- hash-chained event log
def _hash(prev: str, ts: str, account: str, level: str, kind: str, message: str, data: str) -> str:
    return hashlib.sha256("|".join([prev, ts, account, level, kind, message, data]).encode()).hexdigest()


def log_event(account: str, kind: str, message: str, data: dict | None = None, level: str = "info") -> None:
    payload = json.dumps(data or {}, default=str, sort_keys=True)
    with tx() as con:
        row = con.execute("SELECT hash FROM events ORDER BY id DESC LIMIT 1").fetchone()
        prev = row["hash"] if row else "GENESIS"
        ts = datetime.now().isoformat(timespec="milliseconds")
        h = _hash(prev, ts, account, level, kind, message, payload)
        con.execute("INSERT INTO events(ts, account, level, kind, message, data, prev_hash, hash) VALUES(?,?,?,?,?,?,?,?)",
                    (ts, account, level, kind, message, payload, prev, h))


def verify_chain() -> dict:
    prev = "GENESIS"
    n = 0
    for r in query("SELECT * FROM events ORDER BY id"):
        if r["prev_hash"] != prev or _hash(prev, r["ts"], r["account"], r["level"], r["kind"], r["message"],
                                           r["data"]) != r["hash"]:
            return {"ok": False, "broken_at_id": r["id"], "checked": n}
        prev = r["hash"]
        n += 1
    return {"ok": True, "checked": n, "head": prev}


# ---------------------------------------------------------------- phases
def phase_status(day: str, account: str, phase: str) -> str | None:
    row = query_one("SELECT status FROM runs WHERE day=? AND account=? AND phase=?", (day, account, phase))
    return row["status"] if row else None


def set_phase(day: str, account: str, phase: str, status: str, info: dict | None = None) -> None:
    ts = now()
    execute("INSERT INTO runs(day, account, phase, status, started, finished, info) VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(day, account, phase) DO UPDATE SET status=excluded.status, "
            "finished=CASE WHEN excluded.status IN ('done','failed','skipped') THEN excluded.finished ELSE runs.finished END, "
            "info=excluded.info",
            (day, account, phase, status, ts, ts if status in ("done", "failed", "skipped") else None,
             json.dumps(info or {}, default=str)))
