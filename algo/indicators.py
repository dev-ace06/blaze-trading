from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np
import pandas as pd


def session_vwap(frame: pd.DataFrame) -> pd.Series:
    typical = (frame["high"] + frame["low"] + frame["close"]) / 3.0
    volume = frame["volume"].fillna(0).astype(float)
    denominator = volume.cumsum().replace(0, np.nan)
    return (typical * volume).cumsum() / denominator


def ema(values: pd.Series, span: int = 9) -> pd.Series:
    return values.astype(float).ewm(span=span, adjust=False).mean()


def atr(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    previous_close = frame["close"].shift(1)
    true_range = pd.concat(
        [
            frame["high"] - frame["low"],
            (frame["high"] - previous_close).abs(),
            (frame["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.ewm(alpha=1 / period, adjust=False).mean()


def rvol(volume: pd.Series, expected: pd.Series, fallback: float) -> pd.Series:
    baseline = expected.astype(float).fillna(float(fallback)).replace(0, float(fallback))
    return volume.astype(float) / baseline


def opening_range(frame: pd.DataFrame) -> tuple[float, float, float]:
    if frame.empty:
        raise ValueError("opening range requires candles")
    local_index = frame.index
    if isinstance(local_index, pd.DatetimeIndex):
        opening = frame[(local_index.hour == 9) & (local_index.minute < 30)]
    else:
        opening = frame.iloc[:3]
    if opening.empty:
        raise ValueError("no 09:15-09:30 candles")
    return float(opening["high"].max()), float(opening["low"].min()), float(opening["volume"].sum())


@dataclass(frozen=True)
class Candle:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


class CandleBuilder:
    def __init__(self, minutes: int = 5) -> None:
        self.minutes = minutes
        self._bucket: datetime | None = None
        self._open = self._high = self._low = self._close = 0.0
        self._volume = 0.0

    def _floor(self, timestamp: datetime) -> datetime:
        minute = timestamp.minute - timestamp.minute % self.minutes
        return timestamp.replace(minute=minute, second=0, microsecond=0)

    def add_tick(self, timestamp: datetime, price: float, volume: float = 0) -> Candle | None:
        bucket = self._floor(timestamp)
        completed = None
        if self._bucket is not None and bucket > self._bucket:
            completed = Candle(
                self._bucket,
                self._open,
                self._high,
                self._low,
                self._close,
                self._volume,
            )
            self._bucket = None
        if self._bucket is None:
            self._bucket = bucket
            self._open = self._high = self._low = self._close = float(price)
            self._volume = float(volume)
        else:
            self._high = max(self._high, float(price))
            self._low = min(self._low, float(price))
            self._close = float(price)
            self._volume += float(volume)
        return completed

    def flush(self) -> Candle | None:
        if self._bucket is None:
            return None
        candle = Candle(self._bucket, self._open, self._high, self._low, self._close, self._volume)
        self._bucket = None
        return candle
