from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, time
from zoneinfo import ZoneInfo


IST = ZoneInfo("Asia/Kolkata")


@dataclass(frozen=True)
class RiskLimits:
    risk_per_trade: float = 0.0125
    daily_loss_limit: float = 0.05
    max_attempts_per_stock: int = 2
    max_trades_per_day: int = 8
    max_concurrent_positions: int = 3
    max_notional_fraction: float = 0.40
    leverage_cap: float = 3.0
    max_gross_exposure: float = 3.0


@dataclass(frozen=True)
class SizeResult:
    qty: int
    risk_amount: float
    stop_distance: float
    limited_by: str


def size_position(
    equity: float,
    entry: float,
    stop: float,
    current_gross: float,
    direction_multiplier: float,
    vix_multiplier: float,
    limits: RiskLimits,
) -> SizeResult:
    stop_distance = abs(entry - stop)
    risk_amount = equity * limits.risk_per_trade * direction_multiplier * vix_multiplier
    if equity <= 0 or entry <= 0 or stop_distance <= 0:
        return SizeResult(0, max(0.0, risk_amount), stop_distance, "invalid_input")
    risk_qty = math.floor(risk_amount / stop_distance)
    notional_qty = math.floor((equity * limits.max_notional_fraction * limits.leverage_cap) / entry)
    remaining_gross = max(0.0, equity * limits.max_gross_exposure - current_gross)
    gross_qty = math.floor(remaining_gross / entry)
    qty = max(0, min(risk_qty, notional_qty, gross_qty))
    caps = {"risk": risk_qty, "notional": notional_qty, "gross_exposure": gross_qty}
    limited_by = min(caps, key=caps.get)
    return SizeResult(qty, risk_amount, stop_distance, limited_by)


@dataclass
class RiskState:
    day_start_equity: float
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    halted: bool = False
    attempts: dict[str, int] = field(default_factory=dict)
    trade_count: int = 0
    open_positions: int = 0
    limits: RiskLimits = field(default_factory=RiskLimits)

    def can_enter(self, symbol: str, now: datetime) -> tuple[bool, str]:
        local = now.astimezone(IST).time().replace(tzinfo=None)
        if self.halted:
            return False, "daily_loss_halt"
        if not time(9, 30) <= local <= time(14, 15):
            return False, "outside_entry_window"
        if self.attempts.get(symbol, 0) >= self.limits.max_attempts_per_stock:
            return False, "max_attempts"
        if self.trade_count >= self.limits.max_trades_per_day:
            return False, "max_trades"
        if self.open_positions >= self.limits.max_concurrent_positions:
            return False, "max_positions"
        return True, "ok"

    def record_attempt(self, symbol: str, filled: bool = False) -> None:
        self.attempts[symbol] = self.attempts.get(symbol, 0) + 1
        if filled:
            self.trade_count += 1
            self.open_positions += 1

    def refresh_loss_guard(self) -> bool:
        if daily_loss_breached(self, self.limits.daily_loss_limit):
            self.halted = True
        return self.halted


def daily_loss_breached(state: RiskState, loss_limit: float = 0.05) -> bool:
    return state.realized_pnl + state.unrealized_pnl <= -(state.day_start_equity * loss_limit)


def should_square_off(now: datetime) -> bool:
    return now.astimezone(IST).time().replace(tzinfo=None) >= time(14, 50)
