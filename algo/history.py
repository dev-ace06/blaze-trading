from __future__ import annotations

import csv
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable, Protocol
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf


IST = ZoneInfo("Asia/Kolkata")
NIFTY_TICKER = "^NSEI"
VIX_TICKER = "^INDIAVIX"
OHLCV = ["open", "high", "low", "close", "volume"]


@dataclass(frozen=True)
class UniverseRow:
    symbol: str
    ticker: str
    company_name: str
    aliases: tuple[str, ...] = ()


@dataclass
class SymbolHistory:
    symbol: str
    prev_close: float = 0.0
    daily_atr: float = 0.0
    avg_value_cr: float = 0.0
    avg_daily_volume: float = 0.0
    avg_first15_volume: float = 0.0
    avg_5m_volume: float = 0.0
    slot_volume: dict[str, float] = field(default_factory=dict)
    seed_5m: pd.DataFrame | None = field(default=None, repr=False)

    @property
    def daily_atr_pct(self) -> float:
        return self.daily_atr / self.prev_close * 100 if self.prev_close else 0.0

    def expected_volume(self, slot: str) -> float:
        for value in (self.slot_volume.get(slot), self.avg_5m_volume, self.avg_daily_volume / 75):
            if value and value > 0:
                return float(value)
        return 0.0


@dataclass(frozen=True)
class MarketContext:
    nifty_prev_close: float | None = None
    vix: float | None = None


class HistoryLoader(Protocol):
    def load(self, universe: list[UniverseRow], today: date) -> tuple[dict[str, SymbolHistory], MarketContext]: ...

    def today_bars(self, universe: list[UniverseRow], today: date) -> dict[str, pd.DataFrame]: ...


def load_universe(path: str | Path = "universe.csv") -> list[UniverseRow]:
    with Path(path).open(encoding="utf-8", newline="") as handle:
        rows = csv.DictReader(handle)
        return [
            UniverseRow(
                row["symbol"].strip(),
                row["yfinance_ticker"].strip(),
                row.get("company_name", "").strip(),
                tuple(alias.strip() for alias in (row.get("aliases") or "").split("|") if alias.strip()),
            )
            for row in rows
            if row.get("symbol", "").strip()
        ]


