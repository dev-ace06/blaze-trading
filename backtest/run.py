from __future__ import annotations

import csv
import json
import math
from dataclasses import asdict, dataclass
from datetime import date, time
from pathlib import Path
from typing import Any

import pandas as pd

from algo.config import AppConfig
from algo.indicators import atr, ema, opening_range, session_vwap
from algo.portfolio import CostModel, Portfolio
from algo.risk import RiskLimits, RiskState, size_position
from algo.selection import Candidate, rank_candidates
from algo.sentiment.premarket import blend_bias, regime, vix_multiplier
from algo.strategy import EntryContext, PositionView, evaluate_entry, initial_stop, manage_position

from .metrics import calculate_metrics
from .plots import generate_plots


@dataclass(frozen=True)
class BacktestResult:
    metrics: dict[str, Any]
    trades: list[dict[str, Any]]
    daily_equity: list[float]


def _with_indicators(frame: pd.DataFrame) -> pd.DataFrame:
    output = frame.copy().sort_index()
    output["ema9"] = ema(output["close"], 9)
    output["atr14"] = atr(output, 14)
    output["session"] = output.index.date
    output["vwap"] = float("nan")
    for _, indexes in output.groupby("session").groups.items():
        output.loc[indexes, "vwap"] = session_vwap(output.loc[indexes]).to_numpy()
    slots = pd.Series(output.index.strftime("%H:%M"), index=output.index)
    expected = output["volume"].groupby(slots).transform(lambda values: values.shift().rolling(10, min_periods=1).mean())
    fallback = max(1.0, float(output["volume"].median()))
    output["rvol"] = output["volume"] / expected.fillna(fallback).replace(0, fallback)
    return output


def _risk_limits(config: AppConfig) -> RiskLimits:
    return RiskLimits(
        risk_per_trade=config.risk.risk_per_trade,
        daily_loss_limit=config.risk.daily_loss_limit,
        max_attempts_per_stock=config.risk.max_attempts_per_stock,
        max_trades_per_day=config.risk.max_trades_per_day,
        max_concurrent_positions=config.risk.max_concurrent_positions,
        max_notional_fraction=config.risk.max_notional_fraction,
        leverage_cap=config.risk.leverage_cap,
        max_gross_exposure=config.risk.max_gross_exposure,
    )


def _cost_model(config: AppConfig) -> CostModel:
    return CostModel(**asdict(config.costs))


