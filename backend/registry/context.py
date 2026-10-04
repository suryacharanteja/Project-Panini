"""The context a tool call runs in. V2-06 supersedes this with the run engine's RunContext; the fields
here are the subset the registry pipeline and hands need."""

from collections import Counter
from dataclasses import dataclass, field

from ..core_contracts import Principal


@dataclass(frozen=True)
class ToolView:
    capabilities: frozenset[str] | None = None  # None = every enabled capability
    surface: str = "avatar"
    background: bool = False
    private: bool = True
    depth: int = 0
    tasks: bool = True
    connectors: bool = False
    remotes: bool = False
    ledger: bool = False
    managed_browser: bool = False  # V2-08: PANINI_BROWSER_DRIVER=playwright
    claw_cdp: bool = False  # V2-09: PANINI_CLAW_CDP (full control of the attached Chrome tab)
    sandbox: bool = False  # V2-11: PANINI_SANDBOX (PANINI's own isolated computers)
    live: bool = False  # V2-12: PANINI_LIVE_VIEW (live view and takeover)
    spaces: bool = False

    def satisfies(self, requires) -> bool:
        checks = {
            "private": self.private,
            "foreground": not self.background,
            "top": self.depth == 0,
            "tasks": self.tasks,
            "connectors": self.connectors,
            "remotes": self.remotes,
            "ledger": self.ledger,
            "managed_browser": self.managed_browser,
            "claw_cdp": self.claw_cdp,
            "browser_v2": self.managed_browser or self.claw_cdp,
            "sandbox": self.sandbox,
            "live": self.live,
            "spaces": self.spaces,
        }
        return all(checks[r] for r in requires)


@dataclass
class ToolContext:
    principal: Principal = field(default_factory=Principal)
    view: ToolView = field(default_factory=ToolView)
    run_id: str | None = None
    agent: object | None = None  # v1 agent loop, when the call comes from it
    delegate: object | None = None  # async (capability, instruction) -> outcome dict
    confirm: object | None = None  # async (summary, detail) -> bool
    calls: Counter = field(default_factory=Counter)
    taint: set = field(default_factory=set)
    final: object | None = None  # a control tool's terminal AgentResult
    decision: object | None = None  # the policy decision for the current call
    preapproved: bool = False
    v1_asked: bool = False

    def check(self):
        """Stop before an effect when PANINI is paused (V2-03 grant epochs)."""
        import asyncio

        from .. import v2flags
        from ..policy import current

        if v2flags.value("PANINI_V2_POLICY") == "enforce" and current().paused():
            raise asyncio.CancelledError()