def normalise_bars(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame(columns=OHLCV)
    output = frame.copy()
    output.columns = [str(column).lower().replace(" ", "_") for column in output.columns]
    if any(column not in output for column in OHLCV):
        return pd.DataFrame(columns=OHLCV)
    output = output[OHLCV].apply(pd.to_numeric, errors="coerce").dropna(subset=["open", "high", "low", "close"])
    output["volume"] = output["volume"].fillna(0)
    output = output.sort_index()
    if not isinstance(output.index, pd.DatetimeIndex):
        output.index = pd.to_datetime(output.index)
    output.index = output.index.tz_localize(IST) if output.index.tz is None else output.index.tz_convert(IST)
    return output[~output.index.duplicated(keep="last")]


def split_download(downloaded: pd.DataFrame, ticker: str) -> pd.DataFrame:
    if downloaded is None or downloaded.empty:
        return normalise_bars(pd.DataFrame())
    if not isinstance(downloaded.columns, pd.MultiIndex):
        return normalise_bars(downloaded)
    try:
        if ticker in set(map(str, downloaded.columns.get_level_values(0))):
            return normalise_bars(downloaded[ticker])
        return normalise_bars(downloaded.xs(ticker, axis=1, level=1))
    except (KeyError, TypeError):
        return normalise_bars(pd.DataFrame())


def yf_download(tickers: list[str], period: str, interval: str) -> pd.DataFrame:
    return yf.download(
        tickers,
        period=period,
        interval=interval,
        group_by="ticker",
        auto_adjust=False,
        progress=False,
        threads=True,
        timeout=30,
    )


Downloader = Callable[[list[str], str, str], pd.DataFrame]


def download_batches(
    tickers: list[str],
    period: str,
    interval: str,
    batch_size: int = 25,
    retries: int = 3,
    downloader: Downloader = yf_download,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, pd.DataFrame]:
    names = list(dict.fromkeys(tickers))
    output: dict[str, pd.DataFrame] = {}
    for start in range(0, len(names), batch_size):
        batch = names[start : start + batch_size]
        for attempt in range(1, retries + 1):
            try:
                downloaded = downloader(batch, period, interval)
            except Exception:
                downloaded = pd.DataFrame()
            frames = {ticker: split_download(downloaded, ticker) for ticker in batch}
            output.update({ticker: frame for ticker, frame in frames.items() if not frame.empty})
            if any(not frame.empty for frame in frames.values()) or attempt == retries:
                break
            sleep(2 ** attempt)
    return output


def build_symbol_history(symbol: str, daily: pd.DataFrame | None, intraday: pd.DataFrame | None, today: date) -> SymbolHistory:
    history = SymbolHistory(symbol)
    if daily is not None and not daily.empty:
        past = daily[daily.index.date < today]
        if not past.empty:
            previous = past["close"].shift(1)
            true_range = pd.concat(
                [past["high"] - past["low"], (past["high"] - previous).abs(), (past["low"] - previous).abs()], axis=1
            ).max(axis=1)
            tail = past.tail(20)
            history.prev_close = float(past["close"].iloc[-1])
            history.daily_atr = float(true_range.ewm(alpha=1 / 14, adjust=False).mean().iloc[-1])
            history.avg_value_cr = float((tail["close"] * tail["volume"]).mean()) / 10_000_000
            history.avg_daily_volume = float(tail["volume"].mean())
    if intraday is not None and not intraday.empty:
        past = intraday[intraday.index.date < today]
        sessions = sorted(set(past.index.date))[-10:]
        past = past[pd.Index(past.index.date).isin(sessions)]
        if not past.empty:
            history.seed_5m = past
            history.slot_volume = past["volume"].groupby(past.index.strftime("%H:%M")).mean().to_dict()
            history.avg_5m_volume = float(past["volume"].mean())
            opening = past.between_time("09:15", "09:29")
            per_day = opening.groupby(opening.index.date)["volume"].agg(["sum", "count"])
            per_day = per_day[per_day["count"] >= 3]
            history.avg_first15_volume = float(per_day["sum"].mean()) if not per_day.empty else 0.0
    return history


class YFinanceHistory:
    def __init__(self, batch_size: int = 25, downloader: Downloader = yf_download, sleep: Callable[[float], None] = time.sleep) -> None:
        self.batch_size = batch_size
        self.downloader = downloader
        self.sleep = sleep

    def _fetch(self, tickers: list[str], period: str, interval: str) -> dict[str, pd.DataFrame]:
        return download_batches(tickers, period, interval, self.batch_size, 3, self.downloader, self.sleep)

    def load(self, universe: list[UniverseRow], today: date) -> tuple[dict[str, SymbolHistory], MarketContext]:
        tickers = [row.ticker for row in universe]
        daily = self._fetch(tickers + [NIFTY_TICKER, VIX_TICKER], "3mo", "1d")
        intraday = self._fetch(tickers, "1mo", "5m")
        histories = {
            row.symbol: build_symbol_history(row.symbol, daily.get(row.ticker), intraday.get(row.ticker), today)
            for row in universe
        }
        nifty = daily.get(NIFTY_TICKER)
        vix = daily.get(VIX_TICKER)
        nifty_past = nifty[nifty.index.date < today] if nifty is not None and not nifty.empty else None
        context = MarketContext(
            float(nifty_past["close"].iloc[-1]) if nifty_past is not None and not nifty_past.empty else None,
            float(vix["close"].iloc[-1]) if vix is not None and not vix.empty else None,
        )
        return histories, context

    def today_bars(self, universe: list[UniverseRow], today: date) -> dict[str, pd.DataFrame]:
        by_ticker = self._fetch([row.ticker for row in universe] + [NIFTY_TICKER], "1d", "5m")
        output = {}
        for row in universe:
            frame = by_ticker.get(row.ticker)
            if frame is not None and not frame.empty:
                output[row.symbol] = frame[frame.index.date == today]
        return output