def _write_rows(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def run_backtest(
    data: dict[str, pd.DataFrame],
    daily_features: dict[str, dict[date, dict[str, float]]],
    config: AppConfig,
    output_dir: str | Path = "reports/backtest",
    write_plots: bool = True,
    historical_bias: dict[date, float] | None = None,
    nifty: pd.DataFrame | None = None,
) -> BacktestResult:
    if not data:
        raise ValueError("backtest requires at least one symbol with 5-minute data")
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    prepared = {symbol: _with_indicators(frame) for symbol, frame in data.items() if not frame.empty}
    trading_dates = sorted({day for frame in prepared.values() for day in frame.index.date})
    initial_equity = config.backtest.initial_equity
    portfolio = Portfolio(initial_equity, _cost_model(config))
    equity_curve = [initial_equity]
    loss_hits = 0
    decision_path = output / "decisions.jsonl"
    decision_path.write_text("", encoding="utf-8")
    examples: list[tuple[dict[str, Any], pd.DataFrame]] = []
    params = config.strategy
    # Bars are stamped with their start time; a bar's close is 5 minutes later.
    last_entry_bar = time(14, 10)
    square_off_bar = time(14, 50)
    nifty_avg = pd.Series(dtype=float)
    if nifty is not None and not nifty.empty:
        # Index has no volume, so its "VWAP" is the session time-weighted average price.
        nifty_avg = nifty["close"].groupby(nifty.index.date).transform(lambda values: values.expanding().mean())

    for trading_date in trading_dates:
        day_start_equity = portfolio.equity
        risk_state = RiskState(day_start_equity, limits=_risk_limits(config))
        day_frames = {symbol: frame[frame.index.date == trading_date] for symbol, frame in prepared.items()}
        day_frames = {symbol: frame for symbol, frame in day_frames.items() if len(frame.between_time("09:15", "09:29")) >= 3}
        candidates = []
        ranges: dict[str, tuple[float, float]] = {}
        for symbol, frame in day_frames.items():
            try:
                or_high, or_low, or_volume = opening_range(frame)
            except ValueError:
                continue
            ranges[symbol] = (or_high, or_low)
            feature = daily_features.get(symbol, {}).get(trading_date, {})
            price = float(frame.iloc[2]["close"])
            daily_atr = float(feature.get("atr", price * 0.02))
            daily_atr_pct = daily_atr / price * 100 if price else 0
            avg_or_volume = float(feature.get("avg_first_15_volume", 0))
            if avg_or_volume <= 0:
                avg_or_volume = float(feature.get("avg_daily_volume", or_volume / 0.12)) * 0.12
            candidates.append(
                Candidate(
                    symbol=symbol,
                    price=price,
                    avg_daily_value_cr=float(feature.get("avg_value_cr", 0)),
                    daily_atr_pct=daily_atr_pct,
                    gap_pct=float(feature.get("gap_pct", 0)),
                    rvol_or=or_volume / avg_or_volume if avg_or_volume else 0,
                    or_width_pct=(or_high - or_low) / price * 100 if price else 0,
                    news_score=0.0,
                )
            )
        watchlist = [row.symbol for row in rank_candidates(candidates, config.selection.top_n)]
        timestamps = sorted({timestamp for symbol in watchlist for timestamp in day_frames[symbol].index if timestamp.time() >= time(9, 30)})
        prices: dict[str, float] = {}
        day_halted = False
        bias = (historical_bias or {}).get(trading_date, 0.0)
        day_regime = regime(bias)
        vix_level = None
        def current_regime(timestamp: pd.Timestamp) -> str:
            if not params.use_nifty_trend or timestamp not in nifty_avg.index:
                return day_regime
            return regime(blend_bias(bias, 0.0, float(nifty.loc[timestamp, "close"]), float(nifty_avg.loc[timestamp])))

        for timestamp in timestamps:
            for symbol, position in list(portfolio.positions.items()):
                if symbol not in day_frames or timestamp not in day_frames[symbol].index:
                    continue
                row = day_frames[symbol].loc[timestamp]
                prices[symbol] = float(row["close"])
                view = PositionView(
                    symbol,
                    position.side,
                    position.entry_price,
                    position.initial_stop,
                    position.initial_qty,
                    position.qty,
                    position.stop,
                    position.scaled_out,
                )
                management = manage_position(
                    view,
                    float(row["high"]),
                    float(row["low"]),
                    float(row["close"]),
                    float(row["vwap"]),
                    float(row["ema9"]),
                    float(row["atr14"]),
                    params=params,
                    open_price=float(row["open"]),
                )
                if timestamp.time() >= square_off_bar:
                    management = management.__class__("EXIT", "forced_square_off", float(row["open"]), position.qty, position.stop)
                if management.new_stop is not None:
                    position.stop = management.new_stop
                if management.action in {"EXIT", "PARTIAL_EXIT"}:
                    trade = portfolio.close_position(symbol, management.price or float(row["close"]), management.reason, management.qty, timestamp)
                    trade_dict = asdict(trade)
                    portfolio.trades[-1] = trade
                    if len(examples) < 3:
                        examples.append((trade_dict, day_frames[symbol]))
                    risk_state.realized_pnl = portfolio.realized_pnl - (day_start_equity - initial_equity)
                    risk_state.open_positions = len(portfolio.positions)

            risk_state.unrealized_pnl = portfolio.unrealized_pnl(prices)
            if risk_state.refresh_loss_guard() and not day_halted:
                day_halted = True
                loss_hits += 1
                for symbol, position in list(portfolio.positions.items()):
                    exit_price = prices.get(symbol, position.entry_price)
                    portfolio.close_position(symbol, exit_price, "daily_loss_halt", at=timestamp)

            if timestamp.time() > last_entry_bar or day_halted:
                continue
            bar_regime = current_regime(timestamp)
            for symbol in watchlist:
                if symbol in portfolio.positions or timestamp not in day_frames[symbol].index:
                    continue
                row = day_frames[symbol].loc[timestamp]
                allowed, limit_reason = risk_state.can_enter(symbol, timestamp)
                or_high, or_low = ranges[symbol]
                context = EntryContext(
                    symbol,
                    timestamp.to_pydatetime(),
                    float(row["close"]),
                    float(row["high"]),
                    float(row["low"]),
                    or_high,
                    or_low,
                    float(row["vwap"]),
                    float(row["ema9"]),
                    float(row["atr14"]),
                    float(row["rvol"]),
                    bar_regime,
                    0.0,
                    False,
                    allowed,
                    limit_reason,
                )
                decision = evaluate_entry(context, params)
                with decision_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps({"timestamp": timestamp.isoformat(), "symbol": symbol, **asdict(decision)}) + "\n")
                if not decision.action.startswith("ENTER"):
                    continue
                side = decision.side or "LONG"
                stop = initial_stop(
                    side, context.close, or_low if side == "LONG" else or_high, context.atr_5m, config.risk.min_stop_pct, params.stop_atr_mult
                )
                sizing = size_position(
                    day_start_equity,
                    context.close,
                    stop,
                    portfolio.gross_exposure(prices),
                    decision.size_multiplier,
                    vix_multiplier(vix_level),
                    risk_state.limits,
                )
                risk_state.record_attempt(symbol, filled=sizing.qty >= 1)
                if sizing.qty < 1:
                    continue
                portfolio.open_position(symbol, side, sizing.qty, context.close, stop, decision.reason, timestamp)
                prices[symbol] = context.close

        if portfolio.positions:
            for symbol in list(portfolio.positions):
                frame = day_frames.get(symbol)
                if frame is not None and not frame.empty:
                    portfolio.close_position(symbol, float(frame.iloc[-1]["close"]), "end_of_day", at=frame.index[-1])
        equity_curve.append(portfolio.equity)

    trades = [asdict(trade) for trade in portfolio.trades]
    metrics = calculate_metrics(initial_equity, equity_curve, trades, loss_hits)
    metrics["news_replayed"] = False
    metrics["historical_sentiment_proxy"] = "Nifty gap plus available prior-session macro returns; no historical news"
    metrics["symbols_with_data"] = len(prepared)
    (output / "metrics.json").write_text(json.dumps(metrics, indent=2, default=str), encoding="utf-8")
    trade_columns = list(trades[0]) if trades else ["symbol", "side", "qty", "entry_price", "exit_price", "gross_pnl", "costs", "net_pnl", "r_multiple", "reason", "opened_at", "closed_at"]
    _write_rows(output / "trades.csv", trades, trade_columns)
    _write_rows(
        output / "daily_equity.csv",
        [{"index": index, "equity": value} for index, value in enumerate(equity_curve)],
        ["index", "equity"],
    )
    if write_plots:
        generate_plots(output, equity_curve, trades, examples)
    return BacktestResult(metrics, trades, equity_curve)
