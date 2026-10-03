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
    def __init__(self, workspace_of, *, session_factory=PlaywrightSession, box: bool = True):
        self.box = box
        self.isolation = "none"
        self.workspace_of = workspace_of  # computer -> Path
        self.factory = session_factory
        self.sessions: dict[str, PlaywrightSession] = {}
        self.snapshots: dict[str, int] = {}

    def session(self, computer: str) -> PlaywrightSession:
        if computer not in self.sessions:
            artifacts = Path(self.workspace_of(computer)) / "browser"
            preset = isolated_preset(f"sandbox-{computer}", artifacts, live=("sandbox", computer))
            preset.flags.append(
                "--sandbox"
            )  # Chromium's renderer sandbox, explicitly (never Playwright's default).
            # Coordinate mouse tools for the owner's take-over; the adapter never offers them to the agent.
            preset.flags[preset.flags.index("--caps") + 1] = "pdf,vision"
            self.sessions[computer] = self.factory(preset, **self.boxed(artifacts))
        return self.sessions[computer]

    def boxed(self, artifacts: Path) -> dict:
        """The driver and browser start inside a box (lowbox.py): a job that ends them all with PANINI, caps
        memory and processes, a private home and temp folder, and no administrator rights."""
        if not self.box:
            return {}
        from . import lowbox
        from .playwright_mcp import entry_script, node_executable

        node = node_executable()
        if not node:
            return {}
        from . import sandbox_user

        # Strongest first: PANINI's own Windows account (set up once by the owner), else a job-limited box.
        as_user = sandbox_user.credentials() is not None
        self.isolation = "sandbox-account" if as_user else "job-box"
        argv = lowbox.command([node, str(entry_script())], artifacts, integrity="medium", as_user=as_user)
        return {"command": argv[0], "args": argv[1:]}

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
        if action == "human_click":
            return {"text": await self._call(computer, "browser_mouse_click_xy",
                                             {"x": args["x"], "y": args["y"], "element": "owner"})}  # fmt: skip
        if action == "human_type":
            for char in args["text"][:2000]:
                await self._call(computer, "browser_press_key", {"key": char})
            return {"text": f"Typed {min(len(args['text']), 2000)} characters."}
        if action == "human_key":
            return {"text": await self._call(computer, "browser_press_key", {"key": args["key"]})}
        if action == "human_scroll":
            return {
                "text": await self._call(
                    computer, "browser_mouse_wheel", {"deltaX": 0, "deltaY": args["deltaY"]}
                )
            }
        raise NativeBrowserError("Unknown browser action.")

    async def frame(self, computer: str) -> bytes:
        """The current page as PNG, for the owner's live view."""
        if computer not in self.sessions:
            raise NativeBrowserError("The sandbox browser is not open.")
        folder = Path(self.workspace_of(computer)) / "browser"  # The driver saves relative to its box folder.
        await self._call(computer, "browser_take_screenshot", {"type": "png", "filename": "live-frame.png"})
        frames = sorted(folder.rglob("live-frame*.png"), key=lambda f: f.stat().st_mtime)
        if not frames:
            raise NativeBrowserError("The browser did not produce a frame.")
        data = frames[-1].read_bytes()
        for old in frames:
            old.unlink(missing_ok=True)
        return data

    async def close(self, computer: str) -> None:
        session = self.sessions.pop(computer, None)
        self.snapshots.pop(computer, None)
        if session is not None:
            await asyncio.shield(session.close())
