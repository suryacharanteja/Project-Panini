"""Tool registry.

⚠️ This module must never be imported by ``panini.core`` or ``panini.api``.
A ruff banned-api rule fails the build if it is — the orchestrator receives only
``security.ToolGateway``, so there is no code path to a tool that skips the
security kernel (HLD §1.3).

Registration validates security declarations. Execution enforces timeouts,
output caps, and exception isolation.
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any

from panini.errors import ToolDefinitionError, ToolError, ToolNotAvailable, ToolTimeout
from panini.security.credentials import CredentialBundle
from panini.security.types import EgressClass, ToolSpec
from panini.tools.base import Tool, ToolHealth, ToolRegistration, ToolResult

DEFAULT_TIMEOUT_S = 30.0

#: Tool output is capped because an unbounded result could force context
#: eviction — and what gets evicted first is the system prompt's injection
#: warning. This is a security control, not hygiene (DD-130).
MAX_OUTPUT_BYTES = 102_400

#: Predicate names the egress classifier knows about. A CONDITIONAL tool naming
#: anything else would fail closed at classification time, but catching it at
#: registration turns a silent over-prompt into an obvious startup error.
KNOWN_EGRESS_PREDICATES = frozenset({
    "fs_write_synced",
    "query_carries_content",
    "calendar_has_attendees",
})


class ToolRegistry:
    """Holds tools, validates their declarations, and executes them."""

    def __init__(self, default_timeout_s: float = DEFAULT_TIMEOUT_S) -> None:
        self._tools: dict[str, ToolRegistration] = {}
        self._default_timeout = default_timeout_s

    # ── registration ─────────────────────────────────────────────────────────

    def register(self, tool: Tool) -> None:
        self._validate(tool)
        if tool.name in self._tools:
            raise ToolDefinitionError(f"tool {tool.name!r} is already registered")
        self._tools[tool.name] = ToolRegistration(tool=tool)

    @staticmethod
    def _validate(tool: Tool) -> None:
        cls = type(tool)

        for attr in ("name", "description", "tier", "egress_class"):
            if getattr(cls, attr, None) is None:
                raise ToolDefinitionError(
                    f"{cls.__name__} must declare {attr!r}. Security properties have "
                    f"no defaults: a permissive default is indistinguishable from a "
                    f"deliberate choice."
                )

        if cls.egress_class is EgressClass.CONDITIONAL:
            if not cls.egress_predicate:
                raise ToolDefinitionError(
                    f"{cls.__name__}: egress_class=CONDITIONAL requires an egress_predicate"
                )
            if cls.egress_predicate not in KNOWN_EGRESS_PREDICATES:
                raise ToolDefinitionError(
                    f"{cls.__name__}: unknown egress_predicate "
                    f"{cls.egress_predicate!r}. Known: {sorted(KNOWN_EGRESS_PREDICATES)}"
                )

        if not inspect.iscoroutinefunction(cls.execute):
            raise ToolDefinitionError(f"{cls.__name__}.execute must be async")

    # ── lookup ───────────────────────────────────────────────────────────────

    def names(self) -> list[str]:
        return sorted(self._tools)

    def spec(self, name: str) -> ToolSpec:
        reg = self._tools.get(name)
        if reg is None:
            raise ToolNotAvailable(f"unknown tool {name!r}")
        return type(reg.tool).spec()

    def is_enabled(self, name: str) -> bool:
        reg = self._tools.get(name)
        return bool(reg and reg.enabled and reg.health.ok)

    def set_enabled(self, name: str, enabled: bool) -> None:
        reg = self._tools.get(name)
        if reg is None:
            raise ToolNotAvailable(f"unknown tool {name!r}")
        reg.enabled = enabled

    def apply_enabled_map(self, enabled: dict[str, bool]) -> None:
        """Apply the config's tools.enabled map.

        Only reduces capability — an entry for an unregistered tool is ignored
        rather than creating one.
        """
        for name, flag in enabled.items():
            if name in self._tools:
                self._tools[name].enabled = flag

    def schemas(self, enabled_only: bool = True) -> list[dict[str, Any]]:
        return [
            reg.tool.schema()
            for name, reg in sorted(self._tools.items())
            if not enabled_only or self.is_enabled(name)
        ]

    async def check_health(self) -> dict[str, ToolHealth]:
        results: dict[str, ToolHealth] = {}
        for name, reg in self._tools.items():
            try:
                reg.health = await reg.tool.health()
            except Exception as exc:  # noqa: BLE001 — a broken health check disables, never crashes
                reg.health = ToolHealth(ok=False, detail=str(exc))
            results[name] = reg.health
        return results

    # ── execution ────────────────────────────────────────────────────────────

    async def execute(
        self, name: str, args: dict[str, Any], creds: CredentialBundle
    ) -> ToolResult:
        """Run a tool. Reached only via ToolGateway.

        Wraps every failure mode so the orchestrator sees ToolError rather than
        an arbitrary library exception it cannot reason about.
        """
        reg = self._tools.get(name)
        if reg is None:
            raise ToolNotAvailable(f"unknown tool {name!r}")

        tool = reg.tool
        timeout = type(tool).timeout_s or self._default_timeout

        try:
            if type(tool).blocking:
                # CPU- or IO-blocking tools must not run on the event loop, or
                # audio playback and SSE delivery stall behind them (HR-02).
                result = await asyncio.wait_for(
                    asyncio.to_thread(_run_sync, tool, args, creds), timeout=timeout
                )
            else:
                result = await asyncio.wait_for(tool.execute(args, creds), timeout=timeout)
        except TimeoutError as exc:
            raise ToolTimeout(f"{name} exceeded {timeout:g}s") from exc
        except (ToolError, ToolNotAvailable):
            raise
        except Exception as exc:
            raise ToolError(f"{name} failed: {exc}") from exc

        return _cap_output(result)


def _run_sync(tool: Tool, args: dict[str, Any], creds: CredentialBundle) -> ToolResult:
    """Bridge for blocking tools whose execute is async but does blocking work."""
    return asyncio.run(tool.execute(args, creds))


def _cap_output(result: ToolResult) -> ToolResult:
    encoded = result.content.encode("utf-8", errors="replace")
    if len(encoded) <= MAX_OUTPUT_BYTES:
        return result
    truncated = encoded[:MAX_OUTPUT_BYTES].decode("utf-8", errors="ignore")
    dropped = len(encoded) - MAX_OUTPUT_BYTES
    return ToolResult(
        ok=result.ok,
        content=f"{truncated}\n\n[output truncated: {dropped} more bytes]",
        content_type=result.content_type,
        source_ref=result.source_ref,
        error=result.error,
        ui_payload=result.ui_payload,
    )
