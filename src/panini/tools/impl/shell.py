"""Shell execution tool — REIMPLEMENT (Solution Design §4.2).

**There is deliberately no command blocklist** (DD-127). A blocklist is trivially
bypassed — ``cur\\l``, ``$(echo curl)``, base64, a Python one-liner — and creates
false confidence in a boundary that does not exist.

The real control is that this tool is T2, so the security kernel requires
approval on *every* invocation, with the literal command shown to the user. The
approval dialog is the boundary; argument inspection is not.

Egress is ALWAYS and unconditional: no inspection can establish that a shell
command will not transmit data, because ``curl`` is one command away.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any, ClassVar

from panini.security.credentials import CredentialBundle
from panini.security.types import EgressClass, Tier
from panini.tools.base import ContentType, ParamSpec, Tool, ToolResult
from panini.tools.paths import AccessMode, PathGuard

DEFAULT_TIMEOUT_S = 30.0
MAX_OUTPUT_BYTES = 102_400

#: Stripped from the child environment so a command cannot read Panini's own
#: configuration or any secret that happens to be exported.
_ENV_DENY_PREFIXES = ("PANINI_",)
_ENV_DENY_SUBSTRINGS = ("TOKEN", "SECRET", "PASSWORD", "API_KEY", "CREDENTIAL")


def _child_env() -> dict[str, str]:
    return {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(_ENV_DENY_PREFIXES)
        and not any(s in k.upper() for s in _ENV_DENY_SUBSTRINGS)
    }


class ShellExec(Tool):
    name = "shell_exec"
    description = (
        "Run a shell command and return its output. "
        "Requires explicit user approval on every invocation."
    )
    tier = Tier.T2_EXECUTE
    egress_class = EgressClass.ALWAYS
    params: ClassVar[list[ParamSpec]] = [
        ParamSpec("command", "string", "The shell command to run."),
        ParamSpec("cwd", "string", "Working directory.", required=False),
    ]

    def __init__(
        self,
        guard: PathGuard,
        working_dir: Path,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        max_output_bytes: int = MAX_OUTPUT_BYTES,
    ) -> None:
        self._guard = guard
        self._default_cwd = working_dir
        self._timeout = timeout_s
        self._max_output = max_output_bytes
        # One shell invocation at a time: concurrent commands make the audit
        # trail ambiguous about which approval authorised which process.
        self._lock = asyncio.Lock()

    async def execute(self, args: dict[str, Any], creds: CredentialBundle) -> ToolResult:
        command = str(args.get("command", "")).strip()
        if not command:
            return ToolResult.failure("no command supplied")

        cwd = self._default_cwd
        if args.get("cwd"):
            cwd = self._guard.resolve_and_check(str(args["cwd"]), AccessMode.READ).path
        cwd.mkdir(parents=True, exist_ok=True)

        async with self._lock:
            return await self._run(command, cwd)

    async def _run(self, command: str, cwd: Path) -> ToolResult:
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                cwd=str(cwd),
                env=_child_env(),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except OSError as exc:
            return ToolResult.failure(f"could not start command: {exc}")

        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=self._timeout)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return ToolResult.failure(
                f"command exceeded {self._timeout:g}s and was terminated"
            )

        output = stdout.decode("utf-8", errors="replace")
        if len(stdout) > self._max_output:
            dropped = len(stdout) - self._max_output
            output = (
                stdout[: self._max_output].decode("utf-8", errors="ignore")
                + f"\n\n[output truncated: {dropped} more bytes]"
            )

        code = proc.returncode or 0
        body = output or "(no output)"
        if code != 0:
            return ToolResult(
                ok=False,
                content=f"Exit code {code}\n\n{body}",
                content_type=ContentType.CODE,
                error=f"exit code {code}",
                source_ref=command[:80],
            )

        return ToolResult(
            ok=True,
            content=body,
            content_type=ContentType.CODE,
            source_ref=command[:80],
        )
