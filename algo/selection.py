from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np


@dataclass(frozen=True)
class Candidate:
    symbol: str
    price: float
    avg_daily_value_cr: float
    daily_atr_pct: float
    gap_pct: float
    rvol_or: float
    or_width_pct: float
    news_score: float
    score: float = 0.0


def _zscore(values: list[float]) -> list[float]:
    array = np.asarray(values, dtype=float)
    std = float(array.std())
    if not std:
        return [0.0] * len(values)
    return ((array - float(array.mean())) / std).tolist()


def rank_candidates(
    candidates: list[Candidate],
    top_n: int = 5,
    held_symbols: set[str] | None = None,
) -> list[Candidate]:
    held_symbols = held_symbols or set()
    eligible = [
        row
        for row in candidates
        if row.price >= 100
        and row.avg_daily_value_cr >= 200
        and row.or_width_pct >= 0.25
        and row.daily_atr_pct > 0
        and row.or_width_pct <= 2.5 * row.daily_atr_pct
    ]
    if not eligible:
        return []
    features = [
        [abs(row.gap_pct) / row.daily_atr_pct for row in eligible],
        [row.rvol_or for row in eligible],
        [row.or_width_pct / row.daily_atr_pct for row in eligible],
        [abs(row.news_score) for row in eligible],
    ]
    standardized = [_zscore(values) for values in features]
    scored = [
        replace(
            row,
            score=0.35 * standardized[0][index]
            + 0.35 * standardized[1][index]
            + 0.15 * standardized[2][index]
            + 0.15 * standardized[3][index],
        )
        for index, row in enumerate(eligible)
    ]
    scored.sort(key=lambda row: row.score, reverse=True)
    selected = scored[:top_n]
    selected_symbols = {row.symbol for row in selected}
    for held in held_symbols - selected_symbols:
        row = next((item for item in scored if item.symbol == held), None)
        if row is not None:
            if len(selected) >= top_n:
                removable = next((idx for idx in range(len(selected) - 1, -1, -1) if selected[idx].symbol not in held_symbols), None)
                if removable is not None:
                    selected.pop(removable)
            selected.append(row)
    return sorted(selected, key=lambda row: row.score, reverse=True)[:top_n]
