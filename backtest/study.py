"""Parameter study: compare strategy variants on a train half and a held-out test half.

Usage: python -m backtest.study   (uses the cached data from run_backtest.py)
"""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pandas as pd

from algo.config import load_config
from backtest.data import build_daily_features, download_daily, download_intraday, download_macro_history, load_universe
from backtest.run import run_backtest
from run_backtest import historical_bias, nifty_gaps

WIDE = {"strategy": {"stop_atr_mult": 1.5}, "risk": {"min_stop_pct": 0.005}}


def wide(**sections: dict[str, float]) -> dict[str, dict[str, float]]:
    merged = {name: dict(values) for name, values in WIDE.items()}
    for name, values in sections.items():
        merged.setdefault(name, {}).update(values)
    return merged


ROUND2: dict[str, dict[str, dict[str, float]]] = {
    "wide": wide(),
    "wide_2.0atr": wide(strategy={"stop_atr_mult": 2.0}),
    "wide_minstop_0.7": wide(risk={"min_stop_pct": 0.007}),
    "wide_no_nifty": wide(strategy={"use_nifty_trend": False}),
    "wide_rvol_2.0": wide(strategy={"candle_rvol_min": 2.0}),
    "wide_top3": wide(selection={"top_n": 3}),
    "wide_be_1.5r": wide(strategy={"breakeven_r": 1.5}),
    "wide_partial_1.5r": wide(strategy={"partial_r": 1.5}),
    "wide_ext_1.0": wide(strategy={"max_extension_atr": 1.0}),
    "wide_1_attempt": wide(risk={"max_attempts_per_stock": 1}),
}

VARIANTS_ROUND1: dict[str, dict[str, dict[str, float]]] = {
    "spec_default": {},
    "wide_stop_1.5atr": {"strategy": {"stop_atr_mult": 1.5}, "risk": {"min_stop_pct": 0.005}},
    "late_breakeven_1.5r": {"strategy": {"breakeven_r": 1.5}},
    "no_breakeven": {"strategy": {"breakeven_r": 99}},
    "partial_3r": {"strategy": {"partial_r": 3.0}},
    "hold_to_close": {"strategy": {"breakeven_r": 99, "partial_r": 99}},
    "rvol_2.0": {"strategy": {"candle_rvol_min": 2.0}},
    "no_nifty_filter": {"strategy": {"use_nifty_trend": False}},
    "top3_stocks": {"selection": {"top_n": 3}},
    "combo_wide_late_be": {
        "strategy": {"stop_atr_mult": 1.5, "breakeven_r": 1.5, "partial_r": 3.0},
        "risk": {"min_stop_pct": 0.005},
    },
    "combo_wide_hold": {
        "strategy": {"stop_atr_mult": 1.5, "breakeven_r": 99, "partial_r": 99},
        "risk": {"min_stop_pct": 0.005},
    },
}

KEYS = ["total_return_pct", "profit_factor", "win_rate_pct", "max_drawdown_pct", "trades", "worst_day_pct"]


def apply(config, overrides):
    sections = {name: replace(getattr(config, name), **values) for name, values in overrides.items()}
    return replace(config, **sections)


def split(data: dict[str, pd.DataFrame], dates, part: str) -> dict[str, pd.DataFrame]:
    half = dates[len(dates) // 2]
    keep = (lambda index: index.date < half) if part == "train" else (lambda index: index.date >= half)
    return {symbol: frame[keep(frame.index)] for symbol, frame in data.items()}


def main() -> None:
    config = load_config()
    tickers = [row["yfinance_ticker"] for row in load_universe()]
    intraday = download_intraday(tickers, 59, config.backtest.cache_dir)
    daily = download_daily(intraday, cache_dir=config.backtest.cache_dir)
    features = build_daily_features(intraday, daily)
    nifty = download_intraday(["^NSEI"], 59, config.backtest.cache_dir).get("^NSEI", pd.DataFrame())
    dates = sorted({day for frame in intraday.values() for day in frame.index.date})
    biases = historical_bias(download_macro_history(), dates, nifty_gaps(nifty))
    rows = []
    import sys
    variants = VARIANTS_ROUND1 if "--round1" in sys.argv else ROUND2
    for name, overrides in variants.items():
        variant = apply(config, overrides)
        row = {"variant": name}
        for part in ("train", "test", "all"):
            data = intraday if part == "all" else split(intraday, dates, part)
            result = run_backtest(data, features, variant, f"reports/study/{name}/{part}", False, biases, nifty)
            for key in KEYS:
                row[f"{part}_{key}"] = round(float(result.metrics[key]), 2)
        rows.append(row)
        print(name, {k: v for k, v in row.items() if k.endswith(("total_return_pct", "profit_factor"))}, flush=True)
    table = pd.DataFrame(rows)
    Path("reports/study").mkdir(parents=True, exist_ok=True)
    table.to_csv("reports/study/results.csv", index=False)
    print(table[["variant", "train_total_return_pct", "test_total_return_pct", "all_total_return_pct",
                 "all_profit_factor", "all_max_drawdown_pct", "all_trades"]].to_string(index=False))


if __name__ == "__main__":
    main()
