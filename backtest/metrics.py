from __future__ import annotations

import math
from typing import Any

import numpy as np


def calculate_metrics(
    initial_equity: float,
    daily_equity: list[float],
    trades: list[dict[str, Any]],
    daily_loss_hits: int = 0,
) -> dict[str, Any]:
    equity = np.asarray(daily_equity or [initial_equity], dtype=float)
    if equity[0] != initial_equity:
        equity = np.insert(equity, 0, initial_equity)
    returns = np.diff(equity) / equity[:-1] if len(equity) > 1 else np.asarray([], dtype=float)
    daily_pnl = np.diff(equity) if len(equity) > 1 else np.asarray([], dtype=float)
    net = [float(trade.get("net_pnl", 0)) for trade in trades]
    wins = [value for value in net if value > 0]
    losses = [value for value in net if value < 0]
    profit_factor = sum(wins) / abs(sum(losses)) if losses else (math.inf if wins else 0.0)
    peak = np.maximum.accumulate(equity)
    drawdowns = (equity - peak) / peak
    sharpe = 0.0
    if len(returns) > 1 and float(np.std(returns, ddof=1)) > 0:
        sharpe = float(np.mean(returns) / np.std(returns, ddof=1) * math.sqrt(252))
    trading_days = len(returns)
    return {
        "initial_equity": float(initial_equity),
        "final_equity": float(equity[-1]),
        "total_return_pct": float((equity[-1] / initial_equity - 1) * 100),
        "trading_days": trading_days,
        "daily_returns_pct": [float(value * 100) for value in returns],
        "win_rate_pct": float(len(wins) / len(net) * 100) if net else 0.0,
        "profit_factor": float(profit_factor),
        "average_r": float(np.mean([trade.get("r_multiple", 0) for trade in trades])) if trades else 0.0,
        "max_drawdown_pct": float(drawdowns.min() * 100),
        "sharpe_daily": sharpe,
        "best_day_pct": float(returns.max() * 100) if len(returns) else 0.0,
        "worst_day_pct": float(returns.min() * 100) if len(returns) else 0.0,
        "trades": len(trades),
        "trades_per_day": float(len(trades) / trading_days) if trading_days else 0.0,
        "loss_limit_days_pct": float(daily_loss_hits / trading_days * 100) if trading_days else 0.0,
        "gross_profit": float(sum(wins)),
        "gross_loss": float(sum(losses)),
        "net_pnl": float(sum(net)),
    }
