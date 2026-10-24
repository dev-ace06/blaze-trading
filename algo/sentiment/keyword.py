from __future__ import annotations

import re
from dataclasses import dataclass


POSITIVE_LEXICON = {
    "order win": 0.35,
    "wins major order": 0.35,
    "beats estimates": 0.35,
    "upgrade": 0.25,
    "raises guidance": 0.35,
    "record profit": 0.3,
    "strong growth": 0.25,
    "approval": 0.2,
    "dividend": 0.15,
    "buyback": 0.25,
}

NEGATIVE_LEXICON = {
    "downgrade": -0.25,
    "fraud": -0.5,
    "probe": -0.3,
    "sebi ban": -0.5,
    "misses estimates": -0.35,
    "resigns": -0.25,
    "default": -0.5,
    "fire": -0.3,
    "raid": -0.4,
    "crash": -0.5,
    "sanctions": -0.3,
}

TRIGGER_KEYWORDS = set(POSITIVE_LEXICON) | set(NEGATIVE_LEXICON)


@dataclass(frozen=True)
class KeywordResult:
    score: float
    matches: tuple[str, ...]


def keyword_score(text: str) -> KeywordResult:
    normalized = re.sub(r"\s+", " ", text.lower())
    matches: list[str] = []
    score = 0.0
    for phrase, weight in {**POSITIVE_LEXICON, **NEGATIVE_LEXICON}.items():
        if phrase in normalized:
            matches.append(phrase)
            score += weight
    return KeywordResult(max(-1.0, min(1.0, score)), tuple(matches))
