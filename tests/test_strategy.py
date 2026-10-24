from datetime import datetime
from zoneinfo import ZoneInfo

from algo.strategy import EntryContext, PositionView, evaluate_entry, initial_stop, manage_position


IST = ZoneInfo("Asia/Kolkata")


def base_context(**changes):
    values = dict(
        symbol="RELIANCE",
        now=datetime(2026, 1, 5, 10, 0, tzinfo=IST),
        close=101.0,
        high=101.2,
        low=100.5,
        or_high=100.0,
        or_low=98.0,
        vwap=100.5,
        ema9=100.6,
        atr_5m=2.0,
        candle_rvol=1.6,
        regime="BULLISH",
        stock_news_score=0.0,
        long_pause=False,
        limits_ok=True,
        limit_reason="ok",
    )
    values.update(changes)
    return EntryContext(**values)


def test_long_signal_lists_all_condition_values():
    decision = evaluate_entry(base_context())
    assert decision.action == "ENTER_LONG"
    assert decision.size_multiplier == 1.0
    assert "close>or_high=True" in decision.reason
    assert "rvol=1.600" in decision.reason


def test_countertrend_needs_news_and_is_half_size():
    blocked = evaluate_entry(base_context(regime="BEARISH", stock_news_score=0.2))
    allowed = evaluate_entry(base_context(regime="BEARISH", stock_news_score=0.5))
    assert blocked.action == "SKIP"
    assert allowed.action == "ENTER_LONG"
    assert allowed.size_multiplier == 0.5


def test_short_is_exact_mirror_and_news_blocks_direction():
    short = evaluate_entry(
        base_context(close=97.0, high=97.5, low=96.8, vwap=98.0, regime="BEARISH")
    )
    blocked = evaluate_entry(base_context(stock_news_score=-0.4))
    assert short.action == "ENTER_SHORT"
    assert blocked.action == "SKIP"
    assert "news_long_ok=False" in blocked.reason


def test_initial_stop_enforces_minimum_distance():
    assert initial_stop("LONG", entry=100, or_other_side=99.9, atr_5m=0.1) == 99.7
    assert initial_stop("SHORT", entry=100, or_other_side=100.1, atr_5m=0.1) == 100.3


def test_management_hits_stop_conservatively_and_scales_at_two_r():
    position = PositionView("RELIANCE", "LONG", 100, 99, 10, 10, 99)
    stopped = manage_position(position, high=103, low=98.5, close=102, vwap=101, ema9=101.2, atr_5m=1)
    assert stopped.action == "EXIT"
    assert stopped.price == 99
    scaled = manage_position(position, high=102.1, low=99.5, close=102, vwap=101, ema9=101.2, atr_5m=1)
    assert scaled.action == "PARTIAL_EXIT"
    assert scaled.qty == 5
    assert scaled.new_stop == 100.9

