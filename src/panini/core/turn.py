"""Turn — the unit of conversation and the scope of taint.

``add_context`` is the only way context enters a turn, and the only place taint
is set. One mutation point, one invariant: if retrieved content is present, the
turn is tainted (Conversation LLD §2).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from panini.security.taint import TaintState
from panini.security.types import ContextElement, Provenance, ToolInvocation


class TurnState(StrEnum):
    CREATED = "created"
    GENERATING = "generating"
    TOOL_PENDING = "tool_pending"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"
    FAILED = "failed"

    @property
    def is_terminal(self) -> bool:
        return self in (TurnState.COMPLETED, TurnState.INTERRUPTED, TurnState.FAILED)


class Modality(StrEnum):
    VOICE = "voice"
    TEXT = "text"


@dataclass(slots=True)
class TurnInput:
    text: str
    modality: Modality
    provenance: Provenance = Provenance.USER


@dataclass(slots=True)
class OutputSegment:
    content_type: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(slots=True)
class Turn:
    """One user request and the assistant's response to it."""

    session_id: str
    input: TurnInput
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    started_at: str = field(default_factory=_utcnow)
    ended_at: str | None = None
    context: list[ContextElement] = field(default_factory=list)
    invocations: list[ToolInvocation] = field(default_factory=list)
    segments: list[OutputSegment] = field(default_factory=list)
    state: TurnState = TurnState.CREATED
    _taint: TaintState = field(default_factory=TaintState)

    # ── taint ────────────────────────────────────────────────────────────────

    @property
    def tainted(self) -> bool:
        return self._taint.tainted

    @property
    def taint_sources(self) -> list[str]:
        return self._taint.sources

    def add_context(
        self,
        content: str,
        provenance: Provenance,
        source: str,
        *,
        extra_sources: list[str] | None = None,
    ) -> None:
        """Append context, tainting the turn if the content is untrusted.

        The single mutation point for taint. Nothing else sets it, so there is
        one place to audit for correctness.

        ``extra_sources`` records additional origins for one context element —
        a batch of recalled facts is a single block of text but may derive from
        several untrusted turns, and the approval dialog should name all of them
        (FR-514).
        """
        self.context.append(
            ContextElement(
                content=content,
                provenance=provenance,
                source=source,
                added_at=_utcnow(),
            )
        )
        if provenance is Provenance.RETRIEVED:
            self._taint.mark(source)
            for extra in extra_sources or ():
                self._taint.mark(extra)

    # ── lifecycle ────────────────────────────────────────────────────────────

    def finish(self, state: TurnState) -> None:
        if not state.is_terminal:
            raise ValueError(f"{state} is not a terminal state")
        self.state = state
        self.ended_at = _utcnow()

    def pending_invocation_ids(self) -> list[str]:
        """Invocations still awaiting approval.

        Used on interruption: a dialog belonging to an abandoned turn must not
        remain answerable (Conversation LLD §8.1).
        """
        return [
            inv.invocation_id
            for inv in self.invocations
            if inv.approval_granted is None and inv.decision.approval_required
        ]

    def assistant_text(self) -> str:
        return "\n".join(s.content for s in self.segments if s.content_type == "text")
