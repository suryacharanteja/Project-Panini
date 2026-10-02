"""Playwright MCP sessions (V2-08): one Playwright MCP server process per browser profile, spawned from the
`playwright-core` that ships with PANINI (no browser download: the installed Edge or Chrome is used).

The process environment is an allow-list (every PANINI and provider secret is stripped). A session refuses to
start when the server's tool catalogue differs from the pinned one. A crashed session fails the call in flight
(`unknown_effect` when it may have acted) and restarts on the next call; three restarts in ten minutes make it
unavailable.
"""

import asyncio
import hashlib
import json
import logging
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..config import ROOT  # The runtime root when packaged (PANINI_RUNTIME_ROOT), else the repository.
from ..harness.mcp_stdio import McpError, McpStdio

logger = logging.getLogger(__name__)

SIDECAR = ROOT / "panini-core/capabilities/browser/playwright"
VERSION = "1.63.0"
CATALOGUE = SIDECAR / f"tools-{VERSION}.json"
ENV_ALLOW = {
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "WINDIR",
    "PATH",
    "PATHEXT",
    "TEMP",
    "TMP",
    "LOCALAPPDATA",
    "APPDATA",
    "USERPROFILE",
    "HOMEDRIVE",
    "HOMEPATH",
    "PROGRAMFILES",
    "PROGRAMFILES(X86)",
    "PROGRAMW6432",
    "PROGRAMDATA",
    "COMPUTERNAME",
    "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE",
    "OS",
    "COMSPEC",
}
MAX_RESTARTS, RESTART_WINDOW_S = 3, 600


class BrowserDriverError(RuntimeError):
    pass


class SessionCrashed(BrowserDriverError):
    """The driver process died during a call; whether the action happened is unknown."""


def node_executable() -> str | None:
    packaged = ROOT / "node" / ("node.exe" if os.name == "nt" else "node")
    if packaged.exists():
        return str(packaged)
    return shutil.which("node")


def entry_script() -> Path:
    """`playwright-core/lib/entry/mcp.js`, resolved like Node would from the sidecar folder."""
    for folder in (SIDECAR, *SIDECAR.parents):
        candidate = folder / "node_modules/playwright-core/lib/entry/mcp.js"
        if candidate.exists():
            return candidate
    raise BrowserDriverError("The browser driver (playwright-core) is not installed.")


def clean_env(extra: dict | None = None) -> dict:
    env = {k: v for k, v in os.environ.items() if k.upper() in ENV_ALLOW}
    env.pop("PLAYWRIGHT_BROWSERS_PATH", None)
    env["NODE_OPTIONS"] = ""
    env.update(extra or {})
    return env


