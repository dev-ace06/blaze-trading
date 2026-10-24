from datetime import datetime
from zoneinfo import ZoneInfo

from algo.clock import MarketClock
from algo.config import load_config


IST = ZoneInfo("Asia/Kolkata")


def test_default_config_contains_non_negotiable_limits():
    config = load_config("config.yaml", env_file=None)
    assert config.risk.risk_per_trade == 0.0125
    assert config.risk.daily_loss_limit == 0.05
    assert config.risk.max_trades_per_day == 8
    assert config.schedule.square_off == "14:50"
    assert config.news.gemini_model == "gemini-3.8-flash"


def test_market_clock_enforces_entry_and_squareoff_boundaries():
    at = lambda h, m: MarketClock(lambda: datetime(2026, 1, 5, h, m, tzinfo=IST))
    assert not at(9, 29).in_entry_window()
    assert at(9, 30).in_entry_window()
    assert at(14, 15).in_entry_window()
    assert not at(14, 16).in_entry_window()
    assert at(14, 50).should_square_off()

