"""Streaming sentence and clause segmentation for TTS (DD-124, DD-125, SR-06).

Off-the-shelf segmenters (pysbd, NLTK Punkt, spaCy) assume **complete text**.
This one segments a live token stream while the LLM is still generating, which
requires two things they do not provide:

* **Deferred boundary resolution.** A ``.`` arriving at the end of the buffer
  cannot be judged yet — the next character decides whether it terminates a
  sentence. The decision waits rather than guessing.
* **Measured chunk caps.** Kokoro's synthesis cost per character rises **5.4×
  above ~62 characters** (0.0083 → 0.0446 s/char) while audio duration stays
  linear. Chunks are capped below that knee, so the cap is a *throughput*
  requirement, not only a time-to-first-audio one.

The first chunk is capped tighter still: ``0.20 + 0.0083 × 40 ≈ 0.53 s`` to
first audio, inside the 0.7 s budget with margin.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass

#: Words that end in a full stop without ending a sentence.
ABBREVIATIONS = frozenset({
    # titles
    "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "rev", "hon", "gen", "col",
    # latin
    "e.g", "i.e", "etc", "vs", "cf", "al", "viz", "ibid",
    # business
    "inc", "ltd", "co", "corp", "dept", "est", "approx", "no",
    # reference
    "fig", "vol", "pp", "ed", "eds", "chap", "sec",
    # calendar
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct",
    "nov", "dec", "mon", "tue", "tues", "wed", "thu", "thur", "thurs", "fri",
    "sat", "sun",
})

TERMINATORS = ".!?…"

#: Clause split points, most preferred first. A split at a semicolon reads
#: better than one at an arbitrary space.
_STRONG_CLAUSE = re.compile(r"[;:—–]")
_CONJUNCTION = re.compile(r",\s+(?:and|but|or|so|yet|then|because|although|while)\b")
_COMMA = re.compile(r",")

_WORD_TAIL = re.compile(r"[\w.]+$")


@dataclass(frozen=True, slots=True)
class ChunkLimits:
    """Caps derived from measurement (DD-124)."""

    first_max: int = 40
    max_chars: int = 55
    hard_max: int = 60
    min_chars: int = 15

    def cap_for(self, index: int) -> int:
        return self.first_max if index == 0 else self.max_chars


@dataclass(frozen=True, slots=True)
class Chunk:
    text: str
    index: int

    @property
    def is_first(self) -> bool:
        return self.index == 0


def _preceding_word(buf: str, i: int) -> str:
    match = _WORD_TAIL.search(buf[:i])
    return match.group(0) if match else ""


def is_sentence_boundary(buf: str, i: int) -> bool | None:
    """Whether ``buf[i]`` terminates a sentence.

    Returns ``None`` when undecidable from the text so far — the caller must
    wait for more input. This is the behaviour that makes streaming possible.
    """
    ch = buf[i]
    if ch not in TERMINATORS:
        return False

    # '!', '?' and '…' are unambiguous.
    if ch in "!?…":
        return True

    # A decimal or dotted number: 3.5, 192.168.1.1
    if i > 0 and buf[i - 1].isdigit() and i + 1 < len(buf) and buf[i + 1].isdigit():
        return False

    word = _preceding_word(buf, i).lower().rstrip(".")

    # Known abbreviation: "Dr.", "e.g."
    if word in ABBREVIATIONS:
        return False

    # A single initial: "J. R. R. Tolkien"
    if len(word) == 1 and word.isalpha():
        return False

    # Internal dots mark an acronym: "U.S.A."
    if "." in word:
        return False

    # A terminator at the end of the buffer is undecidable: the next character
    # tells us whether a sentence ended or a token was split.
    if i + 1 >= len(buf):
        return None

    if not buf[i + 1].isspace():
        return False

    return True


def split_long(text: str, cap: int, hard_cap: int) -> list[str]:
    """Divide a sentence into chunks of at most ``cap`` characters.

    ``cap`` is the target (40 for the first chunk, 55 after); ``hard_cap`` is the
    ceiling that must never be crossed, because beyond it synthesis enters the
    5.4× cost regime. A clause boundary slightly past ``cap`` is preferred over
    a mid-phrase break, but never past ``hard_cap``.
    """
    text = text.strip()
    if len(text) <= cap:
        return [text]

    parts: list[str] = []
    remaining = text

    while len(remaining) > cap:
        cut = _choose_cut(remaining, cap, hard_cap)
        parts.append(remaining[:cut].strip())
        remaining = remaining[cut:].lstrip(" ,;:—–")

    if remaining.strip():
        parts.append(remaining.strip())
    return [p for p in parts if p]


def _choose_cut(text: str, cap: int, hard_cap: int) -> int:
    """Best index to cut ``text`` at, in decreasing order of preference.

    Within the cap is preferred; a boundary between cap and hard_cap is accepted
    when nothing better exists, since a slightly long chunk reads better than a
    break mid-phrase.
    """
    within_cap = text[:cap]
    within_hard = text[: min(hard_cap, len(text))]

    # 1 — a clause boundary inside the target cap.
    if (cut := _clause_cut(within_cap)) is not None:
        return cut

    # 2 — a word boundary inside the target cap.
    if (cut := within_cap.rfind(" ")) > 0:
        return cut

    # 3 — a clause boundary between cap and hard cap.
    if (cut := _clause_cut(within_hard)) is not None:
        return cut

    # 4 — a word boundary before the hard cap.
    if (cut := within_hard.rfind(" ")) > 0:
        return cut

    # 5 — a single unbroken token longer than the cap. Cut it rather than let
    #     the chunk grow past the knee.
    return min(cap, len(text))


def _clause_cut(window: str) -> int | None:
    """Latest clause boundary in ``window``, preferring stronger punctuation."""
    for pattern in (_STRONG_CLAUSE, _CONJUNCTION, _COMMA):
        best: int | None = None
        for match in pattern.finditer(window):
            end = match.end()
            if end < 5:            # too short to be a useful chunk
                continue
            best = end             # take the latest, to fill the chunk
        if best is not None:
            return best
    return None


class StreamingSegmenter:
    """Turns a token stream into TTS-sized chunks.

    ``feed`` returns chunks that are complete; ``flush`` emits the remainder at
    end of stream, resolving any pending deferral.
    """

    def __init__(self, limits: ChunkLimits | None = None) -> None:
        self._limits = limits or ChunkLimits()
        self._buffer = ""
        self._index = 0

    def feed(self, token: str) -> Iterator[Chunk]:
        self._buffer += token
        yield from self._drain(final=False)

    def flush(self) -> Iterator[Chunk]:
        yield from self._drain(final=True)
        rest = self._buffer.strip()
        self._buffer = ""
        if rest:
            yield from self._emit(rest)

    # ── internals ────────────────────────────────────────────────────────────

    def _drain(self, final: bool) -> Iterator[Chunk]:
        while True:
            boundary = self._find_boundary(final)

            if boundary is None:
                # No complete sentence yet. If the buffer has grown past the
                # hard cap the reply is one long run-on, so emit a clause rather
                # than let time-to-first-audio drift.
                if len(self._buffer) > self._limits.hard_max * 2:
                    cap = self._limits.cap_for(self._index)
                    pieces = split_long(self._buffer, cap, self._limits.hard_max)
                    if len(pieces) > 1:
                        head, *rest = pieces
                        self._buffer = " ".join(rest)
                        yield from self._emit(head)
                        continue
                return

            sentence = self._buffer[: boundary + 1].strip()
            self._buffer = self._buffer[boundary + 1 :]
            if sentence:
                yield from self._emit(sentence)

    def _find_boundary(self, final: bool) -> int | None:
        for i in range(len(self._buffer)):
            verdict = is_sentence_boundary(self._buffer, i)
            if verdict is True:
                return i
            if verdict is None:
                # Undecidable. At end of stream treat it as a boundary; mid
                # stream, wait for the next token.
                return i if final else None
        return None

    def _emit(self, sentence: str) -> Iterator[Chunk]:
        cap = self._limits.cap_for(self._index)
        for piece in split_long(sentence, cap, self._limits.hard_max):
            yield Chunk(text=piece, index=self._index)
            self._index += 1
