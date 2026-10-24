from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from algo.indicators import CandleBuilder, atr, ema, opening_range, rvol, session_vwap


IST = ZoneInfo("Asia/Kolkata")


def test_session_vwap_and_ema_use_price_volume_and_recursive_weighting():
    frame = pd.DataFrame(
        {
            "high": [10.0, 20.0],
            "low": [10.0, 20.0],
            "close": [10.0, 20.0],
            "volume": [1.0, 3.0],
        }
    )
    assert session_vwap(frame).tolist() == [10.0, 17.5]
    assert ema(frame["close"], span=3).tolist() == [10.0, 15.0]


def test_atr_includes_previous_close_gap():
    frame = pd.DataFrame(
        {"high": [10.0, 13.0], "low": [8.0, 11.0], "close": [9.0, 12.0]}
    )
    result = atr(frame, period=2)
    assert result.iloc[0] == 2.0
    assert result.iloc[1] == 3.0


def test_opening_range_and_rvol_have_fallbacks():
    idx = pd.date_range("2026-01-05 09:15", periods=4, freq="5min", tz=IST)
    frame = pd.DataFrame(
        {"high": [101, 103, 102, 105], "low": [99, 100, 98, 101], "volume": [10, 20, 30, 60]},
        index=idx,
    )
    assert opening_range(frame) == (103.0, 98.0, 60.0)
    values = rvol(pd.Series([30.0, 60.0]), pd.Series([20.0, np.nan]), fallback=30.0)
    assert values.tolist() == [1.5, 2.0]


def test_candle_builder_emits_completed_five_minute_ohlcv():
    builder = CandleBuilder(minutes=5)
    assert builder.add_tick(datetime(2026, 1, 5, 9, 15, 1, tzinfo=IST), 100, 5) is None
    assert builder.add_tick(datetime(2026, 1, 5, 9, 19, 59, tzinfo=IST), 102, 7) is None
    candle = builder.add_tick(datetime(2026, 1, 5, 9, 20, 0, tzinfo=IST), 101, 3)
    assert candle is not None
    assert (candle.open, candle.high, candle.low, candle.close, candle.volume) == (100, 102, 100, 102, 12)

