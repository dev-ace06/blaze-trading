from datetime import datetime
from zoneinfo import ZoneInfo

from algo.risk import RiskLimits, RiskState, daily_loss_breached, size_position


IST = ZoneInfo("Asia/Kolkata")


def test_position_size_applies_risk_and_notional_caps():
    limits = RiskLimits(
        risk_per_trade=0.0125,
        max_notional_fraction=0.40,
        leverage_cap=3.0,
        max_gross_exposure=3.0,
    )
    result = size_position(
        equity=100_000,
        entry=100,
        stop=99,
        current_gross=0,
        direction_multiplier=1.0,
        vix_multiplier=1.0,
        limits=limits,
    )
    assert result.qty == 1200
    assert result.risk_amount == 1250
    assert result.limited_by == "notional"


def test_position_size_uses_multipliers_and_remaining_gross_capacity():
    limits = RiskLimits()
    result = size_position(
        equity=100_000,
        entry=100,
        stop=99,
        current_gross=290_000,
        direction_multiplier=0.5,
        vix_multiplier=0.8,
        limits=limits,
    )
    assert result.qty == 100
    assert result.limited_by == "gross_exposure"


def test_daily_loss_guard_and_entry_limits():
    state = RiskState(day_start_equity=100_000, realized_pnl=-3_000, unrealized_pnl=-2_100)
    assert daily_loss_breached(state, loss_limit=0.05)
    state.halted = True
    allowed, reason = state.can_enter("RELIANCE", datetime(2026, 1, 5, 10, 0, tzinfo=IST))
    assert not allowed
    assert reason == "daily_loss_halt"


def test_limits_reject_outside_window_attempts_and_capacity():
    state = RiskState(day_start_equity=100_000)
    state.attempts["INFY"] = 2
    assert state.can_enter("INFY", datetime(2026, 1, 5, 10, 0, tzinfo=IST))[1] == "max_attempts"
    assert state.can_enter("TCS", datetime(2026, 1, 5, 9, 29, tzinfo=IST))[1] == "outside_entry_window"

