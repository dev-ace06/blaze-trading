from __future__ import annotations

import csv
import hashlib
import html
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import quote_plus

import feedparser
import requests

from .keyword import TRIGGER_KEYWORDS, keyword_score


MARKET_TERMS = {
    "rbi", "repo rate", "fed", "inflation", "cpi", "gdp", "budget", "war", "crash",
    "tariff", "sanctions", "sensex", "nifty",
}


def normalize_title(title: str) -> str:
    text = html.unescape(title).lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def title_hash(title: str) -> str:
    return hashlib.sha256(normalize_title(title).encode()).hexdigest()


def map_symbols(title: str, summary: str, aliases: dict[str, list[str]]) -> list[str]:
    haystack = normalize_title(f"{title} {summary}")
    result = []
    for symbol, names in aliases.items():
        candidates = [symbol, *names]
        if any(re.search(rf"(?<![a-z0-9]){re.escape(normalize_title(alias))}(?![a-z0-9])", haystack) for alias in candidates if alias):
            result.append(symbol)
    return result


def is_market_wide(title: str, summary: str = "") -> bool:
    text = normalize_title(f"{title} {summary}")
    return any(re.search(rf"\b{re.escape(term)}\b", text) for term in MARKET_TERMS)


@dataclass(frozen=True)
class Headline:
    id: str
    title: str
    summary: str
    published: datetime
    score: float
    severity: float
    symbols: list[str]
    market_wide: bool
    reason: str = "keyword"
    source: str = ""


@dataclass
class NewsStore:
    aliases: dict[str, list[str]]
    half_life_minutes: float = 90
    max_age_hours: float = 18
    headlines: list[Headline] = field(default_factory=list)
    long_pause_until: datetime | None = None
    _hashes: set[str] = field(default_factory=set)

    def add(self, item: Headline, now: datetime | None = None) -> bool:
        reference = now or datetime.now(timezone.utc)
        published = item.published if item.published.tzinfo else item.published.replace(tzinfo=timezone.utc)
        if reference - published > timedelta(hours=self.max_age_hours):
            return False
        digest = title_hash(item.title)
        if digest in self._hashes:
            return False
        self._hashes.add(digest)
        self.headlines.append(item)
        if item.market_wide and item.score <= -0.6 and item.severity >= 0.8:
            until = published + timedelta(minutes=15)
            self.long_pause_until = max(until, self.long_pause_until) if self.long_pause_until else until
        return True

    def is_new(self, item: Headline, now: datetime | None = None) -> bool:
        reference = now or datetime.now(timezone.utc)
        published = item.published if item.published.tzinfo else item.published.replace(tzinfo=timezone.utc)
        return reference - published <= timedelta(hours=self.max_age_hours) and title_hash(item.title) not in self._hashes

    def _score(self, predicate: Callable[[Headline], bool], now: datetime) -> float:
        total = 0.0
        for item in self.headlines:
            if not predicate(item):
                continue
            age_minutes = max(0.0, (now - item.published).total_seconds() / 60)
            decay = 0.5 ** (age_minutes / self.half_life_minutes)
            total += item.score * item.severity * decay
        return max(-1.0, min(1.0, total))

    def stock_score(self, symbol: str, now: datetime) -> float:
        return self._score(lambda item: symbol in item.symbols, now)

    def market_score(self, now: datetime) -> float:
        return self._score(lambda item: item.market_wide, now)


