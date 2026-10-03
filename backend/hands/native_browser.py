"""The browser half of the native sandbox (ADR-0001 §5): used when no container engine is installed.

Browsers cannot run inside the AppContainer (their own IPC needs named pipes the container blocks), so each sandbox
computer gets PANINI's isolated Playwright browser instead: a throwaway profile per computer, headless, web content
in Chromium's own renderer sandbox, private and local addresses blocked, service workers off, and its output kept
in the computer's workspace. Commands and files stay in the AppContainer.
"""

import asyncio
from pathlib import Path

from .playwright_mcp import PlaywrightSession, isolated_preset, text_of


class NativeBrowserError(RuntimeError):
    pass


SCROLL_STEP_PX = 600


class NativeBrowser:
    def __init__(self, workspace_of, *, session_factory=PlaywrightSession):
        self.workspace_of = workspace_of  # computer -> Path
        self.factory = session_factory
        self.sessions: dict[str, PlaywrightSession] = {}
        self.snapshots: dict[str, int] = {}

    def session(self, computer: str) -> PlaywrightSession:
        if computer not in self.sessions:
            artifacts = Path(self.workspace_of(computer)) / "browser"
            self.sessions[computer] = self.factory(isolated_preset(f"sandbox-{computer}", artifacts))
        return self.sessions[computer]

    async def _call(self, computer, tool, args=None) -> str:
        result = await self.session(computer).call(tool, args or {})
        text = text_of(result)
        if (result or {}).get("isError"):
            raise NativeBrowserError(text[:500] or f"{tool} failed.")
        return text

    def _check_ref(self, computer, args):
        if args.get("snapshotId") != self.snapshots.get(computer):
            raise NativeBrowserError("That ref is from an older page; take a new sandbox_snapshot.")

    async def action(self, computer: str, action: str, args: dict) -> dict:
        if action == "navigate":
            from .net_guard import BlockedUrl, check_url

            try:
                check_url(args["url"])
            except BlockedUrl as exc:
                raise NativeBrowserError(str(exc)) from None
            self.snapshots.pop(computer, None)
            try:
                return {"text": await self._call(computer, "browser_navigate", {"url": args["url"]})}
            except NativeBrowserError as exc:
                if "interrupted by another navigation" not in str(exc):
                    raise
                # After a blocked page, Chromium's error page can interrupt the next navigation once.
                return {"text": await self._call(computer, "browser_navigate", {"url": args["url"]})}
        if action in ("read", "snapshot"):
            text = await self._call(computer, "browser_snapshot")
            self.snapshots[computer] = self.snapshots.get(computer, 0) + 1
            return {"text": text, "snapshotId": self.snapshots[computer]}
        if action == "screenshot":
            return {"text": await self._call(computer, "browser_take_screenshot", {"type": "png"})}
        if action == "click":
            self._check_ref(computer, args)
            return {
                "text": await self._call(
                    computer, "browser_click", {"target": args["ref"], "element": "target"}
                )
            }
        if action == "type":
            self._check_ref(computer, args)
            call = {"target": args["ref"], "element": "target", "text": args["text"]}
            if args.get("submit"):
                call["submit"] = True
            return {"text": await self._call(computer, "browser_type", call)}
        if action == "key":
            return {"text": await self._call(computer, "browser_press_key", {"key": args["key"]})}
        if action == "scroll":
            key = "PageDown" if args["deltaY"] > 0 else "PageUp"
            text = ""
            for _ in range(max(1, min(20, round(abs(args["deltaY"]) / SCROLL_STEP_PX)))):
                text = await self._call(computer, "browser_press_key", {"key": key})
            return {"text": text}
        raise NativeBrowserError("This control needs a container computer.")

    async def close(self, computer: str) -> None:
        session = self.sessions.pop(computer, None)
        self.snapshots.pop(computer, None)
        if session is not None:
            await asyncio.shield(session.close())
