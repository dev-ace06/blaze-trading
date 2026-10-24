from __future__ import annotations

import csv
import json
import logging
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any


REDACT_KEYS = {"consumer_key", "consumer_secret", "mpin", "totp", "totp_secret", "api_key", "token", "secret"}


def _safe(value: Any) -> Any:
    if is_dataclass(value):
        value = asdict(value)
    if isinstance(value, dict):
        return {key: ("***" if any(term in key.lower() for term in REDACT_KEYS) else _safe(item)) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


class DecisionLogger:
    def __init__(self, log_dir: str | Path = "logs", day: date | None = None) -> None:
        self.day = day or date.today()
        self.path = Path(log_dir) / f"{self.day.isoformat()}.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.console = logging.getLogger("sentinel")
        if not self.console.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
            self.console.addHandler(handler)
            self.console.setLevel(logging.INFO)

    def log(self, event: str, reason: str, **fields: Any) -> None:
        payload = _safe({"timestamp": datetime.now().astimezone(), "event": event, "reason": reason, **fields})
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
        self.console.info("%s | %s | %s", event, fields.get("symbol", "market"), reason)


def write_eod_report(
    output_dir: str | Path,
    summary: dict[str, Any],
    trades: list[Any],
) -> None:
    path = Path(output_dir)
    path.mkdir(parents=True, exist_ok=True)
    (path / "summary.json").write_text(json.dumps(_safe(summary), indent=2), encoding="utf-8")
    with (path / "trades.csv").open("w", newline="", encoding="utf-8") as handle:
        rows = [_safe(trade) for trade in trades]
        if rows:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        else:
            handle.write("symbol,side,qty,entry_price,exit_price,net_pnl,reason\n")
