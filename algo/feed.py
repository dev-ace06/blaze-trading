from __future__ import annotations

import queue
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Callable, Iterable, Sequence

import pandas as pd

from .clock import IST, MarketClock
from .history import Downloader, split_download, yf_download

NIFTY_SYMBOL = "NIFTY50"
NIFTY_INDEX_NAME = "Nifty 50"
NIFTY_TICKER = "^NSEI"
MAX_DRAIN = 200_000


@dataclass(frozen=True)
class Tick:
    symbol: str
    price: float
    timestamp: datetime
    volume: float = 0.0


class PriceFeed(ABC):
    """Source of ticks. `volume` on a tick is the shares traded since the previous tick."""

    realtime = False
    latency_seconds = 0.0
    replays_history = False

    @abstractmethod
    def start(self, symbols: Sequence[str]) -> None: ...

    @abstractmethod
    def poll(self) -> list[Tick]: ...

    @abstractmethod
    def ltp(self, symbol: str) -> float: ...

    def stop(self) -> None:
        return None

    def health(self) -> dict[str, Any]:
        return {}


class QueueFeed(PriceFeed):
    def __init__(self) -> None:
        self._queue: queue.SimpleQueue[Tick] = queue.SimpleQueue()
        self._last: dict[str, float] = {}

    def start(self, symbols: Sequence[str]) -> None:
        return None

    def push(self, tick: Tick) -> None:
        self._last[tick.symbol] = tick.price
        self._queue.put(tick)

    def poll(self) -> list[Tick]:
        ticks: list[Tick] = []
        while len(ticks) < MAX_DRAIN:
            try:
                ticks.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return ticks

    def ltp(self, symbol: str) -> float:
        if symbol not in self._last:
            raise RuntimeError(f"no price yet for {symbol}")
        return self._last[symbol]


class ScriptedFeed(QueueFeed):
    """Feed driven by explicit `push` calls (tests, simulation)."""


class BrokerFeed(QueueFeed):
    """Streams ticks from a broker adapter that exposes `subscribe(symbols, callback, indices=...)`."""

    realtime = True
    latency_seconds = 2.0

    def __init__(self, broker: Any, clock: MarketClock, on_error: Callable[[str], None] | None = None) -> None:
        super().__init__()
        self.broker = broker
        self.clock = clock
        self.on_error = on_error
        self._cumulative: dict[str, float] = {}
        self.messages = 0

    def start(self, symbols: Sequence[str]) -> None:
        self.broker.login()
        if hasattr(self.broker, "load_instruments"):
            self.broker.load_instruments([symbol for symbol in symbols if symbol != NIFTY_SYMBOL])
        self.broker.subscribe(list(symbols), self.on_message, indices={NIFTY_SYMBOL: NIFTY_INDEX_NAME})

    def on_message(self, message: dict[str, Any]) -> None:
        symbol = message.get("symbol")
        price = float(message.get("ltp") or 0)
        if not symbol or price <= 0:
            return
        cumulative = message.get("volume")
        delta = 0.0
        if cumulative is not None:
            cumulative = float(cumulative)
            previous = self._cumulative.get(symbol)
            if previous is not None and cumulative >= previous:
                delta = cumulative - previous
            self._cumulative[symbol] = cumulative
        self.messages += 1
        self.push(Tick(symbol, price, message.get("timestamp") or self.clock.now(), delta))

    def stop(self) -> None:
        if hasattr(self.broker, "unsubscribe"):
            self.broker.unsubscribe()

    def health(self) -> dict[str, Any]:
        return {"messages": self.messages, "error": getattr(self.broker, "feed_error", None)}


def bars_to_ticks(symbol: str, bars: pd.DataFrame, last_bar: pd.Timestamp | None, now: datetime) -> tuple[list[Tick], pd.Timestamp | None]:
    """Turn 1-minute bars into ticks. Completed bars emit O/H/L/C ticks carrying the bar volume;
    the in-progress bar emits a price-only tick so stops see a fresh price."""
    ticks: list[Tick] = []
    for stamp, open_, high, low, close, volume in bars[["open", "high", "low", "close", "volume"]].itertuples(name=None):
        if last_bar is not None and stamp <= last_bar:
            continue
        start = stamp.to_pydatetime()
        end = start + timedelta(minutes=1)
        if end <= now:
            path = (open_, low, high, close) if close >= open_ else (open_, high, low, close)
            for index, (price, offset) in enumerate(zip(path, (0, 15, 30, 59))):
                ticks.append(Tick(symbol, float(price), start + timedelta(seconds=offset), float(volume) if index == 3 else 0.0))
            last_bar = stamp
        else:
            ticks.append(Tick(symbol, float(close), max(start, min(now, end - timedelta(seconds=1))), 0.0))
    return ticks, last_bar


