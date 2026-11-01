"""Settings loading: config/settings.yaml, overridden by config/settings.local.yaml and .env."""
from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"

# SEBI retail-algo framework: strategies at or above 10 orders/sec per exchange must be
# registered with the exchange through the broker. We never allow configuration above this.
SEBI_UNREGISTERED_OPS_CEILING = 9


class MarketCfg(BaseModel):
    default_exchange: Literal["NSE", "BSE", "US"] = "NSE"
    timezone: str = "Asia/Kolkata"
    open_time: str = "09:15"
    close_time: str = "15:30"
    benchmark: str = "^NSEI"
    holidays: list[str] = Field(default_factory=list)


class DataCfg(BaseModel):
    cache_dir: str = "data/cache"
    cache_ttl_hours: float = 6
    history_years: int = 5


class SignalsCfg(BaseModel):
    weights: dict[str, float] = Field(default_factory=lambda: {
        "trend": 0.25, "momentum": 0.20, "mean_reversion": 0.15,
        "forecast": 0.15, "ml": 0.15, "sentiment": 0.10,
    })
    buy_threshold: float = 0.25
    sell_threshold: float = -0.25
    forecast_horizon_days: int = 5
    atr_stop_multiple: float = 2.0
    reward_risk: float = 2.0


class RiskCfg(BaseModel):
    capital: float = 100_000
    risk_per_trade_pct: float = 1.0
    max_position_pct: float = 20.0
    max_gross_exposure_pct: float = 100.0
    max_open_positions: int = 8
    max_daily_loss_pct: float = 3.0
    max_orders_per_day: int = 40
    max_orders_per_second: int = 5
    fat_finger_pct: float = 5.0
    allow_short: bool = False
    kelly_fraction: float = 0.25
    vol_target_annual: float = 0.20

    @field_validator("max_orders_per_second")
    @classmethod
    def _cap_ops(cls, v: int) -> int:
        return max(1, min(v, SEBI_UNREGISTERED_OPS_CEILING))


class ExecutionCfg(BaseModel):
    mode: Literal["paper", "live"] = "paper"
    broker: Literal["paper", "groww", "zerodha"] = "paper"
    product: Literal["CNC", "MIS"] = "CNC"
    require_manual_confirmation: bool = True
    algo_tag: str = "QDPERSONAL"
    slippage_bps: float = 5

    @field_validator("algo_tag")
    @classmethod
    def _tag(cls, v: str) -> str:
        v = "".join(ch for ch in v if ch.isalnum())[:20]
        return v or "QDPERSONAL"


class CostsCfg(BaseModel):
    profile: Literal["groww", "zerodha", "custom"] = "groww"
    custom: dict[str, float] = Field(default_factory=lambda: {
        "brokerage_pct": 0.0, "brokerage_min": 0.0, "brokerage_max": 20.0,
    })


class NewsCfg(BaseModel):
    max_headlines_per_symbol: int = 12
    lookback_days: int = 7
    half_life_hours: float = 36


class LLMCfg(BaseModel):
    enabled: bool = True
    model: str = "claude-opus-5-5"
    effort: Literal["low", "medium", "high", "xhigh", "max"] = "low"
    brief_effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    cache_file: str = "data/cache/llm_sentiment.json"


class UniverseCfg(BaseModel):
    # top_liquid: the top_n most-traded stocks *as of each day* (point-in-time, no look-ahead).
    # index_members uses today's NIFTY Total Market list - fine live, but biased in a backtest.
    source: Literal["top_liquid", "nse_all", "index_members", "watchlist"] = "top_liquid"
    top_n: int = 750
    min_price: float = 20.0
    min_traded_value_cr: float = 2.0      # 20-day median traded value, Rs crore
    min_history_days: int = 260
    refresh_days: float = 7
    history_years: int = 4


class ScreenerCfg(BaseModel):
    weights: dict[str, float] = Field(default_factory=lambda: {
        "mom_12_1": 0.25, "mom_6_1": 0.20, "risk_adj_mom": 0.20, "high_52w": 0.15, "low_vol": 0.10,
        "volume_trend": 0.05, "short_reversal": 0.05,
    })
    max_atr_pct: float = 6.0
    max_extension_pct: float = 25.0       # close vs SMA50
    max_rsi: float = 80.0


class ScheduleCfg(BaseModel):
    premarket: str = "08:15"
    open: str = "09:20"
    cancel_entries: str = "15:10"
    eod: str = "15:45"
    tick_seconds: int = 60


class AgentCfg(BaseModel):
    initial_capital: float = 50_000
    max_positions: int = 6
    min_position_value: float = 5_000
    cash_buffer_pct: float = 2.0
    max_per_sector: int = 2
    shortlist_size: int = 25
    screen_weight: float = 0.4            # final = screen_weight*screen + (1-screen_weight)*deep
    min_final_score: float = 0.30
    exit_score: float = 0.0
    swap_margin: float = 0.25
    max_swaps_per_day: int = 1
    min_hold_days: int = 5
    max_hold_days: int = 90
    stop_atr: float = 2.5
    trail_atr: float = 3.0
    max_gap_up_pct: float = 3.0
    entry_limit_buffer_pct: float = 0.3
    regime_filter: bool = True
    risk_off_max_exposure_pct: float = 40.0
    llm_review: bool = True
    schedule: ScheduleCfg = Field(default_factory=ScheduleCfg)


class PathsCfg(BaseModel):
    audit_log: str = "data/audit/audit.jsonl"
    paper_account: str = "data/paper/account.json"
    kill_switch_file: str = "data/KILL"


class Settings(BaseModel):
    market: MarketCfg = Field(default_factory=MarketCfg)
    watchlist: list[str] = Field(default_factory=list)
    data: DataCfg = Field(default_factory=DataCfg)
    signals: SignalsCfg = Field(default_factory=SignalsCfg)
    risk: RiskCfg = Field(default_factory=RiskCfg)
    execution: ExecutionCfg = Field(default_factory=ExecutionCfg)
    costs: CostsCfg = Field(default_factory=CostsCfg)
    news: NewsCfg = Field(default_factory=NewsCfg)
    llm: LLMCfg = Field(default_factory=LLMCfg)
    universe: UniverseCfg = Field(default_factory=UniverseCfg)
    screener: ScreenerCfg = Field(default_factory=ScreenerCfg)
    agent: AgentCfg = Field(default_factory=AgentCfg)
    paths: PathsCfg = Field(default_factory=PathsCfg)

    def path(self, rel: str) -> Path:
        """Resolve a configured relative path against the project root and ensure its parent exists."""
        p = Path(rel)
        if not p.is_absolute():
            p = ROOT / p
        p.parent.mkdir(parents=True, exist_ok=True)
        return p


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    load_dotenv(ROOT / ".env")
    raw = _deep_merge(_read_yaml(CONFIG_DIR / "settings.yaml"), _read_yaml(CONFIG_DIR / "settings.local.yaml"))
    return Settings.model_validate(raw)


def reload_settings() -> Settings:
    get_settings.cache_clear()
    return get_settings()


def env(name: str, default: str = "") -> str:
    load_dotenv(ROOT / ".env")
    return os.environ.get(name, default).strip()


def setup_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%H:%M:%S")
    for noisy in ("yfinance", "urllib3", "httpx", "httpx2", "peewee"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
