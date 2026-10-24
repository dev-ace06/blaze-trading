import pytest

from backtest.metrics import calculate_metrics


def test_metrics_include_required_performance_fields():
    metrics = calculate_metrics(
        initial_equity=100_000,
        daily_equity=[100_000, 101_000, 100_000, 103_000],
        trades=[{"net_pnl": 1000, "r_multiple": 1}, {"net_pnl": -500, "r_multiple": -0.5}],
        daily_loss_hits=1,
    )
    assert metrics["total_return_pct"] == pytest.approx(3)
    assert metrics["win_rate_pct"] == 50
    assert metrics["profit_factor"] == 2
    assert metrics["average_r"] == 0.25
    assert metrics["max_drawdown_pct"] < 0
    assert metrics["loss_limit_days_pct"] == pytest.approx(100 / 3)