class YFinanceFeed(QueueFeed):
    """Fallback feed: polls 1-minute bars in one batch call. Works with zero broker credentials."""

    latency_seconds = 120.0
    replays_history = True

    def __init__(
        self,
        tickers: dict[str, str],
        clock: MarketClock,
        poll_seconds: float = 45,
        downloader: Downloader = yf_download,
    ) -> None:
        super().__init__()
        self.tickers = dict(tickers)
        self.clock = clock
        self.poll_seconds = poll_seconds
        self.downloader = downloader
        self._last_bar: dict[str, pd.Timestamp] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_error: str | None = None
        self.polls = 0

    def start(self, symbols: Sequence[str]) -> None:
        if self._thread and self._thread.is_alive():
            return
        wanted = set(symbols)
        self.tickers = {symbol: ticker for symbol, ticker in self.tickers.items() if symbol in wanted}
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="yfinance-feed", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll_once()
                self.last_error = None
            except Exception as exc:
                self.last_error = type(exc).__name__
            self._stop.wait(self.poll_seconds)

    def poll_once(self) -> int:
        now = self.clock.now()
        if now.time() < time(9, 15):
            return 0
        downloaded = self.downloader(list(self.tickers.values()), "1d", "1m")
        pushed = 0
        for symbol, ticker in self.tickers.items():
            bars = split_download(downloaded, ticker)
            if bars.empty:
                continue
            bars = bars[bars.index.date == now.date()]
            ticks, self._last_bar[symbol] = bars_to_ticks(symbol, bars, self._last_bar.get(symbol), now)
            for tick in ticks:
                self.push(tick)
            pushed += len(ticks)
        self.polls += 1
        return pushed

    def stop(self) -> None:
        self._stop.set()

    def health(self) -> dict[str, Any]:
        return {"polls": self.polls, "error": self.last_error}


@dataclass(frozen=True)
class Scenario:
    """Waypoints (HH:MM, price) per symbol plus optional volume multipliers (start, end, factor)."""

    paths: dict[str, list[tuple[str, float]]]
    base_volume_per_minute: dict[str, float]
    volume_boosts: dict[str, list[tuple[str, str, float]]] | None = None


def _minutes(text: str) -> float:
    hour, minute = text.split(":")[:2]
    return int(hour) * 60 + int(minute)


def interpolate(waypoints: list[tuple[str, float]], minute: float) -> float:
    points = [(_minutes(at), price) for at, price in waypoints]
    if minute <= points[0][0]:
        return points[0][1]
    for (t0, p0), (t1, p1) in zip(points, points[1:]):
        if t0 <= minute <= t1:
            return p0 if t1 == t0 else p0 + (p1 - p0) * (minute - t0) / (t1 - t0)
    return points[-1][1]


class SyntheticMarket(ScriptedFeed):
    """Deterministic scripted day. `advance(now)` pushes every tick up to `now`."""

    def __init__(self, scenario: Scenario, day: date, tick_seconds: int = 20, start: str = "09:15", end: str = "15:30") -> None:
        super().__init__()
        self.scenario = scenario
        self.day = day
        self.tick_seconds = tick_seconds
        self._next = datetime.combine(day, time.fromisoformat(start), tzinfo=IST)
        self._end = datetime.combine(day, time.fromisoformat(end), tzinfo=IST)

    def _volume(self, symbol: str, minute: float) -> float:
        factor = 1.0
        for begin, finish, boost in (self.scenario.volume_boosts or {}).get(symbol, []):
            if _minutes(begin) <= minute < _minutes(finish):
                factor = boost
        return self.scenario.base_volume_per_minute[symbol] * factor * self.tick_seconds / 60

    def advance(self, now: datetime) -> int:
        pushed = 0
        while self._next <= min(now, self._end):
            minute = self._next.hour * 60 + self._next.minute + self._next.second / 60
            for symbol, waypoints in self.scenario.paths.items():
                price = round(interpolate(waypoints, minute), 2)
                self.push(Tick(symbol, price, self._next, self._volume(symbol, minute)))
                pushed += 1
            self._next += timedelta(seconds=self.tick_seconds)
        return pushed


def symbols_with_index(symbols: Iterable[str]) -> list[str]:
    return [*symbols, NIFTY_SYMBOL]
