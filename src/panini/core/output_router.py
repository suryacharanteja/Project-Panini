"""Streaming segment parser (FR-221, FR-222, DD-122).

The model marks typed segments inline:

    ⟦emotion:thinking⟧
    Here is the script:
    ⟦code:python⟧
    def greet(name): ...
    ⟦/code⟧

Parsing happens incrementally so segments emit as they complete rather than
after the whole response — the TTS pipeline needs text as it arrives.

**Content is never discarded.** An unknown type renders as text, an unclosed tag
is closed at end of stream, and a malformed delimiter is treated as literal
text. A parser that dropped content on a bad tag would lose the answer because
the model mistyped a marker (SR-02).
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass

from panini.core.turn import OutputSegment

OPEN = re.compile(r"⟦([a-z_]+)(?::([a-z0-9_+-]+))?⟧", re.IGNORECASE)
CLOSE = re.compile(r"⟦/([a-z_]+)⟧", re.IGNORECASE)

KNOWN_TYPES = frozenset({
    "text", "code", "image", "video", "data", "diagram", "chart", "action",
})

EMOTIONS = frozenset({
    "neutral", "happy", "thinking", "concerned", "surprised", "apologetic",
})


@dataclass(slots=True)
class Emotion:
    """Model-declared emotion (DD-122). Emitted separately from the text stream
    so the avatar can react before speech begins."""

    value: str


class OutputRouter:
    """Incremental parser turning a token stream into typed segments."""

    def __init__(self) -> None:
        self._buffer = ""
        self._current_type = "text"
        self._current_meta: dict[str, str] = {}
        self._content = ""

    def feed(self, token: str) -> Iterator[OutputSegment | Emotion]:
        self._buffer += token
        yield from self._drain()

    def flush(self) -> Iterator[OutputSegment]:
        """End of stream: emit whatever remains, closing any open tag."""
        self._content += self._buffer
        self._buffer = ""
        if self._content.strip():
            yield self._emit()

    # ── internals ────────────────────────────────────────────────────────────

    def _drain(self) -> Iterator[OutputSegment | Emotion]:
        while True:
            open_m = OPEN.search(self._buffer)
            close_m = CLOSE.search(self._buffer)

            first = min(
                (m for m in (open_m, close_m) if m), key=lambda m: m.start(), default=None
            )
            if first is None:
                # A partial marker at the tail must not be flushed as text, or a
                # tag split across tokens would leak into the output.
                if (cut := self._safe_cut()) > 0:
                    self._content += self._buffer[:cut]
                    self._buffer = self._buffer[cut:]
                return

            self._content += self._buffer[: first.start()]
            self._buffer = self._buffer[first.end() :]

            if first is close_m:
                if self._content.strip():
                    yield self._emit()
                self._reset()
                continue

            kind = first.group(1).lower()
            modifier = (first.group(2) or "").lower()

            if kind == "emotion":
                yield Emotion(modifier if modifier in EMOTIONS else "neutral")
                continue

            if self._content.strip():
                yield self._emit()

            # An unrecognised type becomes text rather than being dropped.
            self._current_type = kind if kind in KNOWN_TYPES else "text"
            self._current_meta = {"language": modifier} if modifier else {}
            self._content = ""

    def _safe_cut(self) -> int:
        """Length of buffer safe to flush without splitting a marker."""
        idx = self._buffer.rfind("⟦")
        return len(self._buffer) if idx == -1 else idx

    def _emit(self) -> OutputSegment:
        segment = OutputSegment(
            content_type=self._current_type,
            content=self._content.strip("\n"),
            metadata=dict(self._current_meta),
        )
        self._content = ""
        return segment

    def _reset(self) -> None:
        self._current_type = "text"
        self._current_meta = {}
        self._content = ""
