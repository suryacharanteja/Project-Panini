"""The managed browser hand (V2-08): PANINI's own persistent Edge/Chrome profile driven by Playwright MCP.

Tools act on element refs from the last accessibility snapshot. Safety in this item (V2-10 completes it):
http(s) URLs only, never typing into a password field, and an element description that must match the ref's
accessible name so the policy engine classifies the real control ("Buy now" is a purchase however it is
described).
"""

import os
import re
import time
from pathlib import Path

from ..core_contracts import Readiness, ToolResult
from .playwright_mcp import (
    BrowserDriverError,
    PlaywrightPool,
    SessionCrashed,
    entry_script,
    managed_preset,
    node_executable,
    text_of,
)

OUTPUT_CHARS = 12000
REF_LINE = re.compile(r"\[ref=(?P<ref>[a-z0-9]+)\]")
PASSWORD = re.compile(r"\bpass(word|code|phrase)\b|\bpin\b|\bcvv\b|\bsecurity code\b", re.I)
BROWSERS = {
    "msedge": [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ],
    "chrome": [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ],
}

# PANINI tool → Playwright tool.
TOOLS = {
    "managed_navigate": "browser_navigate",
    "managed_snapshot": "browser_snapshot",
    "managed_click": "browser_click",
    "managed_type": "browser_type",
    "managed_select": "browser_select_option",
    "managed_press_key": "browser_press_key",
    "managed_wait_for": "browser_wait_for",
    "managed_tabs": "browser_tabs",
    "managed_back": "browser_navigate_back",
    "managed_screenshot": "browser_take_screenshot",
    "managed_verify_text": "browser_verify_text_visible",
}
REF_TOOLS = {"managed_click", "managed_type", "managed_select"}
# V2-09: the same vocabulary in your Chrome (through JevRev Claw and the CDP relay).
CHROME_TOOLS = {name.replace("managed_", "chrome_", 1): tool for name, tool in TOOLS.items()}


def canonical(name: str) -> str:
    """managed_click / chrome_click → managed_click: one implementation for both hands."""
    return "managed_" + name.split("_", 1)[1]


def browser_name() -> str:
    return "chrome" if os.getenv("PANINI_MANAGED_BROWSER", "msedge").strip().lower() == "chrome" else "msedge"


def browser_installed(name: str) -> bool:
    return any(Path(p).exists() for p in BROWSERS.get(name, [])) or os.name != "nt"


def driver_selected() -> bool:
    from .. import v2flags

    return v2flags.value("PANINI_BROWSER_DRIVER") == "playwright"


def element_names(snapshot: str) -> dict[str, str]:
    """ref → the snapshot line's role and accessible name, e.g. 'button "Buy now"'."""
    names = {}
    for line in snapshot.splitlines():
        match = REF_LINE.search(line)
        if match:
            names[match["ref"]] = line[: match.start()].strip(" -:")
    return names


PAGE_STATE = re.compile(r"^### (Page|Snapshot)\b", re.M)
PAGE_TITLE = re.compile(r"Page Title: (.+)")
SNAPSHOT_LINK = re.compile(r"\[Snapshot\]\(([^)]+)\)")
SNAPSHOT_CHARS = 60_000


def inline_snapshot(text: str, root: Path) -> str:
    """Playwright 1.63 writes the page snapshot after an action to a file and returns a link; the model needs
    the snapshot itself. Only files inside PANINI's own driver output folder are read."""
    match = SNAPSHOT_LINK.search(text)
    if not match:
        return text
    try:
        path = Path(match.group(1)).resolve()
        if not path.is_relative_to(root.resolve()) or path.suffix != ".yml":
            return text
        snapshot = path.read_text(encoding="utf-8", errors="replace")[:SNAPSHOT_CHARS]
    except (OSError, ValueError):
        return text
    return text[: match.start()] + "\n```yaml\n" + snapshot + "\n```" + text[match.end() :]


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 1}


