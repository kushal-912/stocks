"""Exchange clock helpers (IST by default)."""
from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from quantdesk.config import get_settings


def _tz() -> ZoneInfo:
    return ZoneInfo(get_settings().market.timezone)


def now_ist() -> datetime:
    return datetime.now(_tz())


def _parse_hhmm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def is_trading_day(d: date | None = None) -> bool:
    d = d or now_ist().date()
    if d.weekday() >= 5:
        return False
    return d.isoformat() not in set(get_settings().market.holidays)


def is_market_open(ts: datetime | None = None) -> bool:
    cfg = get_settings().market
    ts = ts.astimezone(_tz()) if ts else now_ist()
    if not is_trading_day(ts.date()):
        return False
    return _parse_hhmm(cfg.open_time) <= ts.time() <= _parse_hhmm(cfg.close_time)


def seconds_until(hhmm: str) -> float:
    """Seconds from now until the next occurrence of HH:MM (IST) on a trading day."""
    from datetime import timedelta

    now = now_ist()
    target_t = _parse_hhmm(hhmm)
    candidate = now.replace(hour=target_t.hour, minute=target_t.minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    while not is_trading_day(candidate.date()):
        candidate += timedelta(days=1)
    return (candidate - now).total_seconds()
