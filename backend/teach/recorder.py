"""Owner-only recording lifecycle. Raw recorder output is parsed in memory and discarded."""

import asyncio
import json
import re
import time
from pathlib import Path
from uuid import uuid4

from ..hands.browser_v2 import _json_in
from ..hands.playwright_mcp import BrowserDriverError, text_of
from ..harness.memory import _SECRET
from ..panini.contracts import Fault
from . import desktop_events
from .codegen_parser import parse
from .parameterize import propose


class Recorder:
    def __init__(self, harness):
        self.h, self.store = harness, harness.store
        self.lock = asyncio.Lock()
        self.current = None
        self.timer = None
        self.starting = None
        self.starting_session = None
        # A process restart never resumes a recording or repeats its effects.
        self.store.execute(
            "UPDATE teach_recordings SET state='interrupted',updated=? WHERE state='recording'",
            (time.time(),),
        )

    async def start(self, ctx, browser):
        if self.starting:
            raise ValueError("A recording is already starting.")
        self.starting = asyncio.current_task()
        try:
            return await self._start(ctx, browser)
        except asyncio.CancelledError:
            if self.starting_session:
                await self.starting_session.close()
            raise
        finally:
            self.starting = self.starting_session = None

    async def _start(self, ctx, browser):
        async with self.lock:
            if self.current or self.h.teach.replayer.active:
                raise ValueError("Stop the current recording or replay first.")
            if browser not in {"managed", "chrome"}:
                raise ValueError("Record in PANINI's browser or an explicitly attached Chrome tab.")
            hand = self.h.registry.hands.get("browser_snapshot")
            if hand is None:
                raise ValueError("The browser vocabulary is unavailable.")
            selection = await self.h.registry.call(ctx, "browser_select", {"browser": browser})
            if selection.outcome != "ok":
                raise ValueError(selection.content)
            session = await hand.session(browser, ctx)
            self.starting_session = session
            flags = session.preset.flags
            capture_root = (
                Path(flags[flags.index("--output-dir") + 1]) / "panini-teach"
                if "--output-dir" in flags
                else None
            )
            before = set(capture_root.iterdir()) if capture_root and capture_root.exists() else set()
            probe = await session.call(
                "browser_evaluate",
                {"function": "() => ({guard: window.__paniniTeachGuard === true, url: location.href})"},
            )
            state = _json_in(text_of(probe))
            if state.get("guard") is not True or probe.get("isError"):
                raise ValueError(
                    "Enable Teach in Settings, then restart the browser driver to load the recording guard."
                )
            result = await session.call("browser_start_recording", {})
            if result.get("isError"):
                raise ValueError("The browser could not begin recording.")
            identifier = uuid4().hex
            folders = (
                set(capture_root.iterdir()) - before if capture_root and capture_root.exists() else set()
            )
            self.current = {
                "id": identifier,
                "browser": browser,
                "session": session,
                "hand": hand,
                "started": time.time(),
                "capture": next(iter(folders)) if len(folders) == 1 else None,
                "url": state.get("url", ""),
            }
            self.store.execute(
                "INSERT INTO teach_recordings(id,kind,browser,state,created,updated) VALUES (?,'browser',?,'recording',?,?)",
                (identifier, browser, time.time(), time.time()),
            )
            self.timer = asyncio.create_task(self.expire(identifier))
            return {"id": identifier, "state": "recording", "browser": browser, "expires_in": 900}

    async def expire(self, identifier):
        await asyncio.sleep(900)
        if self.current and self.current["id"] == identifier:
            await self.stop(discard=True)

    async def start_desktop(self, ctx, window):
        if self.starting:
            raise ValueError("A recording is already starting.")
        self.starting = asyncio.current_task()
        created_context = ctx.run_id not in self.h.desktop_fine.sessions
        try:
            async with self.lock:
                if self.current or self.h.teach.replayer.active:
                    raise ValueError("Stop the current recording or replay first.")
                self.h.teach.require()
                if ctx.principal.kind != "owner":
                    raise ValueError("Only the owner can start desktop recording.")
                self.h.desktop_fine.ready(ctx)
                adapter = self.h.desktop_fine.adapter
                selected = await adapter.select(window)
                if not adapter.permits(selected["application"]):
                    raise ValueError("This application is not allowed.")
                identifier = uuid4().hex
                self.starting_session = DesktopRecording(adapter.pipe, identifier)
                result = await adapter.pipe.call(
                    {"op": "record_start", "task_id": identifier, "target": selected, "timeout_ms": 12000}
                )
                self.h.teach.require()
                self.h.desktop_fine.ready(ctx)
                self.current = {
                    "id": identifier,
                    "kind": "desktop",
                    "browser": "desktop",
                    "session": self.starting_session,
                    "native_id": result["recording_id"],
                    "application": selected["application"],
                    "before": result["before"],
                    "started": time.time(),
                }
                self.store.execute(
                    "INSERT INTO teach_recordings(id,kind,browser,state,created,updated) VALUES (?,'desktop','desktop','recording',?,?)",
                    (identifier, time.time(), time.time()),
                )
                self.timer = asyncio.create_task(self.expire(identifier))
                return {
                    "id": identifier,
                    "state": "recording",
                    "kind": "desktop",
                    "application": selected["application"],
                    "expires_in": 900,
                }
        except BaseException:
            if self.current and self.current["session"] is self.starting_session:
                self.current = None
            if self.starting_session:
                await self.starting_session.close()
            raise
        finally:
            self.starting = self.starting_session = None
            if created_context:
                self.h.desktop_fine.sessions.pop(ctx.run_id, None)

    async def finish_desktop(self, current, discard):
        if discard:
            await current["session"].close()
            return "discarded", {}
        result = await current["session"].pipe.call(
            {
                "op": "record_stop",
                "task_id": current["id"],
                "recording_id": current["native_id"],
                "timeout_ms": 12000,
            }
        )
        if result["state"] != "stopped":
            return "interrupted", {}
        data = desktop_events.normalize(result["events"], current["application"])
        data["recording_artifacts"] = {}
        for key, value in (("start", current["before"]), ("end", result["after"])):
            text = desktop_events.snapshot(value)
            data["recording_artifacts"][key] = self.h.artifacts.put(
                "snapshot", text.encode(), "text/plain", run_id=current["id"]
            )
        return "draft", data

    async def stop(self, *, discard=False):
        starting = self.starting
        if starting and starting is not asyncio.current_task():
            starting.cancel()
            await asyncio.gather(starting, return_exceptions=True)
        async with self.lock:
            current = self.current
            if not current:
                return {"state": "idle"}
            self.current = None
            if self.timer and self.timer is not asyncio.current_task():
                self.timer.cancel()
            self.timer = None
            state, data = "interrupted", {}
            try:
                if current.get("kind") == "desktop":
                    state, data = await self.finish_desktop(current, discard)
                    return {"id": current["id"], "state": state, "replay": data}
                result = await current["session"].call("browser_stop_recording", {}, timeout_s=30)
                if result.get("isError"):
                    raise BrowserDriverError("Recording did not stop cleanly.")
                if not discard:
                    text = text_of(result)
                    # Only the code fence is fed to the AST parser; page state is not source code.
                    code = re.search(r"```(?:python|typescript|js)\s*\n(.*?)\n```", text, re.S)
                    data = propose(parse(code[1] if code else ""))
                    url = current["url"]
                    offset = 0
                    if (
                        url.startswith(("https://", "http://"))
                        and not _SECRET.search(url)
                        and data["steps"][0]["tool"] != "browser_navigate"
                    ):
                        offset = 1
                        for index, step in enumerate(data["steps"], start=2):
                            step["id"] = f"s{index}"
                        data["steps"].insert(
                            0, {"id": "s1", "tool": "browser_navigate", "args": {"url": url}}
                        )
                    data["recording_artifacts"] = self.import_captures(current, offset)
                    state = "draft"
                else:
                    state = "discarded"
            except (BrowserDriverError, ValueError, Fault):
                # Stop must remain available after disabling the feature or losing the driver.
                try:
                    await current["session"].close()
                except (BrowserDriverError, ValueError, Fault, OSError):
                    pass
            finally:
                self.store.execute(
                    "UPDATE teach_recordings SET state=?,data=?,updated=? WHERE id=?",
                    (state, json.dumps(data), time.time(), current["id"]),
                )
            return {"id": current["id"], "state": state, "replay": data}

    def import_captures(self, current, offset):
        folder = current["capture"]
        if not folder or not folder.exists() or folder.is_symlink():
            return {}
        artifacts = {}
        for path in sorted(folder.iterdir()):
            if path.is_symlink() or path.stat().st_size > 2000000:
                continue
            if path.name in {"start.txt", "end.txt"}:
                key, kind, mime = path.stem, "snapshot", "text/plain"
            elif re.fullmatch(r"step-\d{3}\.png", path.name):
                key, kind, mime = f"s{int(path.stem[5:]) + offset}", "screenshot", "image/png"
            else:
                continue
            artifacts[key] = self.h.artifacts.put(kind, path.read_bytes(), mime, run_id=current["id"])
        return artifacts

    def status(self):
        if self.starting:
            return {"state": "starting"}
        if not self.current:
            return {"state": "idle"}
        return {k: self.current[k] for k in ("id", "browser", "started")} | {
            "state": "recording",
            "kind": self.current.get("kind", "browser"),
            "application": self.current.get("application", ""),
        }


class DesktopRecording:
    def __init__(self, pipe, identifier):
        self.pipe, self.identifier = pipe, identifier

    async def close(self):
        await self.pipe.call({"op": "stop", "task_id": self.identifier, "timeout_ms": 12000})
