from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, time as dt_time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .broker.base import Broker
from .clock import MarketClock
from .config import AppConfig
from .report import DecisionLogger, write_eod_report


IST = ZoneInfo("Asia/Kolkata")


@dataclass(frozen=True)
class EngineStatus:
    mode: str
    now: str
    next_event: str
    action: str


class LiveEngine:
    def __init__(self, config: AppConfig, broker: Broker, mode: str, logger: DecisionLogger | None = None) -> None:
        self.config = config
        self.broker = broker
        self.mode = mode
        self.clock = MarketClock(
            entry_start=config.schedule.entry_start,
            entry_end=config.schedule.entry_end,
            square_off=config.schedule.square_off,
        )
        self.logger = logger or DecisionLogger()
        self.running = False

    def schedule_status(self) -> EngineStatus:
        now = self.clock.now()
        current = now.time().replace(tzinfo=None)
        events = [
            (dt_time.fromisoformat(self.config.schedule.startup), "startup/login and premarket"),
            (dt_time.fromisoformat(self.config.schedule.market_open), "opening range"),
            (dt_time.fromisoformat(self.config.schedule.entry_start), "entry window opens"),
            (dt_time.fromisoformat(self.config.schedule.entry_end), "entry window closes"),
            (dt_time.fromisoformat(self.config.schedule.square_off), "forced square-off"),
        ]
        next_event = next((label for at, label in events if current < at), "market day complete")
        action = "square_off" if self.clock.should_square_off(now) else "wait" if not self.clock.in_entry_window(now) else "monitor"
        return EngineStatus(self.mode, now.isoformat(), next_event, action)

    def flatten_all(self) -> None:
        positions = self.broker.positions()
        if isinstance(positions, dict) and "data" not in positions:
            rows = [{"symbol": symbol, "quantity": qty} for symbol, qty in positions.items()]
        else:
            rows = positions.get("data", []) if isinstance(positions, dict) else positions
        for row in rows or []:
            symbol = row.get("symbol") or row.get("tradingSymbol") or row.get("trdSym")
            quantity = int(float(row.get("quantity", row.get("netQty", 0)) or 0))
            if not symbol or not quantity:
                continue
            side = "SELL" if quantity > 0 else "BUY"
            error = None
            for attempt in range(1, self.config.broker.exit_retries + 1):
                try:
                    self.broker.place_order(symbol, side, abs(quantity), exit=True)
                    self.logger.log("exit", "forced_flatten", symbol=symbol, quantity=abs(quantity), attempt=attempt)
                    error = None
                    break
                except Exception as exc:
                    error = exc
                    self.logger.log("order_error", "exit_retry", symbol=symbol, attempt=attempt, error=type(exc).__name__)
                    time.sleep(min(2 ** (attempt - 1), 4))
            if error:
                self.logger.log("critical", "failed_to_flatten", symbol=symbol, error=type(error).__name__)

    def run(self, once: bool = False, dry_run: bool = False) -> EngineStatus:
        status = self.schedule_status()
        self.logger.log("schedule", status.action, mode=self.mode, now=status.now, next_event=status.next_event, dry_run=dry_run)
        if once or dry_run:
            return status
        self.broker.login()
        self.running = True
        try:
            while self.running:
                if Path("STOP").exists():
                    self.logger.log("kill_switch", "STOP file detected")
                    self.flatten_all()
                    break
                if self.clock.should_square_off():
                    self.flatten_all()
                    break
                self.logger.log("heartbeat", "engine_alive", mode=self.mode)
                time.sleep(5)
        except KeyboardInterrupt:
            self.logger.log("kill_switch", "Ctrl+C")
            self.flatten_all()
        finally:
            self.running = False
        return self.schedule_status()
