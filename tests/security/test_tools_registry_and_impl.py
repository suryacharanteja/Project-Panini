"""Registry enforcement and the two dangerous tools.

Registration tests assert that a tool which fails to declare its security
properties cannot enter the system at all — the earliest possible point to catch
an unclassified tool.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, ClassVar

import pytest

from panini.errors import PathEscape, ToolDefinitionError, ToolError, ToolTimeout
from panini.security.credentials import CredentialBundle
from panini.security.types import EgressClass, Tier
from panini.tools.base import ParamSpec, Tool, ToolResult
from panini.tools.impl.filesystem import build_filesystem_tools
from panini.tools.impl.shell import ShellExec
from panini.tools.paths import PathGuard
from panini.tools.registry import MAX_OUTPUT_BYTES, ToolRegistry

pytestmark = pytest.mark.security

EMPTY_CREDS = CredentialBundle({})


@pytest.fixture
def layout(tmp_path: Path):
    docs = tmp_path / "docs"
    audit = tmp_path / "audit"
    synced = tmp_path / "OneDrive"
    for d in (docs, audit, synced):
        d.mkdir()
    (audit / "audit.db").write_text("evidence", encoding="utf-8")
    return {"docs": docs, "audit": audit, "synced": synced}


@pytest.fixture
def guard(layout) -> PathGuard:
    return PathGuard(
        allow_roots=[layout["docs"], layout["synced"]],
        deny_roots=[layout["audit"]],
        synced_roots=[layout["synced"]],
    )


# ── registration enforcement ────────────────────────────────────────────────

def test_tool_without_tier_cannot_register() -> None:
    class NoTier(Tool):
        name = "no_tier"
        description = "x"
        egress_class = EgressClass.NONE

        async def execute(self, args: dict[str, Any], creds: CredentialBundle) -> ToolResult:
            return ToolResult(ok=True, content="")

    with pytest.raises(ToolDefinitionError, match="tier"):
        ToolRegistry().register(NoTier())


def test_tool_without_egress_class_cannot_register() -> None:
    class NoEgress(Tool):
        name = "no_egress"
        description = "x"
        tier = Tier.T1_WRITE

        async def execute(self, args: dict[str, Any], creds: CredentialBundle) -> ToolResult:
            return ToolResult(ok=True, content="")

    with pytest.raises(ToolDefinitionError, match="egress_class"):
        ToolRegistry().register(NoEgress())


def test_conditional_with_unknown_predicate_cannot_register() -> None:
    """Catch a typo'd predicate at startup rather than as a silent over-prompt."""

    class BadPredicate(Tool):
        name = "bad_pred"
        description = "x"
        tier = Tier.T1_WRITE
        egress_class = EgressClass.CONDITIONAL
        egress_predicate = "does_not_exist"

        async def execute(self, args: dict[str, Any], creds: CredentialBundle) -> ToolResult:
            return ToolResult(ok=True, content="")

    with pytest.raises(ToolDefinitionError, match="unknown egress_predicate"):
        ToolRegistry().register(BadPredicate())


def test_duplicate_registration_is_refused(guard: PathGuard) -> None:
    reg = ToolRegistry()
    tools = build_filesystem_tools(guard)
    reg.register(tools[0])
    with pytest.raises(ToolDefinitionError, match="already registered"):
        reg.register(tools[0])


def test_enabled_map_only_reduces_capability(guard: PathGuard) -> None:
    """An entry for an unregistered tool must not create one."""
    reg = ToolRegistry()
    for t in build_filesystem_tools(guard):
        reg.register(t)
    reg.apply_enabled_map({"fs_write": False, "nonexistent_tool": True})
    assert reg.is_enabled("fs_write") is False
    assert "nonexistent_tool" not in reg.names()


# ── output cap ──────────────────────────────────────────────────────────────

async def test_output_is_capped(guard: PathGuard) -> None:
    """Unbounded output could evict the injection warning from context (DD-130)."""

    class Chatty(Tool):
        name = "chatty"
        description = "x"
        tier = Tier.T0_READ
        egress_class = EgressClass.NONE

        async def execute(self, args: dict[str, Any], creds: CredentialBundle) -> ToolResult:
            return ToolResult(ok=True, content="A" * (MAX_OUTPUT_BYTES * 2))

    reg = ToolRegistry()
    reg.register(Chatty())
    result = await reg.execute("chatty", {}, EMPTY_CREDS)
    assert len(result.content.encode()) < MAX_OUTPUT_BYTES + 200
    assert "truncated" in result.content


async def test_timeout_raises_tool_timeout() -> None:
    import asyncio

    class Slow(Tool):
        name = "slow"
        description = "x"
        tier = Tier.T0_READ
        egress_class = EgressClass.NONE
        timeout_s: ClassVar[float | None] = 0.05

        async def execute(self, args: dict[str, Any], creds: CredentialBundle) -> ToolResult:
            await asyncio.sleep(5)
            return ToolResult(ok=True, content="")

    reg = ToolRegistry()
    reg.register(Slow())
    with pytest.raises(ToolTimeout):
        await reg.execute("slow", {}, EMPTY_CREDS)


async def test_arbitrary_exception_becomes_tool_error() -> None:
    """The orchestrator must never see a raw library exception."""

    class Exploding(Tool):
        name = "boom"
        description = "x"
        tier = Tier.T0_READ
        egress_class = EgressClass.NONE

        async def execute(self, args: dict[str, Any], creds: CredentialBundle) -> ToolResult:
            raise ValueError("library internals")

    reg = ToolRegistry()
    reg.register(Exploding())
    with pytest.raises(ToolError, match="boom failed"):
        await reg.execute("boom", {}, EMPTY_CREDS)