def schema_digest(schema) -> str:
    return hashlib.sha256(json.dumps(schema, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def load_catalogue(path: Path = CATALOGUE) -> dict[str, str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {tool["name"]: tool["schema_sha256"] for tool in data["tools"]}


@dataclass
class Preset:
    key: str
    flags: list[str] = field(default_factory=list)
    effect_tools: frozenset[str] = frozenset()  # Tools whose interrupted call may already have acted.

    def args(self) -> list[str]:
        return list(self.flags)


def managed_preset(data_dir: Path, artifacts: Path, *, browser="msedge", headless=False) -> Preset:
    profile = data_dir / "browser" / "managed"
    profile.mkdir(parents=True, exist_ok=True)
    out = artifacts / "playwright" / "managed"
    out.mkdir(parents=True, exist_ok=True)
    flags = [
        "--browser",
        browser,
        "--user-data-dir",
        str(profile),
        "--caps",
        "pdf,devtools,vision,testing",  # Not storage/network: cookie and storage tools are credential-adjacent.
        "--output-dir",
        str(out),
        "--image-responses",
        "omit",
        "--codegen",
        "none",
        "--init-script",
        str(SIDECAR / "init-overlay.js"),
        "--timeout-action",
        "8000",
        "--timeout-navigation",
        "30000",
    ]
    if headless:
        flags.append("--headless")
    return Preset("managed", flags, EFFECT_TOOLS)


def isolated_preset(run_id: str, artifacts: Path, *, browser="msedge") -> Preset:
    from .net_guard import PRIVATE_ORIGINS

    out = artifacts / "playwright" / "isolated" / run_id
    out.mkdir(parents=True, exist_ok=True)
    flags = [
        "--browser",
        browser,
        "--isolated",
        "--headless",
        "--block-service-workers",
        "--blocked-origins",
        PRIVATE_ORIGINS,
        "--caps",
        "pdf",
        "--output-dir",
        str(out),
        "--image-responses",
        "omit",
        "--codegen",
        "none",
    ]
    return Preset(f"isolated:{run_id}", flags, EFFECT_TOOLS)


def chrome_preset(relay, artifacts: Path, *, caps="pdf,devtools,testing") -> Preset:
    """Your Chrome through JevRev Claw (V2-09): Playwright connects to PANINI's scoped CDP relay."""
    out = artifacts / "playwright" / "chrome"
    out.mkdir(parents=True, exist_ok=True)
    flags = [
        "--cdp-endpoint",
        relay.endpoint,
        "--cdp-header",
        relay.header,
        "--cdp-timeout",
        "15000",
        "--caps",
        caps,
        "--output-dir",
        str(out),
        "--image-responses",
        "omit",
        "--codegen",
        "none",
        "--timeout-action",
        "8000",
    ]
    return Preset(f"chrome:{relay.token[:12]}", flags, EFFECT_TOOLS)


# Playwright tools that can change something; a crash during one of them is an unknown effect.
EFFECT_TOOLS = frozenset(
    {
        "browser_click",
        "browser_type",
        "browser_fill_form",
        "browser_select_option",
        "browser_press_key",
        "browser_drag",
        "browser_file_upload",
        "browser_handle_dialog",
        "browser_evaluate",
        "browser_run_code",
        "browser_mouse_click_xy",
        "browser_mouse_drag_xy",
    }
)


class PlaywrightSession:
    """One Playwright MCP server process bound to one browser profile."""

    def __init__(
        self, preset: Preset, *, command=None, args=None, env=None, catalogue=None, clock=time.monotonic
    ):
        self.preset, self.clock = preset, clock
        self.command = command
        self.base_args = args
        self.env = env
        self.catalogue = catalogue
        self.client: McpStdio | None = None
        self.tools: dict[str, dict] = {}
        self.lock = asyncio.Lock()
        self.restarts: list[float] = []
        self.started_once = False
        self.last_used = clock()
        self.reason = ""

    @property
    def healthy(self) -> bool:
        return self.client is not None and self.client.alive

    @property
    def available(self) -> bool:
        recent = [t for t in self.restarts if self.clock() - t < RESTART_WINDOW_S]
        return len(recent) < MAX_RESTARTS

    def _command(self):
        if self.command is not None:
            return self.command, list(self.base_args or []) + self.preset.args()
        node = node_executable()
        if node is None:
            raise BrowserDriverError("Node.js is not available for the browser driver.")
        return node, [str(entry_script()), *self.preset.args()]

    async def start(self):
        if self.healthy:
            return
        if not self.available:
            self.reason = "The browser driver kept stopping; it is paused for a few minutes."
            raise BrowserDriverError(self.reason)
        if self.started_once:
            self.restarts.append(self.clock())
        command, args = self._command()
        client = McpStdio(f"Browser driver ({self.preset.key})", BrowserDriverError)
        try:
            await client.spawn(command, args, env=self.env if self.env is not None else clean_env())
            await client.initialize(timeout=60)
            listed = await client.request("tools/list", {}, timeout=60)
        except (OSError, McpError, BrowserDriverError) as exc:
            await client.close(grace_s=1)
            self.reason = f"The browser driver could not start: {exc}"
            raise BrowserDriverError(self.reason) from None
        tools = {t["name"]: t for t in (listed or {}).get("tools", [])}
        mismatch = self._verify(tools)
        if mismatch:
            await client.close(grace_s=1)
            self.reason = "Browser driver version mismatch: " + mismatch
            raise BrowserDriverError(self.reason)
        self.client, self.tools, self.started_once, self.reason = client, tools, True, ""

    def _verify(self, tools: dict) -> str:
        pinned = self.catalogue if self.catalogue is not None else load_catalogue()
        for name, tool in tools.items():
            expected = pinned.get(name)
            if expected is None:
                return f"unexpected tool {name}"
            if expected != schema_digest(tool.get("inputSchema", {})):
                return f"tool {name} changed"
        return ""

    async def call(self, tool: str, args: dict, *, timeout_s: float = 60) -> dict:
        async with self.lock:
            await self.start()
            if tool not in self.tools:
                raise BrowserDriverError(f"The browser driver has no tool {tool}.")
            self.last_used = self.clock()
            try:
                return await self.client.request(
                    "tools/call", {"name": tool, "arguments": args}, timeout=timeout_s
                )
            except BrowserDriverError as exc:
                crashed = await self._exited()
                if crashed:
                    await self.client.close(grace_s=0.5)
                if crashed and tool in self.preset.effect_tools:
                    raise SessionCrashed(f"The browser driver stopped during {tool}.") from exc
                raise

    async def _exited(self, wait_s: float = 1.0) -> bool:
        """Whether the driver process ended (a lost pipe is noticed before the exit code is)."""
        process = self.client.process if self.client else None
        if process is None:
            return True
        try:
            await asyncio.wait_for(process.wait(), wait_s)
        except TimeoutError:
            return False
        return True

    async def close(self):
        if self.client is None:
            return
        if self.client.alive:
            try:
                await self.client.request("tools/call", {"name": "browser_close", "arguments": {}}, timeout=5)
            except BrowserDriverError:
                pass
        await self.client.close(grace_s=5)
        self.client = None


class PlaywrightPool:
    def __init__(self, factory=PlaywrightSession, clock=time.monotonic):
        self.factory, self.clock = factory, clock
        self.sessions: dict[str, PlaywrightSession] = {}

    def session(self, preset: Preset, **options) -> PlaywrightSession:
        current = self.sessions.get(preset.key)
        if current is None:
            current = self.sessions[preset.key] = self.factory(preset, **options)
        return current

    async def close_idle(self, idle_s: float = 600, chrome_idle_s: float = 120):
        for key, session in list(self.sessions.items()):
            # Your Chrome detaches sooner, so its "debugging this browser" bar does not linger (V2-09).
            limit = chrome_idle_s if key.startswith("chrome:") else idle_s
            if session.healthy and self.clock() - session.last_used > limit and not session.lock.locked():
                await session.close()
            if key.startswith("isolated:") and not session.healthy:
                self.sessions.pop(key, None)

    async def close_all(self):
        await asyncio.gather(*(s.close() for s in self.sessions.values()), return_exceptions=True)
        self.sessions.clear()


def text_of(result: dict) -> str:
    """The text content of an MCP tool result."""
    return "\n".join(
        part.get("text", "") for part in (result or {}).get("content", []) if part.get("type") == "text"
    )
