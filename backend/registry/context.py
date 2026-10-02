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

    def satisfies(self, requires) -> bool:
        checks = {
            "private": self.private,
            "foreground": not self.background,
            "top": self.depth == 0,
            "tasks": self.tasks,
            "connectors": self.connectors,
            "remotes": self.remotes,
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

    def check(self):
        """Raise to stop before an effect (V2-03 adds grant epochs here)."""
