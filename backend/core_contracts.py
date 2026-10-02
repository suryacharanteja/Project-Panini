"""PANINI v2 shared contracts (docs/panini-enhancements/HLD-panini-v2.md §5).

Every v2 component speaks these types: effect classes and approval levels (policy), tool specs and
results (registry and hands), and principals (who is asking, from which surface).
"""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal


class EffectClass(StrEnum):
    OBSERVE = "observe"  # read state; nothing changes anywhere
    NAVIGATE = "navigate"  # change only the view: open/switch/scroll/back, wait
    INPUT = "input"  # enter data without committing it
    LOCAL_WRITE = "local_write"  # change files or app state on this PC inside allowed roots
    LOCAL_EXEC = "local_exec"  # run a program or command on this PC (or a sandbox)
    SUBMIT = "submit"  # commit an action in a site or app
    SHARE_OUT = "share_out"  # send PANINI-held data to a third party
    COMMUNICATE = "communicate"  # reach other people: message, post, email, call
    TRANSACT = "transact"  # money or commitments
    ACCOUNT = "account"  # sign-in, settings, permissions, cookies and site storage
    DESTROY = "destroy"  # delete or otherwise irreversible removal
    INSTALL = "install"  # install software, extensions or packages
    CREDENTIAL = "credential"  # passwords, one-time codes, card numbers: never typed by the agent


RISK = {
    EffectClass.OBSERVE: 0,
    EffectClass.NAVIGATE: 1,
    EffectClass.INPUT: 1,
    EffectClass.LOCAL_WRITE: 2,
    EffectClass.LOCAL_EXEC: 3,
    EffectClass.SUBMIT: 3,
    EffectClass.SHARE_OUT: 4,
    EffectClass.COMMUNICATE: 4,
    EffectClass.ACCOUNT: 5,
    EffectClass.TRANSACT: 5,
    EffectClass.INSTALL: 5,
    EffectClass.DESTROY: 5,
    EffectClass.CREDENTIAL: 9,
}


def raise_effect(current: EffectClass, candidate: EffectClass) -> EffectClass:
    """Effects only ever go up at call time."""
    return candidate if RISK[candidate] > RISK[current] else current


class ApprovalLevel(StrEnum):
    NONE = "none"  # proceed; still receipted
    TAP = "tap"  # any authorized surface may approve
    VERIFY = "verify"  # user verification (passkey/biometric) or the PC shell
    LOCAL = "local"  # only at the PC (shell)
    DENY = "deny"  # never; the person must do it themselves


LEVEL_ORDER = [
    ApprovalLevel.NONE,
    ApprovalLevel.TAP,
    ApprovalLevel.VERIFY,
    ApprovalLevel.LOCAL,
    ApprovalLevel.DENY,
]


def stricter(a: ApprovalLevel, b: ApprovalLevel) -> ApprovalLevel:
    return a if LEVEL_ORDER.index(a) >= LEVEL_ORDER.index(b) else b


@dataclass(frozen=True)
class ToolSpec:
    name: str
    capability: str
    hand: str
    title: str
    description: str
    input_schema: dict
    effect: EffectClass
    dynamic_effect: bool = False
    group: str = "core"
    evidence: tuple[str, ...] = ()
    taints: tuple[str, ...] = ()
    idempotent: bool = False
    timeout_ms: int = 300_000
    concurrency: str | None = None
    background: bool = False
    owner_only: bool = False
    hand_from_run: bool = False
    budget_per_run: int | None = None
    requires: tuple[str, ...] = ()  # view requirements: private, foreground, top, tasks, connectors, remotes
    strict: bool = True
    meta: bool = False  # routes to another tool (use_capability, use_connector, ask_agent)

    def model_tool(self) -> dict:
        """The OpenAI Responses function tool for this spec."""
        return {
            "type": "function",
            "name": self.name,
            "description": self.description,
            "strict": self.strict,
            "parameters": self.input_schema,
        }


@dataclass
class ToolResult:
    outcome: Literal["ok", "failed", "blocked", "unknown_effect", "cancelled"] = "ok"
    content: str = ""
    data: dict | None = None
    evidence: list = field(default_factory=list)
    artifact_ids: list = field(default_factory=list)
    dispatched: bool = False
    tainted: bool = False
    error_code: str | None = None

    def model_output(self) -> dict:
        """What the model sees: the hand's structured data, or an error."""
        if self.data is not None:
            return self.data
        if self.outcome == "ok":
            return {"ok": True, "result": self.content}
        return {"error": self.content or self.outcome}


@dataclass(frozen=True)
class Principal:
    kind: Literal["owner", "persona", "external_agent", "mcp_client", "channel_user", "system"] = "owner"
    surface: str = "avatar"
    device_id: str | None = None
    client_id: str | None = None
    verified: bool = True


@dataclass(frozen=True)
class Readiness:
    state: Literal["ready", "degraded", "needs_setup", "unavailable"]
    reason: str = ""
    next_step: str = ""
