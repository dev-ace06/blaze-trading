from __future__ import annotations

from datetime import datetime, time
from typing import Callable
from zoneinfo import ZoneInfo


IST = ZoneInfo("Asia/Kolkata")


def parse_time(value: str) -> time:
    return time.fromisoformat(value)


class MarketClock:
    def __init__(
        self,
        now_fn: Callable[[], datetime] | None = None,
        entry_start: str = "09:30",
        entry_end: str = "14:15",
        square_off: str = "14:50",
    ) -> None:
        self._now_fn = now_fn or (lambda: datetime.now(IST))
        self.entry_start = parse_time(entry_start)
        self.entry_end = parse_time(entry_end)
        self.square_off = parse_time(square_off)

    def now(self) -> datetime:
        value = self._now_fn()
        if value.tzinfo is None:
            return value.replace(tzinfo=IST)
        return value.astimezone(IST)

    def in_entry_window(self, value: datetime | None = None) -> bool:
        current = (value or self.now()).astimezone(IST).time().replace(tzinfo=None)
        return self.entry_start <= current <= self.entry_end

    def should_square_off(self, value: datetime | None = None) -> bool:
        current = (value or self.now()).astimezone(IST).time().replace(tzinfo=None)
        return current >= self.square_off
