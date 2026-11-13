"""Filesystem tools — REIMPLEMENT (Solution Design §4.2).

Written against Panini's path rules rather than adapted from upstream, because
these two plus the shell tool are the most dangerous surface in the system and
must not inherit unreviewed behaviour.

Four separate tools rather than one with a ``mode`` argument, so tier and egress
differ correctly per operation: a read is T0/NONE, a write is T1/CONDITIONAL,
a delete is T3.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, ClassVar

from panini.security.credentials import CredentialBundle
from panini.security.types import EgressClass, Tier
from panini.tools.base import ContentType, ParamSpec, Tool, ToolResult
from panini.tools.paths import AccessMode, PathGuard

MAX_READ_BYTES = 1_048_576
MAX_LIST_ENTRIES = 500


class _FsTool(Tool):
    """Shared guard wiring."""

    blocking: ClassVar[bool] = True

    def __init__(self, guard: PathGuard, max_read_bytes: int = MAX_READ_BYTES) -> None:
        self._guard = guard
        self._max_read = max_read_bytes

    def describe(self) -> str:
        """Include the reachable directories.

        Without this the model guesses a path, the guard refuses it, and the
        user is prompted for an action that was never going to succeed.
        """
        roots = ", ".join(str(r) for r in self._guard.allow_roots)
        return f"{self.description} Accessible directories: {roots}. Paths outside these are refused."


class FsRead(_FsTool):
    name = "fs_read"
    description = "Read the contents of a text file."
    tier = Tier.T0_READ
    egress_class = EgressClass.NONE
    params: ClassVar[list[ParamSpec]] = [
        ParamSpec("path", "string", "Path to the file to read.")
    ]

    async def execute(self, args: dict[str, Any], creds: CredentialBundle) -> ToolResult:
        rp = self._guard.resolve_and_check(str(args["path"]), AccessMode.READ)
        if not rp.path.is_file():
            return ToolResult.failure(f"{rp.path.name} is not a file")

        size = rp.path.stat().st_size
        data = rp.path.read_text(encoding="utf-8", errors="replace")

        if size > self._max_read:
            # Head and tail rather than a flat truncation: the end of a file is
            # often the most relevant part (logs, appended notes).
            half = self._max_read // 2
            data = (
                f"{data[:half]}\n\n[… {size - self._max_read} bytes omitted …]\n\n{data[-half:]}"
            )

        return ToolResult(
            ok=True, content=data, content_type=ContentType.TEXT, source_ref=str(rp.path)
        )


class FsList(_FsTool):
    name = "fs_list"
    description = "List the entries in a directory."
    tier = Tier.T0_READ
    egress_class = EgressClass.NONE
    params: ClassVar[list[ParamSpec]] = [
        ParamSpec("path", "string", "Directory to list.")
    ]

    async def execute(self, args: dict[str, Any], creds: CredentialBundle) -> ToolResult:
        rp = self._guard.resolve_and_check(str(args["path"]), AccessMode.READ)
        if not rp.path.is_dir():
            return ToolResult.failure(f"{rp.path.name} is not a directory")

        entries: list[dict[str, Any]] = []
        for i, child in enumerate(sorted(rp.path.iterdir())):
            if i >= MAX_LIST_ENTRIES:
                break
            try:
                stat = child.stat()
                entries.append({
                    "name": child.name,
                    "type": "dir" if child.is_dir() else "file",
                    "size": stat.st_size,
                })
            except OSError:
                entries.append({"name": child.name, "type": "unreadable", "size": None})

        return ToolResult(
            ok=True,
            content=json.dumps(entries, indent=2),
            content_type=ContentType.DATA,
            source_ref=str(rp.path),
            ui_payload={"entries": entries},
        )


class FsWrite(_FsTool):
    name = "fs_write"
    description = "Write text to a file, creating it if necessary."
    tier = Tier.T1_WRITE
    # A write into a synchronised folder is exfiltration wearing a local costume,
    # so egress depends on where the path resolves to (Tools LLD §5).
    egress_class = EgressClass.CONDITIONAL
    egress_predicate = "fs_write_synced"
    params: ClassVar[list[ParamSpec]] = [
        ParamSpec("path", "string", "Path to write to."),
        ParamSpec("content", "string", "Text content to write."),
        ParamSpec("append", "boolean", "Append instead of overwriting.", required=False),
    ]

    async def execute(self, args: dict[str, Any], creds: CredentialBundle) -> ToolResult:
        rp = self._guard.resolve_and_check(str(args["path"]), AccessMode.WRITE)
        content = str(args.get("content", ""))
        append = bool(args.get("append", False))

        rp.path.parent.mkdir(parents=True, exist_ok=True)
        with rp.path.open("a" if append else "w", encoding="utf-8") as fh:
            fh.write(content)

        verb = "Appended" if append else "Wrote"
        note = " (synchronised folder)" if rp.is_synced else ""
        return ToolResult(
            ok=True,
            content=f"{verb} {len(content)} characters to {rp.path}{note}",
            source_ref=str(rp.path),
        )


class FsDelete(_FsTool):
    name = "fs_delete"
    description = "Delete a file."
    tier = Tier.T3_DESTRUCTIVE
    egress_class = EgressClass.NONE
    params: ClassVar[list[ParamSpec]] = [
        ParamSpec("path", "string", "Path of the file to delete.")
    ]

    async def execute(self, args: dict[str, Any], creds: CredentialBundle) -> ToolResult:
        rp = self._guard.resolve_and_check(str(args["path"]), AccessMode.DELETE)
        if not rp.path.exists():
            return ToolResult.failure(f"{rp.path} does not exist")
        if rp.path.is_dir():
            # Recursive directory deletion is not offered: the blast radius of a
            # mistaken or injected argument is too large for a single call.
            return ToolResult.failure(
                f"{rp.path.name} is a directory; directory deletion is not supported"
            )
        rp.path.unlink()
        return ToolResult(ok=True, content=f"Deleted {rp.path}", source_ref=str(rp.path))


def build_filesystem_tools(guard: PathGuard, max_read_bytes: int = MAX_READ_BYTES) -> list[Tool]:
    return [
        FsRead(guard, max_read_bytes),
        FsList(guard, max_read_bytes),
        FsWrite(guard, max_read_bytes),
        FsDelete(guard, max_read_bytes),
    ]


__all__ = ["FsDelete", "FsList", "FsRead", "FsWrite", "build_filesystem_tools", "Path"]
