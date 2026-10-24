from __future__ import annotations

import json
import re
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any

from .keyword import KeywordResult, keyword_score
from .news import title_hash


@dataclass(frozen=True)
class GeminiResult:
    id: str
    symbols: list[str]
    market_wide: bool
    sentiment: float
    severity: float
    horizon: str
    reason: str


class GeminiScorer:
    def __init__(
        self,
        api_key: str | None,
        model: str = "gemini-3.8-flash",
        calls_per_minute: int = 10,
        batch_size: int = 10,
    ) -> None:
        self.model = model
        self.calls_per_minute = calls_per_minute
        self.batch_size = batch_size
        self.cache: dict[str, GeminiResult] = {}
        self._calls: deque[float] = deque()
        self._lock = threading.Lock()
        self.client = None
        if api_key:
            try:
                from google import genai

                self.client = genai.Client(api_key=api_key)
            except Exception:
                self.client = None

    def _rate_limit(self) -> None:
        with self._lock:
            now = time.monotonic()
            while self._calls and now - self._calls[0] >= 60:
                self._calls.popleft()
            if len(self._calls) >= self.calls_per_minute:
                delay = 60 - (now - self._calls[0])
                if delay > 0:
                    time.sleep(delay)
            self._calls.append(time.monotonic())

    @staticmethod
    def _fallback(item: dict[str, Any]) -> GeminiResult:
        result = keyword_score(f"{item.get('title', '')} {item.get('summary', '')}")
        return GeminiResult(
            str(item["id"]),
            list(item.get("symbols", [])),
            bool(item.get("market_wide", False)),
            result.score,
            max(0.2, min(1.0, abs(result.score))),
            "intraday",
            f"keyword fallback: {', '.join(result.matches)}",
        )

    def score(self, headlines: list[dict[str, Any]]) -> list[GeminiResult]:
        output: list[GeminiResult] = []
        pending = []
        for item in headlines:
            digest = title_hash(item.get("title", ""))
            if digest in self.cache:
                output.append(self.cache[digest])
            else:
                pending.append((digest, item))
        for start in range(0, len(pending), self.batch_size):
            batch = pending[start : start + self.batch_size]
            scored = self._score_batch([item for _, item in batch])
            by_id = {item.id: item for item in scored}
            for digest, item in batch:
                result = by_id.get(str(item["id"])) or self._fallback(item)
                self.cache[digest] = result
                output.append(result)
        return output

    def _score_batch(self, batch: list[dict[str, Any]]) -> list[GeminiResult]:
        if not self.client:
            return [self._fallback(item) for item in batch]
        self._rate_limit()
        prompt = (
            "Classify these financial headlines for same-day Indian equity trading. Return only a JSON array "
            "of {id,symbols,market_wide,sentiment,severity,horizon,reason}; sentiment -1..1, severity 0..1, "
            "horizon intraday or longer. Headlines:\n" + json.dumps(batch, ensure_ascii=False)
        )
        try:
            response = self.client.models.generate_content(model=self.model, contents=prompt)
            text = response.text.strip()
            match = re.search(r"\[.*\]", text, re.DOTALL)
            rows = json.loads(match.group(0) if match else text)
            return [
                GeminiResult(
                    str(row["id"]),
                    list(row.get("symbols", [])),
                    bool(row.get("market_wide", False)),
                    max(-1.0, min(1.0, float(row["sentiment"]))),
                    max(0.0, min(1.0, float(row["severity"]))),
                    row["horizon"] if row.get("horizon") in {"intraday", "longer"} else "intraday",
                    str(row.get("reason", "Gemini")),
                )
                for row in rows
            ]
        except Exception:
            return [self._fallback(item) for item in batch]
