"""Per-turn taint state (DD-111).

Taint is the load-bearing control of the content-initiated security model. Once
a turn has touched retrieved content, every write or execute tool requires
explicit approval for the remainder of that turn — enforced here in code, not by
asking the model to behave.

Two properties are deliberate and load-bearing:

* **Monotonic.** There is no ``clear()``. Taint ends only when the Turn object is
  discarded; a new turn constructs a fresh TaintState (FR-511, FR-512).
* **Event-loop confined.** Mutation off the loop would race with the orchestrator
  and could leave a turn untainted while a tool result is in flight (HR-04).
"""

from __future__ import annotations

import asyncio


class TaintState:
    """Tracks whether a turn has ingested untrusted content, and from where.

    Note the absence of any method to reset. That is the design, not an omission.
    """

    __slots__ = ("_loop", "_sources", "_tainted")

    def __init__(self) -> None:
        self._tainted: bool = False
        self._sources: list[str] = []
        # Bind to the loop that created us, if there is one. Tests that construct
        # a TaintState synchronously get no binding and no assertion.
        try:
            self._loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None

    @property
    def tainted(self) -> bool:
        return self._tainted

    @property
    def sources(self) -> list[str]:
        """Sources that tainted this turn, in order, for the approval dialog."""
        return list(self._sources)

    def mark(self, source: str) -> None:
        """Mark the turn tainted, attributing it to ``source``.

        Idempotent with respect to the flag; sources accumulate without
        duplicates so an approval prompt can name every origin (FR-514).
        """
        self._assert_loop()
        self._tainted = True
        if source not in self._sources:
            self._sources.append(source)

    def _assert_loop(self) -> None:
        """Fail loudly if mutated off the owning event loop (HR-04)."""
        if self._loop is None:
            return
        try:
            current = asyncio.get_running_loop()
        except RuntimeError:
            raise RuntimeError(
                "TaintState.mark() called outside the event loop. Worker threads "
                "must return results for the loop to apply, never mutate turn "
                "state directly (HLD §7)."
            ) from None
        if current is not self._loop:
            raise RuntimeError(
                "TaintState.mark() called from a different event loop than the one "
                "that created the turn (HR-04)."
            )

    def __repr__(self) -> str:
        if not self._tainted:
            return "TaintState(clean)"
        return f"TaintState(tainted, sources={self._sources!r})"
