"""Audit trail, order-rate limiting and the kill switch.

Regulatory context (India, SEBI retail-algo framework, mandatory from 1 Apr 2026 - verify current
circulars with your broker):
* Self-built algos are allowed for your own (and immediate family's) accounts through a broker API.
* Below 10 orders/second per exchange no strategy registration is needed; orders still carry the
  broker's generic algo ID. Placements, modifications and cancellations all count.
* API access must come from a static IP whitelisted with the broker, with 2FA login every trading day.
* Brokers must keep audit trails; we keep our own as well (append-only JSONL).
"""
from __future__ import annotations

import json
import threading
import time
from collections import deque
from datetime import date, datetime
from pathlib import Path

from quantdesk.config import SEBI_UNREGISTERED_OPS_CEILING, get_settings


class AuditLog:
    def __init__(self, path: str | Path | None = None):
        s = get_settings()
        self.path = Path(path) if path else s.path(s.paths.audit_log)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def write(self, event: str, **payload) -> None:
        rec = {"ts": datetime.now().isoformat(timespec="milliseconds"), "event": event, **payload}
        with self._lock, self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, default=str) + "\n")

    def tail(self, n: int = 200) -> list[dict]:
        if not self.path.exists():
            return []
        lines = self.path.read_text(encoding="utf-8").splitlines()[-n:]
        return [json.loads(x) for x in lines if x.strip()]


class OrderRateLimiter:
    """Sliding one-second window + daily counter (persisted so restarts don't reset it)."""

    def __init__(self, per_second: int | None = None, per_day: int | None = None):
        s = get_settings()
        self.per_second = min(per_second or s.risk.max_orders_per_second, SEBI_UNREGISTERED_OPS_CEILING)
        self.per_day = per_day or s.risk.max_orders_per_day
        self._window: deque[float] = deque()
        self._lock = threading.Lock()
        self._counter_path = s.path("data/audit/order_counter.json")

    def _daily(self) -> dict:
        today = date.today().isoformat()
        if self._counter_path.exists():
            d = json.loads(self._counter_path.read_text(encoding="utf-8"))
            if d.get("date") == today:
                return d
        return {"date": today, "count": 0}

    def check(self) -> tuple[bool, str]:
        with self._lock:
            now = time.monotonic()
            while self._window and now - self._window[0] > 1.0:
                self._window.popleft()
            if len(self._window) >= self.per_second:
                return False, f"order rate limit ({self.per_second}/sec) reached"
            if self._daily()["count"] >= self.per_day:
                return False, f"daily order limit ({self.per_day}) reached"
            return True, ""

    def wait_for_slot(self, timeout: float = 5.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            ok, msg = self.check()
            if ok:
                return True
            if "daily" in msg:
                return False
            time.sleep(0.12)
        return False

    def record(self) -> None:
        with self._lock:
            self._window.append(time.monotonic())
            d = self._daily()
            d["count"] += 1
            self._counter_path.write_text(json.dumps(d), encoding="utf-8")

    def today_count(self) -> int:
        return self._daily()["count"]


def kill_switch_path() -> Path:
    s = get_settings()
    return s.path(s.paths.kill_switch_file)


def is_killed() -> bool:
    return kill_switch_path().exists()


def set_kill(on: bool, reason: str = "") -> None:
    p = kill_switch_path()
    if on:
        p.write_text(json.dumps({"ts": datetime.now().isoformat(timespec="seconds"), "reason": reason}), encoding="utf-8")
        AuditLog().write("kill_switch_on", reason=reason)
    elif p.exists():
        p.unlink()
        AuditLog().write("kill_switch_off")


def kill_reason() -> str:
    p = kill_switch_path()
    if not p.exists():
        return ""
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("reason", "")
    except json.JSONDecodeError:
        return "manual"