# ── filesystem tools ────────────────────────────────────────────────────────

async def test_fs_read_within_allowed_root(guard: PathGuard, layout) -> None:
    (layout["docs"] / "a.txt").write_text("hello", encoding="utf-8")
    tools = {t.name: t for t in build_filesystem_tools(guard)}
    result = await tools["fs_read"].execute({"path": str(layout["docs"] / "a.txt")}, EMPTY_CREDS)
    assert result.ok and result.content == "hello"


async def test_fs_read_cannot_reach_audit(guard: PathGuard, layout) -> None:
    tools = {t.name: t for t in build_filesystem_tools(guard)}
    with pytest.raises(PathEscape, match="protected location"):
        await tools["fs_read"].execute({"path": str(layout["audit"] / "audit.db")}, EMPTY_CREDS)


async def test_fs_write_outside_roots_is_refused(guard: PathGuard, tmp_path: Path) -> None:
    tools = {t.name: t for t in build_filesystem_tools(guard)}
    with pytest.raises(PathEscape):
        await tools["fs_write"].execute(
            {"path": str(tmp_path / "escaped.txt"), "content": "x"}, EMPTY_CREDS
        )


async def test_fs_write_to_synced_folder_reports_it(guard: PathGuard, layout) -> None:
    tools = {t.name: t for t in build_filesystem_tools(guard)}
    result = await tools["fs_write"].execute(
        {"path": str(layout["synced"] / "note.txt"), "content": "data"}, EMPTY_CREDS
    )
    assert result.ok and "synchronised folder" in result.content


async def test_fs_delete_refuses_directories(guard: PathGuard, layout) -> None:
    """Recursive deletion has too large a blast radius for one injected argument."""
    sub = layout["docs"] / "sub"
    sub.mkdir()
    tools = {t.name: t for t in build_filesystem_tools(guard)}
    result = await tools["fs_delete"].execute({"path": str(sub)}, EMPTY_CREDS)
    assert result.ok is False and "directory" in (result.error or result.content)


def test_fs_tools_declare_correct_security_properties(guard: PathGuard) -> None:
    tools = {t.name: type(t) for t in build_filesystem_tools(guard)}
    assert tools["fs_read"].tier is Tier.T0_READ
    assert tools["fs_read"].egress_class is EgressClass.NONE
    assert tools["fs_write"].tier is Tier.T1_WRITE
    assert tools["fs_write"].egress_class is EgressClass.CONDITIONAL
    assert tools["fs_delete"].tier is Tier.T3_DESTRUCTIVE


# ── shell tool ──────────────────────────────────────────────────────────────

def test_shell_is_t2_and_always_egress(guard: PathGuard, layout) -> None:
    """T2 means the kernel prompts on every call; ALWAYS means curl is assumed."""
    tool = ShellExec(guard, layout["docs"])
    assert type(tool).tier is Tier.T2_EXECUTE
    assert type(tool).egress_class is EgressClass.ALWAYS


def test_shell_has_no_blocklist(guard: PathGuard, layout) -> None:
    """DD-127 — a blocklist would be bypassable and give false confidence."""
    tool = ShellExec(guard, layout["docs"])
    for attr in ("BLOCKED_COMMANDS", "DENYLIST", "_blocklist", "FORBIDDEN"):
        assert not hasattr(tool, attr)


async def test_shell_runs_and_captures_output(guard: PathGuard, layout) -> None:
    tool = ShellExec(guard, layout["docs"])
    cmd = "echo hello" if os.name != "nt" else "echo hello"
    result = await tool.execute({"command": cmd}, EMPTY_CREDS)
    assert result.ok and "hello" in result.content


async def test_shell_nonzero_exit_is_reported_not_raised(guard: PathGuard, layout) -> None:
    tool = ShellExec(guard, layout["docs"])
    result = await tool.execute({"command": "exit 3"}, EMPTY_CREDS)
    assert result.ok is False and "3" in (result.error or "")


async def test_shell_timeout_terminates(guard: PathGuard, layout) -> None:
    """A long-running command is killed rather than hanging the assistant.

    Uses the running interpreter to sleep: shell built-ins vary, and on Windows
    a GNU `timeout` on PATH shadows cmd's, exiting immediately and never
    exercising the timeout path.
    """
    import sys

    tool = ShellExec(guard, layout["docs"], timeout_s=0.3)
    cmd = f'"{sys.executable}" -c "import time; time.sleep(5)"'
    result = await tool.execute({"command": cmd}, EMPTY_CREDS)
    assert result.ok is False
    assert "exceeded" in (result.error or "")


async def test_shell_cwd_is_confined(guard: PathGuard, layout, tmp_path: Path) -> None:
    tool = ShellExec(guard, layout["docs"])
    with pytest.raises(PathEscape):
        await tool.execute({"command": "echo x", "cwd": str(tmp_path / "elsewhere")}, EMPTY_CREDS)


def test_shell_environment_strips_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    from panini.tools.impl.shell import _child_env

    monkeypatch.setenv("PANINI_INTERNAL", "x")
    monkeypatch.setenv("MY_API_KEY", "sk-secret")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_secret")
    monkeypatch.setenv("HARMLESS", "ok")

    env = _child_env()
    assert "PANINI_INTERNAL" not in env
    assert "MY_API_KEY" not in env
    assert "GITHUB_TOKEN" not in env
    assert env.get("HARMLESS") == "ok"
