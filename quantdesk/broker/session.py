"""Daily broker session tokens.

SEBI's retail-algo framework requires API sessions to be re-authenticated every trading day, so
tokens are stored with the date they were issued and ignored on any other day.
"""
from __future__ import annotations

import json
from datetime import date

from quantdesk.config import get_settings


def _path(broker: str):
    return get_settings().path(f"data/session/{broker}.json")


def save_token(broker: str, token: str, extra: dict | None = None) -> None:
    _path(broker).write_text(json.dumps({"date": date.today().isoformat(), "access_token": token, **(extra or {})}),
                             encoding="utf-8")


def load_token(broker: str) -> str | None:
    p = _path(broker)
    if not p.exists():
        return None
    data = json.loads(p.read_text(encoding="utf-8"))
    return data["access_token"] if data.get("date") == date.today().isoformat() else None
