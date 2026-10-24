from datetime import datetime, timedelta, timezone

import pytest

from algo.sentiment.news import Headline, NewsStore, map_symbols, normalize_title
from algo.sentiment.keyword import keyword_score
from algo.sentiment.premarket import blend_bias, regime, weighted_score


def test_weighted_sentiment_skips_missing_and_renormalizes():
    score, details = weighted_score(
        {
            "gift_nifty": (0.006, 0.35, 0.006, 1),
            "us_futures": (None, 0.15, 0.006, 1),
            "usd_inr": (0.003, 0.05, 0.003, -1),
        }
    )
    assert score == pytest.approx(0.875 * 0.761594 - 0.125 * 0.761594, rel=1e-4)
    assert details["us_futures"]["status"] == "missing"


def test_bias_and_regime_thresholds():
    bias = blend_bias(premarket_score=0.5, market_news_score=-0.5, nifty=101, nifty_vwap=100)
    assert bias > 0.25
    assert regime(bias) == "BULLISH"
    assert regime(-0.25) == "NEUTRAL"


def test_keyword_scorer_is_transparent_and_clipped():
    positive = keyword_score("Company beats estimates, wins major order and raises guidance")
    negative = keyword_score("SEBI ban after fraud probe and default")
    assert 0.2 <= positive.score <= 1
    assert positive.matches
    assert -1 <= negative.score <= -0.2


def test_news_dedupes_maps_aliases_and_decays():
    now = datetime(2026, 1, 5, 10, 0, tzinfo=timezone.utc)
    aliases = {"RELIANCE": ["Reliance Industries", "RIL"], "SBIN": ["State Bank of India", "SBI"]}
    assert normalize_title(" RIL   wins ORDER! ") == "ril wins order"
    assert map_symbols("Reliance Industries wins a refinery order", "", aliases) == ["RELIANCE"]
    store = NewsStore(aliases=aliases, half_life_minutes=90)
    item = Headline("1", "RIL wins major order", "", now, 0.8, 1.0, ["RELIANCE"], False)
    assert store.add(item, now=now)
    assert not store.add(item, now=now)
    assert store.stock_score("RELIANCE", now + timedelta(minutes=90)) == pytest.approx(0.4)


def test_news_actions_filter_and_severe_market_pause():
    now = datetime(2026, 1, 5, 10, 0, tzinfo=timezone.utc)
    store = NewsStore(aliases={}, half_life_minutes=90)
    store.add(Headline("m", "market crash", "", now, -0.8, 0.9, [], True), now=now)
    assert store.market_score(now) == pytest.approx(-0.72)
    assert store.long_pause_until == now + timedelta(minutes=15)

