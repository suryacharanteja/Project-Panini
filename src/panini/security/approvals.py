"""Approval request lifecycle (DD-114, DD-134, DD-135).

Every enforced control terminates here, at a human reading a dialog. Three
properties are load-bearing:

* **Single-use tokens bound to the invocation** — a reply cannot be replayed, and
  an approval for one action cannot authorise another (HR-05).
* **Timeout means deny** — an unattended machine must not approve by inaction.
* **No blanket approvals for T2/T3** — there is no API to grant one, so none can
  leak in later (FR-524).
"""

from __future__ import annotations

import asyncio
import secrets
import uuid
from dataclasses import dataclass
from typing import Any

from panini.events.bus import EventBus
from panini.security.types import ApprovalReason, GateDecision, Tier, ToolSpec

DEFAULT_TIMEOUT_S = 120.0


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    """Payload rendered by the approval dialog (Frontend LLD §6.3)."""

    invocation_id: str
    token: str
    tool_name: str
    human_action: str
    declared_tier: int
    effective_tier: int
    reason: str | None
    taint_sources: list[str]
    destination: str | None
    payload_preview: str | None
    destination_known: bool
    risk: str  # low | elevated | high — drives visual weight (DD-135)

    def to_payload(self) -> dict[str, Any]:
        return {
            "invocation_id": self.invocation_id,
            "token": self.token,
            "tool_name": self.tool_name,
            "human_action": self.human_action,
            "declared_tier": self.declared_tier,
            "effective_tier": self.effective_tier,
            "escalated": self.effective_tier > self.declared_tier,
            "reason": self.reason,
            "taint_sources": self.taint_sources,
            "destination": self.destination,
            "payload_preview": self.payload_preview,
            "destination_known": self.destination_known,
            "risk": self.risk,
        }


class _Pending:
    __slots__ = ("future", "token")

    def __init__(self, token: str) -> None:
        self.token = token
        self.future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()


class ApprovalManager:
    """Issues approval requests and awaits the user's decision."""

    def __init__(self, bus: EventBus, timeout_s: float = DEFAULT_TIMEOUT_S) -> None:
        self._bus = bus
        self._timeout = timeout_s
        self._pending: dict[str, _Pending] = {}

    async def request(
        self,
        *,
        invocation_id: str,
        spec: ToolSpec,
        args: dict[str, Any],
        decision: GateDecision,
        taint_sources: list[str],
        destination_known: bool,
    ) -> bool:
        """Prompt the user. Returns True only on an explicit approval."""
        token = secrets.token_urlsafe(32)
        pending = _Pending(token)
        self._pending[invocation_id] = pending

        request = ApprovalRequest(
            invocation_id=invocation_id,
            token=token,
            tool_name=spec.name,
            human_action=_describe(spec, args),
            declared_tier=int(decision.declared_tier),
            effective_tier=int(decision.effective_tier),
            reason=decision.reason.value if decision.reason else None,
            taint_sources=taint_sources,
            destination=decision.destination,
            payload_preview=decision.payload_preview,
            destination_known=destination_known,
            risk=_risk_level(decision, destination_known),
        )
        self._bus.publish("approval.requested", request.to_payload())

        try:
            return await asyncio.wait_for(pending.future, timeout=self._timeout)
        except TimeoutError:
            # Fail closed: silence is not consent.
            self._bus.publish(
                "approval.resolved",
                {"invocation_id": invocation_id, "granted": False, "reason": "timeout"},
            )
            return False
        except asyncio.CancelledError:
            # The turn was interrupted; a dialog for an abandoned turn must not
            # remain answerable (Conversation LLD §8.1).
            self._bus.publish(
                "approval.resolved",
                {"invocation_id": invocation_id, "granted": False, "reason": "cancelled"},
            )
            raise
        finally:
            self._pending.pop(invocation_id, None)

    def resolve(self, invocation_id: str, token: str, granted: bool) -> bool:
        """Apply the user's decision. Returns False if the reply is not valid.

        An unknown, reused, or mismatched token is rejected and reported — it
        indicates either a bug or an attempt to forge an approval.
        """
        pending = self._pending.get(invocation_id)
        if pending is None:
            self._bus.publish(
                "security.rejected_approval",
                {"invocation_id": invocation_id, "reason": "unknown_or_already_resolved"},
            )
            return False

        if not secrets.compare_digest(pending.token, token):
            self._bus.publish(
                "security.rejected_approval",
                {"invocation_id": invocation_id, "reason": "token_mismatch"},
            )
            return False

        if pending.future.done():
            return False

        pending.future.set_result(granted)
        self._bus.publish(
            "approval.resolved", {"invocation_id": invocation_id, "granted": granted}
        )
        return True

    def cancel_for_turn(self, invocation_ids: list[str]) -> None:
        """Auto-deny approvals belonging to an abandoned turn (FR-121)."""
        for invocation_id in invocation_ids:
            pending = self._pending.get(invocation_id)
            if pending and not pending.future.done():
                pending.future.set_result(False)

    @property
    def pending_count(self) -> int:
        return len(self._pending)


def new_invocation_id() -> str:
    return uuid.uuid4().hex


def _risk_level(decision: GateDecision, destination_known: bool) -> str:
    """Visual weight tier (DD-135).

    'high' is reserved for egress on a tainted turn to an unrecognised
    destination — the case that carries the enforced read delay. Applying it
    broadly would make the delay background noise.
    """
    if decision.reason is ApprovalReason.EGRESS_TAINTED and not destination_known:
        return "high"
    if decision.reason is ApprovalReason.EGRESS_TAINTED:
        return "elevated"
    if decision.reason is ApprovalReason.TAINT_ESCALATED:
        return "elevated"
    if decision.effective_tier >= Tier.T2_EXECUTE:
        return "elevated"
    return "low"


def _describe(spec: ToolSpec, args: dict[str, Any]) -> str:
    """Plain-language summary. Vague prompts get approved reflexively (A-1)."""
    name = spec.name
    if name == "gmail_send":
        return f"Send an email to {args.get('to', 'an unspecified recipient')}"
    if name == "shell_exec":
        return f"Run a shell command: {args.get('command', '')}"
    if name == "fs_write":
        return f"Write to {args.get('path', 'a file')}"
    if name == "fs_delete":
        return f"Delete {args.get('path', 'a file')}"
    if name in ("calendar_create", "calendar_update"):
        attendees = args.get("attendees") or []
        if attendees:
            return f"Create a calendar event and invite {', '.join(map(str, attendees))}"
        return "Create a calendar event"
    if name == "web_fetch":
        return f"Fetch {args.get('url', 'a URL')}"
    return f"Run {name}"