class ManagedBrowserHand:
    lenient_required = False

    def __init__(self, manifest, runtime):
        self.manifest, self.runtime = manifest, runtime
        self.names: dict[str, str] = {}  # ref → accessible name, from the latest page state
        self._pool = None

    @property
    def pool(self) -> PlaywrightPool:
        harness = self.runtime.get("harness")
        pool = getattr(harness, "browsers", None) if harness is not None else None
        if pool is None:
            if self._pool is None:
                self._pool = PlaywrightPool()
            pool = self._pool
        return pool

    def session(self):
        from ..extension_pairing import data_directory

        data = data_directory()
        headless = os.getenv("PANINI_MANAGED_HEADLESS", "").strip().lower() in {"1", "true", "yes"}
        return self.pool.session(
            managed_preset(data, data / "artifacts", browser=browser_name(), headless=headless)
        )

    def output_root(self) -> Path:
        from ..extension_pairing import data_directory

        return data_directory() / "artifacts" / "playwright"

    def enabled(self) -> bool:
        return driver_selected()

    disabled_message = "The managed browser needs PANINI_BROWSER_DRIVER=playwright."

    async def ensure_session(self):
        return self.session()

    async def readiness(self, capability=None):
        if not driver_selected():
            return Readiness("unavailable", "Set the browser engine to Playwright (PANINI_BROWSER_DRIVER).")
        if node_executable() is None:
            return Readiness("needs_setup", "Node.js is needed for the browser driver.")
        try:
            entry_script()
        except BrowserDriverError as exc:
            return Readiness("needs_setup", str(exc))
        if not browser_installed(browser_name()):
            return Readiness("needs_setup", "Install Microsoft Edge or Chrome.")
        return Readiness("ready")

    async def call(self, ctx, spec, args):
        from ..capabilities.browser import web_url
        from ..panini.contracts import Fault

        if not self.enabled():
            return ToolResult("blocked", self.disabled_message)
        name = canonical(spec.name)
        try:
            payload = self._arguments(name, args)
        except Fault as exc:
            return ToolResult("blocked", exc.message)
        except ValueError as exc:
            return ToolResult("blocked", str(exc))
        if name == "managed_navigate" or (
            name == "managed_tabs" and args.get("action") == "new" and args.get("url")
        ):
            try:
                web_url(payload.get("url", ""))
            except Fault as exc:
                return ToolResult("blocked", exc.message)
        started = time.monotonic()
        try:
            session = await self.ensure_session()
            result = await session.call(TOOLS[name], payload, timeout_s=spec.timeout_ms / 1000)
        except SessionCrashed as exc:
            return ToolResult(
                "unknown_effect",
                f"{exc} Check the page before trying again.",
                dispatched=True,
            )
        except BrowserDriverError as exc:
            return self.driver_failure(exc)
        text = inline_snapshot(text_of(result), self.output_root())
        if (result or {}).get("isError"):
            return ToolResult("failed", text[:2000] or "The browser action failed.")
        self.names.update(element_names(text))
        settled = int((time.monotonic() - started) * 1000)
        data = {"page": text[:OUTPUT_CHARS], "settled_ms": settled}  # What the model reads.
        evidence = []
        if name == "managed_verify_text":
            evidence.append({"kind": "assertion", "summary": f"Text is visible: {args['text'][:200]}"})
        elif name in ("managed_snapshot", "managed_screenshot") or PAGE_STATE.search(text):
            # Playwright returns the page as it is after the action: an observation, like v1's direct actions.
            title = PAGE_TITLE.search(text)
            evidence.append(
                {
                    "kind": "page",
                    "summary": f"Page after {name}: {title.group(1)[:200] if title else 'observed'}",
                }
            )
        return ToolResult("ok", text[:OUTPUT_CHARS], data=data, evidence=evidence)

    def driver_failure(self, exc) -> ToolResult:
        return ToolResult("failed", str(exc)[:500])

    def _arguments(self, name, args):
        if name in REF_TOOLS:
            ref, element = str(args.get("ref", "")).strip(), str(args.get("element", "")).strip()
            if not ref or not element:
                raise ValueError("Give the element ref from the latest snapshot and describe the element.")
            actual = self.names.get(ref, "")
            if (
                actual
                and not (_words(actual) & _words(element))
                and _words(actual) - {"button", "link", "textbox"}
            ):
                raise ValueError(
                    f"Ref {ref} is {actual}, not '{element}'. Describe the element as it appears in the snapshot."
                )
            if name == "managed_type" and (PASSWORD.search(actual) or PASSWORD.search(element)):
                raise ValueError(
                    "PANINI never types passwords or security codes. Ask the person to enter it."
                )
            base = {"element": element, "target": ref}
            if name == "managed_click":
                return base
            if name == "managed_type":
                return {**base, "text": str(args.get("text", "")), "submit": bool(args.get("submit"))}
            return {**base, "values": [str(v) for v in args.get("values") or []]}
        if name == "managed_navigate":
            return {"url": str(args.get("url", ""))}
        if name == "managed_press_key":
            return {"key": str(args.get("key", ""))}
        if name == "managed_wait_for":
            payload = {}
            if args.get("text"):
                payload["text"] = str(args["text"])
            if args.get("seconds"):
                payload["time"] = min(float(args["seconds"]), 30.0)
            return payload or {"time": 1}
        if name == "managed_tabs":
            payload = {"action": args.get("action", "list")}
            if isinstance(args.get("index"), int) and args["index"] >= 0:
                payload["index"] = args["index"]
            if args.get("url"):
                payload["url"] = str(args["url"])
            return payload
        if name == "managed_screenshot":
            return {"scale": "css", "type": "jpeg"}
        if name == "managed_verify_text":
            return {"text": str(args.get("text", ""))}
        return {}


