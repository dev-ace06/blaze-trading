from __future__ import annotations

from pathlib import Path
from typing import Any

import os

_matplotlib_cache = Path(".scratch/matplotlib").resolve()
_matplotlib_cache.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("MPLCONFIGDIR", str(_matplotlib_cache))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from algo.indicators import opening_range, session_vwap


def _save(fig, path: Path) -> None:
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)


def generate_plots(
    output_dir: str | Path,
    daily_equity: list[float],
    trades: list[dict[str, Any]],
    examples: list[tuple[dict[str, Any], pd.DataFrame]] | None = None,
) -> None:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    equity = np.asarray(daily_equity, dtype=float)

    fig, axis = plt.subplots(figsize=(9, 4))
    axis.plot(range(len(equity)), equity, color="#1261a0", linewidth=2)
    axis.set(title="Sentinel ORB Equity Curve", xlabel="Trading day", ylabel="Equity (₹)")
    axis.grid(alpha=0.25)
    _save(fig, output / "equity_curve.png")

    pnl = np.diff(equity) if len(equity) > 1 else np.asarray([])
    fig, axis = plt.subplots(figsize=(9, 4))
    axis.bar(range(len(pnl)), pnl, color=["#16865b" if value >= 0 else "#bf3f3f" for value in pnl])
    axis.axhline(0, color="black", linewidth=0.8)
    axis.set(title="Daily P&L", xlabel="Trading day", ylabel="P&L (₹)")
    _save(fig, output / "daily_pnl.png")

    fig, axis = plt.subplots(figsize=(8, 4))
    multiples = [trade.get("r_multiple", 0) for trade in trades]
    if multiples:
        axis.hist(multiples, bins=min(20, max(5, len(multiples))), color="#6956a5", edgecolor="white")
    else:
        axis.text(0.5, 0.5, "No completed trades", ha="center", va="center", transform=axis.transAxes)
    axis.set(title="R-Multiple Distribution", xlabel="R", ylabel="Trades")
    _save(fig, output / "r_multiple_histogram.png")

    examples = examples or []
    for index in range(3):
        fig, axis = plt.subplots(figsize=(10, 4))
        if examples:
            trade, frame = examples[min(index, len(examples) - 1)]
            view = frame.copy()
            x = np.arange(len(view))
            colors = np.where(view["close"].to_numpy() >= view["open"].to_numpy(), "#16865b", "#bf3f3f")
            axis.vlines(x, view["low"], view["high"], color=colors, linewidth=0.8)
            axis.vlines(x, view["open"], view["close"], color=colors, linewidth=3)
            try:
                or_high, or_low, _ = opening_range(view)
                axis.axhline(or_high, color="#e39b19", linestyle="--", label="OR high")
                axis.axhline(or_low, color="#e39b19", linestyle=":", label="OR low")
            except ValueError:
                pass
            axis.plot(x, session_vwap(view), color="#2d5fd0", linewidth=1.2, label="VWAP")
            axis.axhline(trade["entry_price"], color="#5d3d9b", label="Entry")
            axis.axhline(trade["exit_price"], color="#222", linestyle="--", label="Exit")
            axis.set_title(f"Example trade {index + 1}: {trade['symbol']} {trade['side']}")
            axis.legend(loc="best", ncol=5, fontsize=8)
        else:
            axis.text(0.5, 0.5, "No trade available for this sample", ha="center", va="center", transform=axis.transAxes)
            axis.set_title(f"Example trade {index + 1}")
        axis.set_xlabel("5-minute candle")
        axis.set_ylabel("Price (₹)")
        _save(fig, output / f"example_trade_{index + 1}.png")
