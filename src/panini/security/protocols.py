"""Structural interfaces the security kernel depends on.

Declared as Protocols so ``security`` imports nothing from ``core`` or ``tools``.
The dependency arrow points one way: everything depends on the kernel, the
kernel depends on nobody (HLD §1.3).
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from panini.security.types import Provenance, ToolSpec


@runtime_checkable
class TurnLike(Protocol):
    """The subset of Turn the kernel needs.

    Only ``add_context`` mutates taint, and only the concrete Turn implements it —
    the kernel calls it but does not own the state.
    """

    id: str
    session_id: str

    @property
    def tainted(self) -> bool: ...

    @property
    def taint_sources(self) -> list[str]: ...

    def add_context(self, content: str, provenance: Provenance, source: str) -> None: ...


@runtime_checkable
class ToolResultLike(Protocol):
    ok: bool
    content: str
    source_ref: str | None
    error: str | None


@runtime_checkable
class ToolRegistryLike(Protocol):
    """Tool lookup and execution, as the gateway sees it."""

    def names(self) -> list[str]: ...

    def spec(self, name: str) -> ToolSpec: ...

    def is_enabled(self, name: str) -> bool: ...

    def schemas(self, enabled_only: bool = True) -> list[dict[str, Any]]:
        """Full model-facing schemas, including parameters.

        The gateway forwards these rather than reconstructing them from
        ToolSpec: a spec carries security metadata only, and a model given bare
        tool names has to guess argument shapes.
        """
        ...

    async def execute(
        self, name: str, args: dict[str, Any], creds: dict[str, str]
    ) -> ToolResultLike: ...