class ChromeBrowserHand(ManagedBrowserHand):
    """Your Chrome (V2-09): the tabs you attached in JevRev Claw, driven by Playwright through PANINI's scoped
    CDP relay. Real (trusted) input; only the attached tab; re-checked on every command; Chrome shows its
    debugging bar while PANINI is in control and Cancel there stops it."""

    IDLE_S = 120

    def __init__(self, manifest, runtime):
        super().__init__(manifest, runtime)
        self.relay = None
        self.leg = None
        self.preset = None
        self.bound = None

    def enabled(self) -> bool:
        from .. import v2flags

        return v2flags.enabled("PANINI_CLAW_CDP")

    disabled_message = "Full control of your Chrome is off (PANINI_CLAW_CDP)."

    def bridge(self):
        from ..routes.browser import get_bridge

        adapter = (self.runtime.get("adapters") or {}).get("browser")
        request = getattr(adapter, "request", None)
        return get_bridge(request) if request is not None else None

    async def readiness(self, capability=None):
        if not self.enabled():
            return Readiness("unavailable", self.disabled_message)
        bridge = self.bridge()
        if bridge is None or not bridge.connected:
            return Readiness("needs_setup", "Connect JevRev Claw and attach a tab.")
        if not bridge.cdp_supported:
            return Readiness("needs_setup", "Update JevRev Claw to v4 for full control.")
        if bridge.attached_tab is None:
            return Readiness("needs_setup", "Attach the tab PANINI should use in JevRev Claw.")
        return Readiness("ready")

    async def ensure_session(self):
        from ..extension_pairing import data_directory
        from .cdp_policy import origin_of
        from .cdp_relay import CdpRelay
        from .claw_leg import ClawLeg
        from .playwright_mcp import chrome_preset

        bridge = self.bridge()
        if bridge is None or not bridge.connected:
            raise BrowserDriverError("Your Chrome is not connected. Attach a tab in JevRev Claw.")
        if not bridge.cdp_supported:
            raise BrowserDriverError(
                "Update JevRev Claw to v4 for full control, or use the basic browser tool."
            )
        if bridge.attached_tab is None:
            raise BrowserDriverError("No tab is attached. Attach the tab PANINI should use in JevRev Claw.")
        binding = bridge.binding()
        idle = self.relay is not None and time.monotonic() - self.relay.last_command > self.IDLE_S
        if self.relay is None or self.relay.closed_reason or self.bound != binding or idle:
            await self.release()
            self.leg = ClawLeg(bridge, binding)
            scope = {
                "tabs": [bridge.attached_tab],
                "origin": origin_of(bridge.tab_url),
                "allow_new_tabs": False,
            }
            self.relay = await CdpRelay(self.leg, scope).start()
            self.leg.listen(self.relay)
            self.bound = binding
            data = data_directory()
            self.preset = chrome_preset(self.relay, data / "artifacts")
            self.names.clear()
        return self.pool.session(self.preset)

    async def release(self):
        """End full control: close Playwright's connection, the relay and the debugger attachment."""
        if self.preset is not None:
            session = self.pool.sessions.pop(self.preset.key, None)
            if session is not None:
                await session.close()
        if self.relay is not None:
            await self.relay.close("released")
        if self.leg is not None:
            self.leg.close()
        self.relay = self.leg = self.preset = self.bound = None

    def driver_failure(self, exc) -> ToolResult:
        relay = self.relay
        if relay is not None and relay.closed_reason:
            reason = relay.closed_reason
            if reason.startswith("detached"):
                return ToolResult(
                    "blocked", "You stopped browser control in Chrome. Nothing more was done there."
                )
            if reason == "scope_changed":
                return ToolResult("blocked", "The attached tab changed. Attach it again to continue.")
        return ToolResult("failed", str(exc)[:500])
