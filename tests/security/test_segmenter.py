"""Streaming segmenter (DD-124, DD-125, SR-06).

Two properties matter and neither is checkable by eye:

* abbreviations must not split, or the assistant says "Doctor. Chen";
* no chunk may exceed 60 characters, or synthesis enters the 5.4× cost regime
  measured in validation and playback starves mid-reply.
"""

from __future__ import annotations

import pytest

from panini.voice.segmenter import (
    ChunkLimits,
    StreamingSegmenter,
    is_sentence_boundary,
    split_long,
)


def stream(text: str, size: int = 5, limits: ChunkLimits | None = None) -> list[str]:
    """Feed text in small pieces, exercising the streaming path."""
    seg = StreamingSegmenter(limits)
    out = []
    for i in range(0, len(text), size):
        out.extend(c.text for c in seg.feed(text[i : i + size]))
    out.extend(c.text for c in seg.flush())
    return out


# ── boundary detection (SR-06) ──────────────────────────────────────────────

@pytest.mark.parametrize(
    "text",
    [
        "Dr. Chen is my dentist.",
        "Mrs. Patel called.",
        "Use e.g. this one.",
        "Compare i.e. the second.",
        "Acme Inc. filed today.",
        "See fig. 4 for detail.",
    ],
)
def test_abbreviation_does_not_split(text: str) -> None:
    chunks = stream(text)
    assert len(chunks) == 1, f"split incorrectly: {chunks}"


def test_decimal_does_not_split() -> None:
    assert stream("It is 3.5 inches wide.") == ["It is 3.5 inches wide."]


def test_ip_address_does_not_split() -> None:
    assert len(stream("Connect to 192.168.1.1 now.")) == 1


def test_initials_do_not_split() -> None:
    assert len(stream("J. R. R. Tolkien wrote it.")) == 1


def test_acronym_does_not_split() -> None:
    assert len(stream("She lives in the U.S.A. today.")) == 1


def test_genuine_sentences_do_split() -> None:
    chunks = stream("First one. Second one. Third one.")
    assert len(chunks) == 3


def test_question_and_exclamation_split() -> None:
    assert len(stream("Really? Yes! Good.")) == 3


# ── streaming behaviour ─────────────────────────────────────────────────────

def test_terminator_at_buffer_end_defers() -> None:
    """Undecidable until the next character arrives."""
    assert is_sentence_boundary("Hello.", 5) is None
    assert is_sentence_boundary("Hello. ", 5) is True
    assert is_sentence_boundary("Hello.x", 5) is False


def test_flush_resolves_a_pending_deferral() -> None:
    seg = StreamingSegmenter()
    assert list(seg.feed("Done.")) == []      # deferred
    assert [c.text for c in seg.flush()] == ["Done."]


def test_chunking_is_independent_of_token_size() -> None:
    text = "First sentence. Second sentence. Third one."
    assert stream(text, 1) == stream(text, 7) == stream(text, 100)


def test_no_content_is_lost() -> None:
    text = "Alpha. Beta gamma. Delta epsilon zeta."
    joined = " ".join(stream(text))
    for word in ("Alpha", "Beta", "gamma", "Delta", "epsilon", "zeta"):
        assert word in joined


# ── measured chunk caps (DD-124) ────────────────────────────────────────────

def test_first_chunk_respects_the_tighter_cap() -> None:
    """First chunk governs time-to-first-audio: 0.20 + 0.0083x40 ≈ 0.53 s."""
    long = (
        "This opening sentence is deliberately much longer than forty characters "
        "so that it must be divided before speech can begin."
    )
    chunks = stream(long)
    assert len(chunks[0]) <= ChunkLimits().first_max


def test_no_chunk_exceeds_the_hard_cap() -> None:
    """Above ~62 chars synthesis cost per character rises 5.4×."""
    text = (
        "I checked your calendar for tomorrow and found a conflict between the "
        "team meeting at ten and your dentist appointment. I have drafted an "
        "email proposing three alternative times, and it is ready for your "
        "review whenever you would like to see it."
    )
    for chunk in stream(text):
        assert len(chunk) <= ChunkLimits().hard_max, f"{len(chunk)}: {chunk!r}"


def test_the_starvation_case_from_validation_is_split() -> None:
    """The measured 1.86 s gap came from a 95-character second sentence."""
    text = (
        "I found five new emails. Two are from your project team about the "
        "schedule change, and one is marked urgent from finance."
    )
    chunks = stream(text)
    assert len(chunks[0]) <= 40                       # fast first audio
    assert all(len(c) <= 60 for c in chunks)          # none above the knee
    assert len(chunks) >= 3                           # the long sentence divided


# ── clause splitting ────────────────────────────────────────────────────────

def test_split_prefers_strong_clause_boundaries() -> None:
    text = "This is the first part; this is the second part of the sentence."
    parts = split_long(text, 40, 60)
    assert parts[0].endswith(";") or parts[0].endswith("part;")


def test_split_uses_conjunctions_before_bare_commas() -> None:
    text = "We reviewed the numbers, and then we agreed on the revised plan today."
    parts = split_long(text, 40, 60)
    assert len(parts) >= 2
    assert all(len(p) <= 60 for p in parts)


def test_split_falls_back_to_whitespace() -> None:
    """A long run of words with no punctuation must still be divided."""
    text = " ".join(["word"] * 40)
    parts = split_long(text, 40, 60)
    assert all(len(p) <= 60 for p in parts)
    assert "".join(p.replace(" ", "") for p in parts) == text.replace(" ", "")


def test_short_text_is_not_split() -> None:
    assert split_long("Short enough.", 40, 60) == ["Short enough."]


# ── run-on protection ───────────────────────────────────────────────────────

def test_run_on_without_punctuation_still_emits_early() -> None:
    """A reply with no sentence end must not delay speech indefinitely."""
    seg = StreamingSegmenter()
    emitted: list[str] = []
    for _ in range(40):
        emitted.extend(c.text for c in seg.feed("and then more words "))
    assert emitted, "nothing emitted before flush on a run-on reply"
    assert all(len(c) <= ChunkLimits().hard_max for c in emitted)


def test_no_characters_are_lost_anywhere() -> None:
    """Stronger than word-presence: every non-space character must survive.

    Chunk boundaries are computed by index arithmetic, where an off-by-one
    silently swallows a word — and a dropped word in speech is invisible until
    someone notices the assistant said something slightly wrong.
    """
    import re

    cases = [
        "I checked your calendar for tomorrow and found a conflict between the "
        "team meeting at ten and your dentist appointment. I have drafted an "
        "email proposing three alternative times, and it is ready for your "
        "review whenever you would like to see it.",
        "Dr. Chen said e.g. 3.5 inches. Then J. R. R. Tolkien wrote about the U.S.A.",
        "supercalifragilisticexpialidociousandthensomemoreletters at all",
        "One. Two. Three.",
    ]
    for text in cases:
        joined = "".join(stream(text))
        assert re.sub(r"\s+", "", joined) == re.sub(r"\s+", "", text), text[:40]
