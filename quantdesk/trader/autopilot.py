"""Autopilot: the long-running scheduler that drives the agent through each trading day.

Restart-safe: every phase is recorded in the ledger, so a restart (crash, reboot, late start)
picks up exactly where the day stands - late premarket runs as catch-up, missed opens are
executed if the session is still young enough, and EOD always runs.
"""
from __future__ import annotations

import logging
import os
import time
import traceback
from datetime import datetime, time as dtime

from quantdesk.config import get_settings
from quantdesk.data.calendar import is_market_open, is_trading_day, now_ist
from quantdesk.store import db

log = logging.getLogger("quantdesk.autopilot")


_lock_handle = None


def _lock_path():
    return get_settings().path("data/autopilot.lock")


def acquire_lock() -> bool:
    """Hold an OS-level exclusive lock for the life of the process: one autopilot per machine."""
    global _lock_handle
    fh = open(_lock_path(), "a+")
    try:
        if os.name == "nt":
            import msvcrt

            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return False
    _lock_handle = fh
    return True


def autopilot_running() -> bool:
    """True if some process currently holds the autopilot lock."""
    fh = open(_lock_path(), "a+")
    try:
        if os.name == "nt":
            import msvcrt

            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        return False
    except OSError:
        return True
    finally:
        fh.close()


def _t(hhmm: str) -> dtime:
    h, m = hhmm.split(":")
    return dtime(int(h), int(m))


class Autopilot:
    def __init__(self, agent=None):
        from quantdesk.trader.agent import TradingAgent

        self.s = get_settings()
        self.agent = agent or TradingAgent()
        self.sch = self.s.agent.schedule
        self.failures: dict[str, int] = {}

    def _phase(self, name: str, fn, *args) -> None:
        day = self.agent.today()
        key = f"{day}:{name}"
        if self.failures.get(key, 0) >= 3:
            return
        try:
            fn(*args)
        except Exception as exc:
            self.failures[key] = self.failures.get(key, 0) + 1
            db.set_phase(day, self.agent.account, name, "failed", {"error": str(exc), "attempt": self.failures[key]})
            self.agent.log("phase_failed", f"{name} failed (attempt {self.failures[key]}/3): {exc}",
                           {"traceback": traceback.format_exc()[-3000:]}, "error")

    def step(self) -> None:
        a, acct = self.agent, self.agent.account
        now = now_ist()
        day, t = now.date().isoformat(), now.time()
        db.kv_set(f"heartbeat:{acct}", {"ts": db.now(), "pid": __import__("os").getpid()})
        if not is_trading_day(now.date()):
            return
        done = lambda p: db.phase_status(day, acct, p) in ("done", "skipped")

        if _t(self.sch.premarket) <= t < dtime(14, 45) and not done("premarket"):
            self._phase("premarket", a.premarket)

        if t >= dtime(9, 15) and not done("open"):
            status = a.confirm_trading_day()
            if status == "holiday":
                for p in ("open", "cancel", "eod"):
                    db.set_phase(day, acct, p, "skipped", {"reason": "market holiday"})
                return
            if status == "trading" and t >= _t(self.sch.open) and done("premarket"):
                if t < dtime(14, 45):
                    self._phase("open", a.open_session)  # under the kill switch the guard blocks buys, exits still run
                else:
                    db.set_phase(day, acct, "open", "skipped", {"reason": "too late in the session"})

        if is_market_open() or (t >= dtime(15, 30) and not done("eod")):
            try:
                a.tick()  # transient failures must never stop risk management for the day
            except Exception as exc:
                log.warning("tick failed: %s", exc)
                if now.minute % 10 == 0:
                    a.log("tick_failed", str(exc), level="warning")

        if t >= _t(self.sch.cancel_entries) and done("open") and not done("cancel"):
            self._phase("cancel", a.cancel_unfilled_entries)

        if t >= _t(self.sch.eod) and not done("eod") and db.kv_get(f"daystatus:{day}") != "holiday":
            self._phase("eod", a.end_of_day)

    def run(self) -> None:
        a = self.agent
        a.log("autopilot_start", f"Autopilot started (broker={a.broker.name}, mode={self.s.execution.mode})")
        try:
            while True:
                started = time.monotonic()
                try:
                    self.step()
                except Exception as exc:  # never die
                    log.exception("step failed: %s", exc)
                    a.log("autopilot_error", str(exc), {"traceback": traceback.format_exc()[-3000:]}, "error")
                interval = self.sch.tick_seconds if is_market_open() else 60
                time.sleep(max(5.0, interval - (time.monotonic() - started)))
        except KeyboardInterrupt:
            a.log("autopilot_stop", "Autopilot stopped by user")
