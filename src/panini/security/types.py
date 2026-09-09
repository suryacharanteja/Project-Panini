"""Core security value types.

These encode the distinction the whole content-initiated security model rests
on: *tier* answers "how damaging locally?", *egress* answers "can this send my
data out?". They are orthogonal — treating them as one is the failure mode that
lets exfiltration hide inside a read operation (threat T-07).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import IntEnum, StrEnum


class Provenance(StrEnum):
    """Origin of a piece of context. Determines whether it may be obeyed.

    Instructions come only from USER and SYSTEM. RETRIEVED content is data,
    whatever it claims about itself (DD-110).
    """

    USER = "user"
    SYSTEM = "system"
    RETRIEVED = "retrieved"


class Tier(IntEnum):
    """Permission tier — how damaging the action is locally (DD-060..064)."""

    T0_READ = 0
    T1_WRITE = 1
    T2_EXECUTE = 2
    T3_DESTRUCTIVE = 3


class EgressClass(StrEnum):
    """Whether a tool can move data off the machine (DD-112).

    UNKNOWN is deliberately not a "don't know yet" placeholder — it is treated
    as ALWAYS. A tool cannot accidentally be non-egress (Tools LLD §4.6).
    """

    NONE = "none"
    ALWAYS = "always"
    CONDITIONAL = "conditional"
    UNKNOWN = "unknown"

    def is_definitely_safe(self) -> bool:
        return self is EgressClass.NONE


class ApprovalReason(StrEnum):
    """Why an approval prompt is being shown.

    Surfaced in the dialog so the user knows whether this is routine or the
    consequence of the assistant having read untrusted content (FR-514).
    """

    TIER = "tier"
    TAINT_ESCALATED = "taint_escalated"
    EGRESS_TAINTED = "egress_tainted"
    UNKNOWN_DEST = "unknown_destination"


class Outcome(StrEnum):
    """Terminal state of a tool invocation, as recorded in the audit log."""

    PENDING = "pending"
    OK = "ok"
    DENIED = "denied"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """Security-relevant declaration for a tool.

    Registration fails if tier or egress class are absent, so this is always
    fully populated by the time the gateway sees it (Tools LLD §2.1).
    """

    name: str
    tier: Tier
    egress_class: EgressClass
    egress_predicate: str | None = None
    credential_handles: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.egress_class is EgressClass.CONDITIONAL and not self.egress_predicate:
            raise ValueError(
                f"Tool {self.name!r}: egress_class=CONDITIONAL requires an egress_predicate"
            )


@dataclass(frozen=True, slots=True)
class EgressEval:
    """Result of classifying one invocation's egress behaviour."""

    is_egress: bool
    destination: str | None = None
    payload_preview: str | None = None

    @classmethod
    def none(cls) -> EgressEval:
        return cls(is_egress=False)


@dataclass(frozen=True, slots=True)
class GateDecision:
    """The security kernel's ruling on one invocation.

    Both declared and effective tier are retained so the audit log shows *why*
    a prompt appeared, not merely that it did (Security Kernel LLD §6.1).
    """

    declared_tier: Tier
    effective_tier: Tier
    approval_required: bool
    reason: ApprovalReason | None
    is_egress: bool
    destination: str | None = None
    payload_preview: str | None = None

    @property
    def was_escalated(self) -> bool:
        return self.effective_tier > self.declared_tier


# Signature for conditional egress predicates. Kept loose here to avoid a
# circular import on Turn; the concrete registry lives in egress.py.
EgressPredicate = Callable[[dict[str, object], bool], EgressEval]


@dataclass(frozen=True, slots=True)
class ContextElement:
    """One piece of content in a turn's context, with its origin.

    Appending an element whose provenance is RETRIEVED is what taints a turn.
    """

    content: str
    provenance: Provenance
    source: str
    added_at: str

    @property
    def is_trusted(self) -> bool:
        return self.provenance is not Provenance.RETRIEVED


@dataclass(slots=True)
class ToolInvocation:
    """Record of one tool call within a turn."""

    invocation_id: str
    tool_name: str
    args: dict[str, object]
    decision: GateDecision
    audit_id: int | None = None
    approval_granted: bool | None = None
    outcome: Outcome = Outcome.PENDING
    error: str | None = None
    _meta: dict[str, object] = field(default_factory=dict)
