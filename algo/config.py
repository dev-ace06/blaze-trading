from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import dotenv_values


@dataclass(frozen=True)
class ScheduleConfig:
    startup: str = "08:30"
    preopen_start: str = "09:00"
    market_open: str = "09:15"
    entry_start: str = "09:30"
    entry_end: str = "14:15"
    square_off: str = "14:50"
    market_close: str = "15:30"
    candle_minutes: int = 5
    news_poll_seconds: int = 90
    rerank_minutes: int = 15
    loop_interval_seconds: float = 0.5
    feed_poll_seconds: int = 45
    heartbeat_seconds: int = 60
    entry_max_lag_seconds: int = 180


@dataclass(frozen=True)
class RiskConfig:
    risk_per_trade: float = 0.0125
    daily_loss_limit: float = 0.05
    max_attempts_per_stock: int = 2
    max_trades_per_day: int = 8
    max_concurrent_positions: int = 3
    max_notional_fraction: float = 0.40
    leverage_cap: float = 3.0
    max_gross_exposure: float = 3.0
    min_stop_pct: float = 0.003


@dataclass(frozen=True)
class SelectionConfig:
    top_n: int = 5
    min_price: float = 100
    min_avg_daily_value_cr: float = 200
    min_or_width_pct: float = 0.0025
    max_or_atr_multiple: float = 2.5
    gap_weight: float = 0.35
    rvol_weight: float = 0.35
    or_width_weight: float = 0.15
    news_weight: float = 0.15


@dataclass(frozen=True)
class StrategyConfig:
    candle_rvol_min: float = 1.5
    max_extension_atr: float = 0.6
    breakeven_r: float = 1.0
    partial_r: float = 2.0
    partial_fraction: float = 0.5
    trail_atr_buffer: float = 0.1
    stop_atr_mult: float = 1.0
    use_nifty_trend: bool = True


@dataclass(frozen=True)
class SentimentConfig:
    bullish_threshold: float = 0.25
    bearish_threshold: float = -0.25
    premarket_weight: float = 0.7
    market_news_weight: float = 0.3
    intraday_day_bias_weight: float = 0.5
    intraday_nifty_weight: float = 0.5
    nifty_vwap_scale: float = 0.003
    components: dict[str, dict[str, float]] = field(default_factory=dict)


@dataclass(frozen=True)
class NewsConfig:
    max_age_hours: int = 18
    half_life_minutes: float = 90
    gemini_model: str = "gemini-3.8-flash"
    gemini_calls_per_minute: int = 10
    gemini_batch_size: int = 10
    request_timeout_seconds: int = 8
    negative_block: float = -0.5
    positive_block: float = 0.5
    entry_negative_limit: float = -0.3
    entry_positive_limit: float = 0.3
    ranking_boost_threshold: float = 0.4
    severe_exit_threshold: float = 0.7
    severe_market_score: float = -0.6
    severe_market_severity: float = 0.8
    long_pause_minutes: int = 15
    sources: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CostConfig:
    brokerage_per_order: float = 0
    stt_sell: float = 0.00025
    exchange_txn: float = 0.0000297
    sebi_per_crore: float = 10
    stamp_buy: float = 0.00003
    gst: float = 0.18
    slippage: float = 0.0003


@dataclass(frozen=True)
class BrokerConfig:
    environment: str = "prod"
    exchange_segment: str = "nse_cm"
    product: str = "MIS"
    order_type: str = "MKT"
    validity: str = "DAY"
    broker_side_stops: bool = False
    request_timeout_seconds: int = 10
    exit_retries: int = 3
    use_broker_equity: bool = False
    flatten_settle_seconds: float = 3.0
    stale_quote_seconds: float = 20.0


@dataclass(frozen=True)
class BacktestConfig:
    interval: str = "5m"
    batch_size: int = 15
    initial_equity: float = 1_000_000
    cache_dir: str = "data"
    output_dir: str = "reports/backtest"


@dataclass(frozen=True)
class AppConfig:
    timezone: str
    initial_equity: float
    schedule: ScheduleConfig
    risk: RiskConfig
    selection: SelectionConfig
    strategy: StrategyConfig
    sentiment: SentimentConfig
    news: NewsConfig
    costs: CostConfig
    broker: BrokerConfig
    backtest: BacktestConfig
    secrets: dict[str, str] = field(default_factory=dict, repr=False)


def _section(cls: type, data: dict[str, Any], name: str):
    return cls(**(data.get(name) or {}))


def load_config(path: str | Path = "config.yaml", env_file: str | Path | None = ".env") -> AppConfig:
    with Path(path).open(encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    secrets = {}
    if env_file is not None and Path(env_file).exists():
        secrets = {key: value for key, value in dotenv_values(env_file).items() if value}
    return AppConfig(
        timezone=raw.get("timezone", "Asia/Kolkata"),
        initial_equity=float(raw.get("initial_equity", 1_000_000)),
        schedule=_section(ScheduleConfig, raw, "schedule"),
        risk=_section(RiskConfig, raw, "risk"),
        selection=_section(SelectionConfig, raw, "selection"),
        strategy=_section(StrategyConfig, raw, "strategy"),
        sentiment=_section(SentimentConfig, raw, "sentiment"),
        news=_section(NewsConfig, raw, "news"),
        costs=_section(CostConfig, raw, "costs"),
        broker=_section(BrokerConfig, raw, "broker"),
        backtest=_section(BacktestConfig, raw, "backtest"),
        secrets=secrets,
    )
