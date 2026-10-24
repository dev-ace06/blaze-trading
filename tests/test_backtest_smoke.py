from datetime import date

import pandas as pd

from algo.config import load_config
from backtest.run import run_backtest


def synthetic_day():
    idx = pd.date_range("2026-01-05 09:15", periods=68, freq="5min", tz="Asia/Kolkata")
    close = [100.0] * 3 + [101.0 + i * 0.05 for i in range(65)]
    return pd.DataFrame(
        {
            "open": close,
            "high": [value + 0.25 for value in close],
            "low": [value - 0.25 for value in close],
            "close": close,
            "volume": [100] * 3 + [300] * 65,
        },
        index=idx,
    )


def test_backtest_smoke_replays_shared_strategy_on_synthetic_data(tmp_path):
    config = load_config("config.yaml", env_file=None)
    result = run_backtest(
        data={"TEST": synthetic_day()},
        daily_features={"TEST": {date(2026, 1, 5): {"atr": 2, "avg_value_cr": 500, "gap_pct": 1}}},
        config=config,
        output_dir=tmp_path,
        write_plots=False,
    )
    assert result.metrics["trading_days"] == 1
    assert (tmp_path / "metrics.json").exists()
    assert (tmp_path / "trades.csv").exists()
    first_decision_count = len((tmp_path / "decisions.jsonl").read_text().splitlines())
    run_backtest(
        data={"TEST": synthetic_day()},
        daily_features={"TEST": {date(2026, 1, 5): {"atr": 2, "avg_value_cr": 500, "gap_pct": 1}}},
        config=config,
        output_dir=tmp_path,
        write_plots=False,
    )
    assert len((tmp_path / "decisions.jsonl").read_text().splitlines()) == first_decision_count

