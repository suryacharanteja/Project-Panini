"""ToolGateway — the mandatory gate between orchestration and tools.

This is the only interface `core/` has to tools; the raw registry is never
injected into the orchestrator, and a ruff banned-api rule fails the build if
anyone imports it there (HLD §1.3, LLD Index §3.2).

The invocation sequence is ordered deliberately. Three steps distinguish it from
a conventional permission model:

* **Egress is classified before permission is resolved** — so exfiltration cannot
  hide inside something the tier system reads as harmless.
* **Tier is escalated when the turn is tainted** — breaking the confused-deputy
  chain without relying on the model to refuse an injected instruction.
* **The result is tagged RETRIEVED, tainting the turn** — a tool's *output* is
  untrusted content. Reading email taints the turn; that is the point.
"""

from __future__ import annotations

from typing import Any

from panini.errors import ApprovalDenied, ToolNotAvailable
from panini.events.bus import EventBus
from panini.security.approvals import ApprovalManager, new_invocation_id
from panini.security.credentials import CredentialStore
from panini.security.egress import EgressClassifier
from panini.security.permissions import PermissionResolver
from panini.security.protocols import ToolRegistryLike, ToolResultLike, TurnLike
from panini.security.types import Outcome, Provenance, ToolSpec
from panini.store.audit import AuditStore


class ToolGateway:
    """Executes tools, subject to provenance, taint, tier, egress, and audit."""

    def __init__(
        self,
        registry: ToolRegistryLike,
        *,
        permissions: PermissionResolver,
        egress: EgressClassifier,
        approvals: ApprovalManager,
        audit: AuditStore,
        credentials: CredentialStore,
        bus: EventBus,
    ) -> None:
        self._registry = registry
        self._permissions = permissions
        self._egress = egress
        self._approvals = approvals
        self._audit = audit
        self._credentials = credentials
        self._bus = bus

    # ── discovery ────────────────────────────────────────────────────────────

    def list_available(self, turn: TurnLike) -> list[ToolSpec]:
        """Security specs for enabled tools (FR-404)."""
        return [
            self._registry.spec(name)
            for name in self._registry.names()
            if self._registry.is_enabled(name)
        ]

    def tool_schemas(self, turn: TurnLike) -> list[dict[str, Any]]:
        """Model-facing schemas for enabled tools, parameters included.

        Disabled tools are omitted entirely rather than advertised and refused,
        so the model never proposes an action that cannot happen (FR-404).
        """
        return self._registry.schemas(enabled_only=True)

    # ── the gate ─────────────────────────────────────────────────────────────

    async def invoke(
        self, turn: TurnLike, tool_name: str, args: dict[str, Any]
    ) -> ToolResultLike:
        # 1 — resolve. Unknown or disabled tools never reach execution.
        spec = self._registry.spec(tool_name)
        if not self._registry.is_enabled(tool_name):
            raise ToolNotAvailable(f"tool {tool_name!r} is disabled")

        # 2 — classify egress BEFORE any permission decision, so the tier
        #     resolver can account for it.
        egress = self._egress.classify(spec, args, turn.tainted)

        # 3 — resolve effective tier, escalating if the turn is tainted.
        decision = self._permissions.decide(spec, egress, turn.tainted)

        invocation_id = new_invocation_id()

        # 4 — audit PENDING before anything executes. AuditWriteFailure
        #     propagates: an action that cannot be accounted for does not happen.
        audit_id = await self._audit.begin(
            session_id=turn.session_id,
            turn_id=turn.id,
            invocation_id=invocation_id,
            tool_name=tool_name,
            args=args,
            provenance=Provenance.RETRIEVED if turn.tainted else Provenance.USER,
            tainted=turn.tainted,
            taint_sources=turn.taint_sources,
            decision=decision,
        )

        self._bus.publish(
            "tool.invoked",
            {
                "turn_id": turn.id,
                "tool": tool_name,
                "tainted": turn.tainted,
                "escalated": decision.was_escalated,
                "is_egress": decision.is_egress,
            },
        )

        # 5 — approval, if required.
        if decision.approval_required:
            granted = await self._approvals.request(
                invocation_id=invocation_id,
                spec=spec,
                args=args,
                decision=decision,
                taint_sources=turn.taint_sources,
                destination_known=self._permissions.destination_is_known(decision.destination),
            )
            if not granted:
                await self._audit.complete(
                    audit_id, Outcome.DENIED, approval_granted=False
                )
                raise ApprovalDenied(tool_name)

        # 6 — resolve credential handles at the boundary. Values exist only from
        #     here to the tool call, never in context, logs, or audit.
        creds = self._credentials.resolve(spec.credential_handles)

        # 7 — execute.
        try:
            result = await self._registry.execute(tool_name, args, creds)  # type: ignore[arg-type]
        except Exception as exc:
            await self._audit.complete(
                audit_id,
                Outcome.ERROR,
                error=str(exc),
                approval_granted=True if decision.approval_required else None,
            )
            raise

        # 8 — audit outcome.
        await self._audit.complete(
            audit_id,
            Outcome.OK if result.ok else Outcome.ERROR,
            error=result.error,
            approval_granted=True if decision.approval_required else None,
        )

        # 9 — TAINT. A tool's output is untrusted content, whether or not the
        #     call succeeded: an error message can carry attacker-influenced text
        #     just as a result can (invariant I-6).
        turn.add_context(
            content=result.content,
            provenance=Provenance.RETRIEVED,
            source=f"{tool_name}:{result.source_ref or '-'}",
        )

        return result
