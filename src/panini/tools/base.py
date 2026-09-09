"""Tool interface and result type.

Security properties are declared as class attributes and validated at
registration. A tool that omits its tier or egress class cannot register, so by
the time the gateway sees a ToolSpec it is always fully populated
(Tools LLD §2.1).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, ClassVar

from panini.security.credentials import CredentialBundle
from panini.security.types import EgressClass, Tier, ToolSpec


class ContentType(StrEnum):
    """How the renderer should present a result (DD-081)."""

    TEXT = "text"
    CODE = "code"
    IMAGE = "image"
    VIDEO = "video"
    DATA = "data"
    DIAGRAM = "diagram"
    CHART = "chart"
    ACTION = "action"


@dataclass(frozen=True, slots=True)
class ToolResult:
    ok: bool
    content: str
    content_type: ContentType = ContentType.TEXT
    source_ref: str | None = None
    error: str | None = None
    ui_payload: dict[str, Any] | None = None

    @classmethod
    def failure(cls, error: str, source_ref: str | None = None) -> ToolResult:
        """A failed result still carries content.

        The error text reaches the model, so it is treated as untrusted like any
        other tool output — the gateway taints on it regardless of ``ok``.
        """
        return cls(ok=False, content=f"Error: {error}", error=error, source_ref=source_ref)


@dataclass(frozen=True, slots=True)
class ToolHealth:
    ok: bool
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class ParamSpec:
    """One tool parameter, as presented to the model."""

    name: str
    type: str
    description: str
    required: bool = True
    enum: list[str] | None = None


class Tool(ABC):
    """Base class for every tool.

    Subclasses must declare ``name``, ``tier`` and ``egress_class``. Omitting
    either is a registration failure rather than a silent default, because a
    permissive default would be indistinguishable from a deliberate choice.
    """

    name: ClassVar[str]
    description: ClassVar[str]
    params: ClassVar[list[ParamSpec]] = []

    tier: ClassVar[Tier]
    egress_class: ClassVar[EgressClass]
    egress_predicate: ClassVar[str | None] = None
    credential_handles: ClassVar[tuple[str, ...]] = ()

    #: Set when ``execute`` performs blocking I/O; the registry moves it off the
    #: event loop so audio and SSE delivery are not stalled (HR-02).
    blocking: ClassVar[bool] = False

    #: Per-tool override of the registry default.
    timeout_s: ClassVar[float | None] = None

    @abstractmethod
    async def execute(self, args: dict[str, Any], creds: CredentialBundle) -> ToolResult: ...

    async def health(self) -> ToolHealth:
        """Whether the tool is configured and usable.

        An unhealthy tool is not offered to the model, so a missing credential
        surfaces at startup rather than mid-conversation (FR-426).
        """
        return ToolHealth(ok=True)

    # ── derived ──────────────────────────────────────────────────────────────

    @classmethod
    def spec(cls) -> ToolSpec:
        return ToolSpec(
            name=cls.name,
            tier=cls.tier,
            egress_class=cls.egress_class,
            egress_predicate=cls.egress_predicate,
            credential_handles=cls.credential_handles,
        )

    def schema(self) -> dict[str, Any]:
        """JSON-schema fragment describing this tool to the model.

        An instance method rather than a classmethod so a tool can describe its
        *runtime* constraints — a filesystem tool that does not tell the model
        which directories are reachable produces avoidable failures and wasted
        approval prompts.
        """
        properties: dict[str, Any] = {}
        required: list[str] = []
        for p in self.params:
            entry: dict[str, Any] = {"type": p.type, "description": p.description}
            if p.enum:
                entry["enum"] = p.enum
            properties[p.name] = entry
            if p.required:
                required.append(p.name)
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.describe(),
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            },
        }

    def describe(self) -> str:
        """Description shown to the model. Override to add runtime constraints."""
        return self.description


@dataclass(slots=True)
class ToolRegistration:
    tool: Tool
    enabled: bool = True
    health: ToolHealth = field(default_factory=lambda: ToolHealth(ok=True))