def _parse_date(entry) -> datetime:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if parsed:
        return datetime(*parsed[:6], tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


def fetch_rss(url: str, timeout: float = 8) -> list[dict]:
    try:
        response = requests.get(url, timeout=timeout, headers={"User-Agent": "SentinelORB/1.0"})
        response.raise_for_status()
        feed = feedparser.parse(response.content)
        return [
            {
                "title": entry.get("title", ""),
                "summary": entry.get("summary", ""),
                "published": _parse_date(entry),
                "link": entry.get("link", ""),
                "source": url,
            }
            for entry in feed.entries
        ]
    except Exception:
        return []


def google_news_url(query: str) -> str:
    return f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=en-IN&gl=IN&ceid=IN:en"


def fetch_nse_announcements(timeout: float = 8) -> list[dict]:
    session = requests.Session()
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json,text/plain,*/*"}
    try:
        session.get("https://www.nseindia.com", headers=headers, timeout=timeout)
        response = session.get(
            "https://www.nseindia.com/api/corporate-announcements?index=equities",
            headers=headers,
            timeout=timeout,
        )
        response.raise_for_status()
        return [
            {
                "title": f"{row.get('sm_name') or row.get('symbol') or ''} {row.get('desc', '')}".strip(),
                "summary": row.get("attchmntText", ""),
                "published": datetime.now(timezone.utc),
                "link": row.get("attchmntFile", ""),
                "source": "NSE",
            }
            for row in response.json()
        ]
    except Exception:
        return []


class NewsEngine:
    def __init__(
        self,
        store: NewsStore,
        sources: list[str],
        report_path: str | Path,
        timeout: float = 8,
        poll_seconds: int = 90,
        *,
        fetcher: Callable[[], list[dict]] | None = None,
        scorer=None,
        extra_queries: Callable[[], list[str]] | None = None,
        on_headline: Callable[[Headline], None] | None = None,
        on_error: Callable[[str, Exception], None] | None = None,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self.store = store
        self.sources = sources
        self.report_path = Path(report_path)
        self.timeout = timeout
        self.poll_seconds = poll_seconds
        self.fetcher = fetcher
        self.scorer = scorer
        self.extra_queries = extra_queries
        self.on_headline = on_headline
        self.on_error = on_error
        self.now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self.polls = 0
        self._report_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _fetch(self) -> list[dict]:
        if self.fetcher is not None:
            return self.fetcher()
        urls = list(self.sources)
        if self.extra_queries is not None:
            urls += [google_news_url(query) for query in self.extra_queries()]
        raw: list[dict] = []
        with ThreadPoolExecutor(max_workers=6) as pool:
            for rows in pool.map(lambda url: fetch_rss(url, self.timeout), urls):
                raw.extend(rows)
        raw.extend(fetch_nse_announcements(self.timeout))
        return raw

    def _keyword_headline(self, row: dict) -> tuple[Headline, bool]:
        result = keyword_score(f"{row['title']} {row['summary']}")
        item = Headline(
            id=title_hash(row["title"]),
            title=row["title"],
            summary=row["summary"],
            published=row["published"],
            score=result.score,
            severity=min(1.0, max(0.2, abs(result.score))),
            symbols=map_symbols(row["title"], row["summary"], self.store.aliases),
            market_wide=is_market_wide(row["title"], row["summary"]),
            reason=f"keywords:{','.join(result.matches)}",
            source=row.get("source", ""),
        )
        worth_llm = bool(item.symbols or item.market_wide) and (abs(result.score) >= 0.2 or any(m in TRIGGER_KEYWORDS for m in result.matches))
        return item, worth_llm

    def _llm_rescore(self, items: list[Headline]) -> list[Headline]:
        if self.scorer is None or not items:
            return items
        payload = [
            {"id": item.id, "title": item.title, "summary": item.summary[:300], "symbols": item.symbols, "market_wide": item.market_wide}
            for item in items
        ]
        try:
            results = {result.id: result for result in self.scorer.score(payload)}
        except Exception as exc:
            if self.on_error:
                self.on_error("gemini", exc)
            return items
        known = set(self.store.aliases)
        rescored = []
        for item in items:
            result = results.get(item.id)
            if result is None:
                rescored.append(item)
                continue
            symbols = list(dict.fromkeys([*item.symbols, *[s for s in result.symbols if s in known]]))
            rescored.append(
                Headline(
                    item.id, item.title, item.summary, item.published, result.sentiment, result.severity,
                    symbols, item.market_wide or result.market_wide, f"{'keyword' if 'keyword fallback' in result.reason else 'gemini'}:{result.reason}", item.source,
                )
            )
        return rescored

    def poll_once(self) -> int:
        now = self.now_fn()
        self.polls += 1
        scored: list[tuple[Headline, bool]] = []
        for row in self._fetch():
            item, worth_llm = self._keyword_headline(row)
            if self.store.is_new(item, now):
                scored.append((item, worth_llm))
        llm_ids = {item.id for item, worth in scored if worth}
        rescored = {item.id: item for item in self._llm_rescore([item for item, worth in scored if worth])}
        added = 0
        for item, _ in scored:
            item = rescored.get(item.id, item) if item.id in llm_ids else item
            if self.store.add(item, now=now):
                self.append_report(item, "score_updated")
                added += 1
                if self.on_headline:
                    try:
                        self.on_headline(item)
                    except Exception as exc:
                        if self.on_error:
                            self.on_error("on_headline", exc)
        return added

    def append_report(self, item: Headline, action: str, symbols: list[str] | None = None) -> None:
        with self._report_lock:
            self.report_path.parent.mkdir(parents=True, exist_ok=True)
            new_file = not self.report_path.exists()
            with self.report_path.open("a", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                if new_file:
                    writer.writerow(["published", "source", "title", "symbols", "score", "severity", "action", "reason"])
                writer.writerow([item.published.isoformat(), item.source, item.title, "|".join(symbols if symbols is not None else item.symbols), item.score, item.severity, action, item.reason])

    _append_report = append_report

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()

        def loop() -> None:
            while not self._stop.is_set():
                try:
                    self.poll_once()
                except Exception as exc:
                    if self.on_error:
                        try:
                            self.on_error("poll", exc)
                        except Exception:
                            pass
                self._stop.wait(self.poll_seconds)

        self._thread = threading.Thread(target=loop, name="news-engine", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
