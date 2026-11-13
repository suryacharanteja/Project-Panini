"""LLMProvider interface (DD-040).

Providers are swappable via config. ``is_local`` is not cosmetic — it drives the
provider indicator and the acknowledgement required before content leaves the
machine (DD-113, FR-550..552).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class Message:
    role: str  # system | user | assistant | tool
    content: str
    tool_call_id: str | None = None
    name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_call_id:
            d["tool_call_id"] = self.tool_call_id
        if self.name:
            d["name"] = self.name
        return d


@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str
    name: str
    args: dict[str, Any]


@dataclass(frozen=True, slots=True)
class Chunk:
    """One increment of a streamed response."""

    delta: str = ""
    tool_call: ToolCall | None = None
    done: bool = False
    finish_reason: str | None = None


@dataclass(frozen=True, slots=True)
class Capabilities:
    supports_tools: bool
    max_context: int
    is_local: bool
    model: str


@dataclass(frozen=True, slots=True)
class HealthStatus:
    available: bool
    detail: str = ""
    models: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class GenerateOptions:
    temperature: float = 0.7
    max_tokens: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class LLMProvider(ABC):
    """Streaming chat provider with optional tool calling."""

    name: str

    @abstractmethod
    def generate(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        options: GenerateOptions | None = None,
    ) -> AsyncIterator[Chunk]: ...

    @abstractmethod
    async def health(self) -> HealthStatus: ...

    @property
    @abstractmethod
    def capabilities(self) -> Capabilities: ...

    async def aclose(self) -> None:
        """Release transport resources. Safe to call more than once."""
        return None
