"""Fact retrieval — memory tier 3 (DD-118, DD-119, DD-121).

Scoring is **lexical, not semantic** (DD-119). With a ~300-token budget only
three to five facts load per turn, so overlap on a small curated set performs
acceptably, adds no model dependency, and is debuggable — the user can see why
a fact was selected. Embedding retrieval is revisitable if measurement shows it
necessary.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime

from panini.store.facts import Fact

STOPWORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "to", "of", "in",
    "on", "at", "for", "with", "and", "or", "but", "my", "me", "i", "you", "your",
    "it", "this", "that", "what", "when", "where", "who", "how", "do", "does",
    "did", "can", "could", "would", "should", "please", "tell", "show",
})

_WORD = re.compile(r"[a-z0-9']+")

#: Longest-first so "ations" is stripped before "s". Deliberately crude: the aim
#: is to collapse obvious inflections ("authorised" / "authorises" /
#: "authorisation" → "authoris"), not to be linguistically correct. Live testing
#: showed that without this, a query and the fact it should match frequently
#: share no tokens at all and retrieval silently returns nothing.
_SUFFIXES = (
    "ations", "ation", "ising", "izing", "ised", "ized", "ises", "izes",
    "ements", "ement", "ingly", "ing", "edly", "ies", "ied", "ers", "er",
    "est", "ly", "ed", "es", "s",
)

#: Below this length, stripping does more harm than good ("is" → "i").
_MIN_STEM = 4


def _stem(word: str) -> str:
    for suffix in _SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= _MIN_STEM:
            return word[: -len(suffix)]
    return word

#: Roughly four characters per token. Precise enough for a budget check, and it
#: avoids pulling in a tokeniser for a decision this coarse.
CHARS_PER_TOKEN = 4


def _tokens(text: str) -> set[str]:
    return {
        _stem(w)
        for w in _WORD.findall(text.lower())
        if w not in STOPWORDS and len(w) > 2
    }


def _recency(last_used_at: str | None) -> float:
    """Decay from 1.0 to 0.0 over roughly a month of disuse."""
    if not last_used_at:
        return 0.0
    try:
        age_days = (datetime.now(UTC) - datetime.fromisoformat(last_used_at)).days
    except ValueError:
        return 0.0
    return math.exp(-age_days / 30.0)


def _frequency(use_count: int) -> float:
    return min(1.0, math.log1p(use_count) / math.log(20))


@dataclass(frozen=True, slots=True)
class ScoredFact:
    fact: Fact
    score: float
    matched: list[str]

    @property
    def why(self) -> str:
        """Explanation for the Memory view — retrieval should be inspectable."""
        if self.matched:
            return f"matched: {', '.join(sorted(self.matched))}"
        return "recently used"


@dataclass(frozen=True, slots=True)
class Retrieved:
    facts: list[Fact]
    scored: list[ScoredFact]
    taint_sources: list[str]

    @property
    def taints_turn(self) -> bool:
        """Whether loading these facts must taint the turn (DD-121 M-2).

        A confirmed fact of untrusted origin keeps its provenance: the user
        approved its use, not its content.
        """
        return bool(self.taint_sources)

    def render(self) -> str:
        if not self.facts:
            return ""
        lines = "\n".join(f"- {f.statement}" for f in self.facts)
        return f"Things you know about the user:\n{lines}"


class FactRetriever:
    def __init__(self, score_threshold: float = 0.25) -> None:
        self._threshold = score_threshold

    def select(self, facts: list[Fact], query: str, budget_tokens: int) -> Retrieved:
        """Choose facts relevant to ``query`` within the token budget.

        ``facts`` must already be confirmed-only — the store filters in SQL so an
        unconfirmed fact cannot reach this path (DD-121 M-1).
        """
        query_tokens = _tokens(query)
        scored: list[ScoredFact] = []

        for fact in facts:
            fact_tokens = _tokens(f"{fact.subject} {fact.statement}")
            matched = query_tokens & fact_tokens
            overlap = len(matched) / len(query_tokens) if query_tokens else 0.0

            score = (
                0.6 * overlap
                + 0.3 * _recency(fact.last_used_at)
                + 0.1 * _frequency(fact.use_count)
            )
            if score >= self._threshold:
                scored.append(ScoredFact(fact, score, sorted(matched)))

        scored.sort(key=lambda s: s.score, reverse=True)

        chosen: list[ScoredFact] = []
        used = 0
        for candidate in scored:
            cost = len(candidate.fact.statement) // CHARS_PER_TOKEN + 4
            if used + cost > budget_tokens:
                continue
            chosen.append(candidate)
            used += cost

        # M-2: a fact of untrusted origin taints the turn it enters, even though
        # the user confirmed it. Provenance survives approval.
        taint_sources = [c.fact.taint_source for c in chosen if c.fact.taints_on_use]

        return Retrieved(
            facts=[c.fact for c in chosen],
            scored=chosen,
            taint_sources=taint_sources,
        )
