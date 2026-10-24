from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Any, Callable

import pandas as pd
import requests
import yfinance as yf


def weighted_score(
    components: dict[str, tuple[float | None, float, float, int]],
) -> tuple[float, dict[str, dict[str, float | str | None]]]:
    usable = {name: values for name, values in components.items() if values[0] is not None}
    total_weight = sum(values[1] for values in usable.values())
    details: dict[str, dict[str, float | str | None]] = {}
    score = 0.0
    for name, (raw, weight, scale, sign) in components.items():
        if raw is None:
            details[name] = {"raw": None, "score": None, "status": "missing"}
            continue
        component_score = math.tanh((float(raw) / float(scale)) * int(sign))
        normalized_weight = weight / total_weight if total_weight else 0.0
        score += normalized_weight * component_score
        details[name] = {
            "raw": float(raw),
            "score": component_score,
            "weight": normalized_weight,
            "status": "ok",
        }
    return max(-1.0, min(1.0, score)), details


def day_bias(premarket_score: float, market_news_score: float) -> float:
    return 0.7 * premarket_score + 0.3 * market_news_score


def blend_bias(premarket_score: float, market_news_score: float, nifty: float, nifty_vwap: float) -> float:
    base = day_bias(premarket_score, market_news_score)
    trend = 0.0 if not nifty_vwap else math.tanh(((nifty - nifty_vwap) / nifty_vwap) / 0.003)
    return max(-1.0, min(1.0, 0.5 * base + 0.5 * trend))


def regime(bias: float) -> str:
    if bias > 0.25:
        return "BULLISH"
    if bias < -0.25:
        return "BEARISH"
    return "NEUTRAL"


def vix_multiplier(vix: float | None) -> float:
    if vix is None or vix <= 15:
        return 1.0
    if vix <= 20:
        return 0.8
    return 0.6


def _pct_change(frame: pd.DataFrame) -> float | None:
    if frame.empty or "Close" not in frame or len(frame["Close"].dropna()) < 2:
        return None
    closes = frame["Close"].dropna()
    return float(closes.iloc[-1] / closes.iloc[-2] - 1)


def fetch_yfinance_changes(tickers: list[str], period: str = "1mo") -> dict[str, float | None]:
    output: dict[str, float | None] = {}
    for ticker in tickers:
        try:
            output[ticker] = _pct_change(yf.download(ticker, period=period, progress=False, auto_adjust=False))
        except Exception:
            output[ticker] = None
    return output


def fetch_fii_cash(timeout: float = 8) -> float | None:
    session = requests.Session()
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json,text/plain,*/*"}
    try:
        session.get("https://www.nseindia.com", headers=headers, timeout=timeout)
        response = session.get(
            "https://www.nseindia.com/api/fiidiiTradeReact",
            headers=headers,
            timeout=timeout,
        )
        response.raise_for_status()
        rows = response.json()
        fii = next((row for row in rows if "FII" in str(row.get("category", "")).upper()), None)
        if not fii:
            return None
        return float(str(fii.get("netValue", "0")).replace(",", ""))
    except Exception:
        return None


def collect_premarket(
    gift_gap: float | None = None,
    output_path: str | Path | None = None,
    market_fetcher: Callable[[list[str]], dict[str, float | None]] = fetch_yfinance_changes,
) -> tuple[float, dict[str, Any]]:
    changes = market_fetcher(["ES=F", "NQ=F", "^GSPC", "^IXIC", "^DJI", "^N225", "^HSI", "^KS11", "^INDIAVIX", "INR=X", "BZ=F"])

    def average(names: list[str]) -> float | None:
        values = [changes.get(name) for name in names if changes.get(name) is not None]
        return sum(values) / len(values) if values else None

    components = {
        "gift_nifty": (gift_gap, 0.35, 0.006, 1),
        "us_futures": (average(["ES=F", "NQ=F"]), 0.15, 0.006, 1),
        "us_prior_close": (average(["^GSPC", "^IXIC", "^DJI"]), 0.10, 0.010, 1),
        "asia": (average(["^N225", "^HSI", "^KS11"]), 0.15, 0.008, 1),
        "india_vix": (changes.get("^INDIAVIX"), 0.10, 0.10, -1),
        "usd_inr": (changes.get("INR=X"), 0.05, 0.003, -1),
        "brent": (changes.get("BZ=F"), 0.05, 0.020, -1),
        "fii_cash_cr": (fetch_fii_cash(), 0.05, 3000, 1),
    }
    score, details = weighted_score(components)
    payload = {"premarket_score": score, "components": details}
    if output_path:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return score, payload
