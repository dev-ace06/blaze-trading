from __future__ import annotations

import csv
import time
from datetime import date
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import yfinance as yf


IST = ZoneInfo("Asia/Kolkata")
REQUIRED_COLUMNS = ["open", "high", "low", "close", "volume"]


def load_universe(path: str | Path = "universe.csv") -> list[dict[str, str]]:
    with Path(path).open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _normalise(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    output = frame.copy()
    output.columns = [str(column).lower().replace(" ", "_") for column in output.columns]
    if "adj_close" in output and "close" not in output:
        output["close"] = output["adj_close"]
    missing = [column for column in REQUIRED_COLUMNS if column not in output]
    if missing:
        return pd.DataFrame()
    output = output[REQUIRED_COLUMNS].apply(pd.to_numeric, errors="coerce")
    output = output.dropna(subset=["open", "high", "low", "close"]).sort_index()
    output["volume"] = output["volume"].fillna(0)
    if not isinstance(output.index, pd.DatetimeIndex):
        output.index = pd.to_datetime(output.index)
    if output.index.tz is None:
        output.index = output.index.tz_localize(IST)
    else:
        output.index = output.index.tz_convert(IST)
    return output[~output.index.duplicated(keep="last")]


def _extract(downloaded: pd.DataFrame, ticker: str, single: bool) -> pd.DataFrame:
    if downloaded.empty:
        return pd.DataFrame()
    if not isinstance(downloaded.columns, pd.MultiIndex):
        return _normalise(downloaded)
    level0 = set(map(str, downloaded.columns.get_level_values(0)))
    try:
        if ticker in level0:
            return _normalise(downloaded[ticker])
        return _normalise(downloaded.xs(ticker, axis=1, level=1))
    except (KeyError, TypeError):
        return pd.DataFrame()


def _cache_file(cache_dir: Path, ticker: str, interval: str, days: int) -> Path:
    safe = ticker.replace("^", "IDX_").replace("=", "_")
    return cache_dir / f"{safe}_{interval}_{days}d.csv"


def download_intraday(
    tickers: Iterable[str],
    days: int = 59,
    cache_dir: str | Path = "data",
    refresh: bool = False,
    batch_size: int = 15,
) -> dict[str, pd.DataFrame]:
    names = list(dict.fromkeys(tickers))
    cache = Path(cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    output: dict[str, pd.DataFrame] = {}
    pending = []
    for ticker in names:
        path = _cache_file(cache, ticker, "5m", days)
        if path.exists() and not refresh:
            try:
                cached = pd.read_csv(path, index_col=0, parse_dates=True)
                frame = _normalise(cached)
                if not frame.empty:
                    output[ticker] = frame
                    continue
            except Exception:
                pass
        pending.append(ticker)
    for start in range(0, len(pending), batch_size):
        batch = pending[start : start + batch_size]
        try:
            downloaded = yf.download(
                batch,
                period=f"{days}d",
                interval="5m",
                group_by="ticker",
                auto_adjust=False,
                progress=False,
                threads=True,
                timeout=30,
            )
        except Exception:
            downloaded = pd.DataFrame()
        for ticker in batch:
            frame = _extract(downloaded, ticker, len(batch) == 1)
            if not frame.empty:
                output[ticker] = frame
                frame.to_csv(_cache_file(cache, ticker, "5m", days))
        if start + batch_size < len(pending):
            time.sleep(1)
    return output


def download_daily(
    tickers: Iterable[str],
    period: str = "6mo",
    batch_size: int = 30,
    cache_dir: str | Path = "data",
    refresh: bool = False,
    retries: int = 2,
) -> dict[str, pd.DataFrame]:
    names = list(dict.fromkeys(tickers))
    cache = Path(cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    output: dict[str, pd.DataFrame] = {}
    pending = []
    for ticker in names:
        path = cache / f"{ticker.replace('^', 'IDX_')}_1d_{period}.csv"
        if path.exists() and not refresh:
            frame = _normalise(pd.read_csv(path, index_col=0, parse_dates=True))
            if not frame.empty:
                output[ticker] = frame
                continue
        pending.append(ticker)
    for _ in range(retries + 1):
        pending = [ticker for ticker in pending if ticker not in output]
        if not pending:
            break
        _download_daily_batches(pending, period, batch_size, cache, output)
    return output


def _download_daily_batches(
    names: list[str],
    period: str,
    batch_size: int,
    cache: Path,
    output: dict[str, pd.DataFrame],
) -> None:
    for start in range(0, len(names), batch_size):
        batch = names[start : start + batch_size]
        try:
            downloaded = yf.download(
                batch,
                period=period,
                interval="1d",
                group_by="ticker",
                auto_adjust=False,
                progress=False,
                threads=True,
                timeout=30,
            )
        except Exception:
            continue
        for ticker in batch:
            frame = _extract(downloaded, ticker, len(batch) == 1)
            if not frame.empty:
                output[ticker] = frame
                frame.to_csv(cache / f"{ticker.replace('^', 'IDX_')}_1d_{period}.csv")


def build_daily_features(
    intraday: dict[str, pd.DataFrame],
    daily: dict[str, pd.DataFrame],
) -> dict[str, dict[date, dict[str, float]]]:
    result: dict[str, dict[date, dict[str, float]]] = {}
    for ticker, bars in intraday.items():
        day_frame = daily.get(ticker, pd.DataFrame()).copy()
        if not day_frame.empty:
            previous = day_frame["close"].shift(1)
            true_range = pd.concat(
                [
                    day_frame["high"] - day_frame["low"],
                    (day_frame["high"] - previous).abs(),
                    (day_frame["low"] - previous).abs(),
                ],
                axis=1,
            ).max(axis=1)
            day_frame["atr14"] = true_range.ewm(alpha=1 / 14, adjust=False).mean()
            day_frame["avg_value"] = (day_frame["close"] * day_frame["volume"]).shift(1).rolling(20, min_periods=5).mean()
            day_frame["avg_volume"] = day_frame["volume"].shift(1).rolling(20, min_periods=5).mean()
        features: dict[date, dict[str, float]] = {}
        grouped = bars.groupby(bars.index.date)
        prior_or_volumes: list[float] = []
        for trading_date, frame in grouped:
            first = frame.iloc[0]
            prior_daily = day_frame[day_frame.index.date < trading_date].tail(1) if not day_frame.empty else pd.DataFrame()
            previous_close = float(prior_daily["close"].iloc[-1]) if not prior_daily.empty else float(first["open"])
            atr_value = float(prior_daily["atr14"].iloc[-1]) if not prior_daily.empty else float(first["open"] * 0.02)
            avg_value = float(prior_daily["avg_value"].iloc[-1]) if not prior_daily.empty else float((frame["close"] * frame["volume"]).sum())
            avg_volume = float(prior_daily["avg_volume"].iloc[-1]) if not prior_daily.empty else float(frame["volume"].sum())
            or_volume = float(frame.between_time("09:15", "09:29")["volume"].sum())
            features[trading_date] = {
                "atr": atr_value,
                "avg_value_cr": avg_value / 10_000_000,
                "avg_daily_volume": avg_volume,
                "avg_first_15_volume": float(np.mean(prior_or_volumes[-10:])) if prior_or_volumes else avg_volume * 0.12,
                "gap_pct": ((float(first["open"]) / previous_close) - 1) * 100 if previous_close else 0.0,
            }
            prior_or_volumes.append(or_volume)
        result[ticker] = features
    return result


def download_macro_history() -> pd.DataFrame:
    tickers = ["^NSEI", "^GSPC", "^IXIC", "^DJI", "^N225", "^HSI", "^KS11", "^INDIAVIX", "INR=X", "BZ=F"]
    try:
        frame = yf.download(tickers, period="6mo", interval="1d", auto_adjust=False, progress=False, threads=True, timeout=30)
        closes = frame["Close"] if isinstance(frame.columns, pd.MultiIndex) else frame[["Close"]]
        return closes.pct_change(fill_method=None)
    except Exception:
        return pd.DataFrame()
