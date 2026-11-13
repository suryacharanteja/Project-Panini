"""Context assembly with a token budget (FR-203, Conversation LLD §5).

Four invariants govern what may be dropped:

* **C-1** — the system prompt and current turn are never dropped.
* **C-2** — tool schemas for enabled tools are never dropped; dropping them
  silently removes capability the user believes they have.
* **C-3** — dropping emits ``context.trimmed`` so the UI can say earlier context
  was lost, rather than the assistant quietly forgetting.
* **C-4** — working memory drops **whole turns**, oldest first. A half-dropped
  turn leaves a question without its answer, which reads worse to the model
  than the turn being absent.
"""

from __future__ import annotations

from dataclasses import dataclass

from panini.core.memory.retrieval import CHARS_PER_TOKEN, FactRetriever, Retrieved
from panini.core.turn import Turn
from panini.events.bus import EventBus
from panini.llm.provider import Message
from panini.security.types import Provenance
from panini.store.facts import FactStore
from panini.store.history import HistoryStore, StoredTurn


def estimate_tokens(text: str) -> int:
    return len(text) // CHARS_PER_TOKEN + 1


@dataclass(frozen=True, slots=True)
class ContextBudget:
    """Shares of the window, against num_ctx = 8192 (DD-103)."""

    total: int = 8192
    response_headroom: int = 2000
    facts_tokens: int = 300
    summary_tokens: int = 400
    working_tokens: int = 2500


@dataclass(frozen=True, slots=True)
class AssembledContext:
    messages: list[Message]
    retrieved_facts: Retrieved
    turns_included: int
    turns_dropped: int

    @property
    def was_trimmed(self) -> bool:
        return self.turns_dropped > 0


class ContextAssembler:
    def __init__(
        self,
        history: HistoryStore,
        facts: FactStore,
        retriever: FactRetriever,
        bus: EventBus,
        *,
        budget: ContextBudget | None = None,
        working_turns: int = 6,
    ) -> None:
        self._history = history
        self._facts = facts
        self._retriever = retriever
        self._bus = bus
        self._budget = budget or ContextBudget()
        self._working_turns = working_turns

    async def assemble(self, system_prompt: str, turn: Turn) -> AssembledContext:
        """Build the message list for a turn.

        Facts are retrieved *before* the messages are built, because a fact of
        untrusted origin taints the turn — and the taint must be set before the
        first tool call is considered (DD-121 M-2).
        """
        messages: list[Message] = [Message(role="system", content=system_prompt)]

        # ── tier 3: facts ────────────────────────────────────────────────────
        confirmed = await self._facts.load_confirmed()
        retrieved = self._retriever.select(
            confirmed, turn.input.text, self._budget.facts_tokens
        )

        if retrieved.facts:
            if retrieved.taints_turn:
                # M-2 — provenance survives the user's confirmation. One context
                # element, but every contributing fact is named as a source so
                # the approval dialog can show them all.
                turn.add_context(
                    content=retrieved.render(),
                    provenance=Provenance.RETRIEVED,
                    source=retrieved.taint_sources[0],
                    extra_sources=retrieved.taint_sources[1:],
                )
            else:
                messages.append(Message(role="system", content=retrieved.render()))
            await self._facts.touch([f.id for f in retrieved.facts])

        # ── tier 2: session summary ──────────────────────────────────────────
        summary = await self._history.session_summary(turn.session_id)
        if summary:
            messages.append(
                Message(role="system", content=f"Earlier in this conversation: {summary}")
            )

        # ── tier 1: working memory ───────────────────────────────────────────
        recent = await self._history.recent_turns(turn.session_id, self._working_turns)
        included, dropped = self._fit_turns(recent, self._budget.working_tokens)

        for stored in included:
            messages.append(Message(role="user", content=stored.input_text))
            if answer := stored.assistant_text():
                messages.append(Message(role="assistant", content=answer))

        if dropped:
            # C-3 — say so rather than silently forgetting.
            self._bus.publish(
                "context.trimmed",
                {"turn_id": turn.id, "dropped_turns": dropped},
            )

        # ── current turn ─────────────────────────────────────────────────────
        for element in turn.context:
            if element.provenance is Provenance.RETRIEVED:
                messages.append(
                    Message(
                        role="user",
                        content=(
                            f'<retrieved source="{element.source}" trust="untrusted">\n'
                            f"{element.content}\n</retrieved>"
                        ),
                    )
                )
            else:
                messages.append(Message(role="user", content=element.content))

        messages.append(Message(role="user", content=turn.input.text))

        return AssembledContext(
            messages=messages,
            retrieved_facts=retrieved,
            turns_included=len(included),
            turns_dropped=dropped,
        )

    @staticmethod
    def _fit_turns(turns: list[StoredTurn], budget: int) -> tuple[list[StoredTurn], int]:
        """Fit whole turns into the budget, newest-first, then restore order.

        C-4 — a turn is included entirely or not at all.
        """
        kept: list[StoredTurn] = []
        used = 0
        for stored in reversed(turns):
            cost = estimate_tokens(stored.input_text) + estimate_tokens(stored.assistant_text())
            if used + cost > budget:
                break
            kept.append(stored)
            used += cost
        kept.reverse()
        return kept, len(turns) - len(kept)
