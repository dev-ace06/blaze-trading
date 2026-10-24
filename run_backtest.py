from __future__ import annotations

import argparse
import json
from datetime import date

import numpy as np
import pandas as pd

from algo.config import load_config
from algo.sentiment.premarket import weighted_score
from backtest.data import build_daily_features, download_daily, download_intraday, download_macro_history, load_universe
from backtest.run import run_backtest


def available_mean(values: list[float]) -> float | None:
    usable = [float(value) for value in values if not pd.isna(value)]
    return sum(usable) / len(usable) if usable else None


def nifty_gaps(nifty: pd.DataFrame) -> dict[date, float]:
    """Nifty open vs previous session close: the historical stand-in for the GIFT Nifty gap."""
    if nifty.empty:
        return {}
    sessions = nifty.groupby(nifty.index.date).agg(open=("open", "first"), close=("close", "last"))
    previous = sessions["close"].shift(1)
    return {day: float(sessions.loc[day, "open"] / previous[day] - 1) for day in sessions.index if not pd.isna(previous[day])}


def historical_bias(macros: pd.DataFrame, trading_dates: list[date], gaps: dict[date, float] | None = None) -> dict[date, float]:
    if macros.empty and not gaps:
        return {}
    result = {}
    for trading_date in trading_dates:
        prior = macros[macros.index.date < trading_date].tail(1) if not macros.empty else macros
        row = prior.iloc[-1] if not prior.empty else pd.Series(dtype=float)
        components = {
            "gift_nifty_proxy": ((gaps or {}).get(trading_date), 0.35, 0.006, 1),
            "us": (available_mean([row.get("^GSPC", np.nan), row.get("^IXIC", np.nan), row.get("^DJI", np.nan)]), 0.30, 0.01, 1),
            "asia": (available_mean([row.get("^N225", np.nan), row.get("^HSI", np.nan), row.get("^KS11", np.nan)]), 0.30, 0.008, 1),
            "vix": (row.get("^INDIAVIX"), 0.20, 0.10, -1),
            "inr": (row.get("INR=X"), 0.10, 0.003, -1),
            "brent": (row.get("BZ=F"), 0.10, 0.02, -1),
        }
        cleaned = {
            name: (None if pd.isna(raw) else float(raw), weight, scale, sign)
            for name, (raw, weight, scale, sign) in components.items()
        }
        result[trading_date] = weighted_score(cleaned)[0]
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Backtest Sentinel ORB on recent NSE 5-minute data")
    parser.add_argument("--days", type=int, default=59)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--symbols", help="Comma-separated NSE symbols for a focused run")
    parser.add_argument("--output", default=None)
    args = parser.parse_args()
    if not 1 <= args.days <= 60:
        parser.error("--days must be between 1 and 60 for Yahoo 5-minute history")

    config = load_config()
    universe = load_universe()
    if args.symbols:
        selected = {item.strip().upper() for item in args.symbols.split(",") if item.strip()}
        universe = [row for row in universe if row["symbol"].upper() in selected]
    tickers = [row["yfinance_ticker"] for row in universe]
    print(f"Downloading/caching {args.days} days of 5-minute data for {len(tickers)} NSE symbols...")
    intraday = download_intraday(tickers, args.days, config.backtest.cache_dir, args.refresh, config.backtest.batch_size)
    if not intraday:
        raise SystemExit("No Yahoo Finance intraday data was available; retry later or inspect network access.")
    daily = download_daily(intraday, cache_dir=config.backtest.cache_dir, refresh=args.refresh)
    print(f"Daily history for {len(daily)}/{len(intraday)} symbols")
    features = build_daily_features(intraday, daily)
    macros = download_macro_history()
    nifty = download_intraday(["^NSEI"], args.days, config.backtest.cache_dir, args.refresh).get("^NSEI", pd.DataFrame())
    dates = sorted({day for frame in intraday.values() for day in frame.index.date})
    biases = historical_bias(macros, dates, nifty_gaps(nifty))
    result = run_backtest(
        intraday,
        features,
        config,
        output_dir=args.output or config.backtest.output_dir,
        historical_bias=biases,
        nifty=nifty,
    )
    keys = ["total_return_pct", "win_rate_pct", "profit_factor", "average_r", "max_drawdown_pct", "sharpe_daily", "trades", "trades_per_day"]
    print("\nSentinel ORB backtest metrics")
    for key in keys:
        print(f"  {key:24s} {result.metrics[key]}")
    print(f"Reports written to {args.output or config.backtest.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
