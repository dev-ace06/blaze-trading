from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .config import StrategyConfig

DEFAULT_PARAMS = StrategyConfig()


@dataclass(frozen=True)
class EntryContext:
    symbol: str
    now: datetime
    close: float
    high: float
    low: float
    or_high: float
    or_low: float
    vwap: float
    ema9: float
    atr_5m: float
    candle_rvol: float
    regime: str
    stock_news_score: float
    long_pause: bool
    limits_ok: bool
    limit_reason: str


@dataclass(frozen=True)
class Decision:
    action: str
    side: str | None
    size_multiplier: float
    reason: str


def evaluate_entry(context: EntryContext, params: StrategyConfig = DEFAULT_PARAMS) -> Decision:
    long_conditions = {
        "close>or_high": context.close > context.or_high,
        "close>vwap": context.close > context.vwap,
        "rvol_ok": context.candle_rvol >= params.candle_rvol_min,
        "regime_long_ok": context.regime != "BEARISH" or context.stock_news_score >= 0.4,
        "news_long_ok": context.stock_news_score > -0.3,
        "long_pause_clear": not context.long_pause,
        "not_extended": context.atr_5m > 0 and context.close - context.or_high <= params.max_extension_atr * context.atr_5m,
        "limits_ok": context.limits_ok,
    }
    short_conditions = {
        "close<or_low": context.close < context.or_low,
        "close<vwap": context.close < context.vwap,
        "rvol_ok": context.candle_rvol >= params.candle_rvol_min,
        "regime_short_ok": context.regime != "BULLISH" or context.stock_news_score <= -0.4,
        "news_short_ok": context.stock_news_score < 0.3,
        "not_extended": context.atr_5m > 0 and context.or_low - context.close <= params.max_extension_atr * context.atr_5m,
        "limits_ok": context.limits_ok,
    }
    values = (
        f"close={context.close:.3f},or_high={context.or_high:.3f},or_low={context.or_low:.3f},"
        f"vwap={context.vwap:.3f},atr={context.atr_5m:.3f},rvol={context.candle_rvol:.3f},"
        f"regime={context.regime},news={context.stock_news_score:.3f},limit_reason={context.limit_reason}"
    )
    if all(long_conditions.values()):
        multiplier = 0.5 if context.regime == "BEARISH" else 1.0
        checks = ",".join(f"{name}={value}" for name, value in long_conditions.items())
        return Decision("ENTER_LONG", "LONG", multiplier, f"{values},{checks}")
    if all(short_conditions.values()):
        multiplier = 0.5 if context.regime == "BULLISH" else 1.0
        checks = ",".join(f"{name}={value}" for name, value in short_conditions.items())
        return Decision("ENTER_SHORT", "SHORT", multiplier, f"{values},{checks}")
    checks = {**long_conditions, **short_conditions}
    return Decision("SKIP", None, 0.0, f"{values}," + ",".join(f"{name}={value}" for name, value in checks.items()))


def initial_stop(
    side: str,
    entry: float,
    or_other_side: float,
    atr_5m: float,
    min_stop_pct: float = 0.003,
    atr_mult: float = 1.0,
) -> float:
    minimum = entry * min_stop_pct
    if side == "LONG":
        candidate = max(or_other_side, entry - atr_mult * atr_5m)
        return min(candidate, entry - minimum)
    candidate = min(or_other_side, entry + atr_mult * atr_5m)
    return max(candidate, entry + minimum)


@dataclass(frozen=True)
class PositionView:
    symbol: str
    side: str
    entry: float
    initial_stop: float
    qty: int
    remaining_qty: int
    stop: float
    scaled_out: bool = False


@dataclass(frozen=True)
class ManagementDecision:
    action: str
    reason: str
    price: float | None = None
    qty: int = 0
    new_stop: float | None = None


def manage_position(
    position: PositionView,
    high: float,
    low: float,
    close: float,
    vwap: float,
    ema9: float,
    atr_5m: float,
    news_score: float = 0.0,
    news_severity: float = 0.0,
    params: StrategyConfig = DEFAULT_PARAMS,
    open_price: float | None = None,
) -> ManagementDecision:
    # A candle that opens through the stop fills at the open, not at the stop price.
    risk = abs(position.entry - position.initial_stop)
    be_r, partial_r, buffer = params.breakeven_r, params.partial_r, params.trail_atr_buffer
    if position.side == "LONG":
        if low <= position.stop:
            fill = min(position.stop, open_price) if open_price is not None else position.stop
            return ManagementDecision("EXIT", "stop_or_trail", fill, position.remaining_qty, position.stop)
        if news_score <= -0.5 and news_severity >= 0.7:
            return ManagementDecision("EXIT", "severe_negative_news", close, position.remaining_qty, position.stop)
        favorable = high - position.entry
        stop = position.stop
        if favorable >= be_r * risk:
            stop = max(stop, position.entry)
        if favorable >= partial_r * risk:
            stop = max(stop, min(vwap, ema9) - buffer * atr_5m)
            if not position.scaled_out and position.remaining_qty > 1:
                qty = max(1, int(position.remaining_qty * params.partial_fraction))
                return ManagementDecision("PARTIAL_EXIT", "two_r_scale", position.entry + partial_r * risk, qty, stop)
        if news_score <= -0.5:
            stop = max(stop, position.entry - 0.5 * risk)
        return ManagementDecision("HOLD", "managed_long", new_stop=stop)
    if high >= position.stop:
        fill = max(position.stop, open_price) if open_price is not None else position.stop
        return ManagementDecision("EXIT", "stop_or_trail", fill, position.remaining_qty, position.stop)
    if news_score >= 0.5 and news_severity >= 0.7:
        return ManagementDecision("EXIT", "severe_positive_news", close, position.remaining_qty, position.stop)
    favorable = position.entry - low
    stop = position.stop
    if favorable >= be_r * risk:
        stop = min(stop, position.entry)
    if favorable >= partial_r * risk:
        stop = min(stop, max(vwap, ema9) + buffer * atr_5m)
        if not position.scaled_out and position.remaining_qty > 1:
            qty = max(1, int(position.remaining_qty * params.partial_fraction))
            return ManagementDecision("PARTIAL_EXIT", "two_r_scale", position.entry - partial_r * risk, qty, stop)
    if news_score >= 0.5:
        stop = min(stop, position.entry + 0.5 * risk)
    return ManagementDecision("HOLD", "managed_short", new_stop=stop)
