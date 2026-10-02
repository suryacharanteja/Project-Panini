"""CDP relay to your Chrome (V2-09).

Python port of Playwright's extension relay (`packages/playwright-core/src/tools/mcp/cdpRelay.ts`,
`browserModel.ts`, `cdpRelayV2.ts`; Copyright Microsoft Corporation, Apache-2.0), with PANINI scoping:

- Playwright MCP connects to a loopback WebSocket (`/cdp/<token>` plus a bearer header); one client only.
- The extension leg is not a second socket: it is JevRev Claw's already-authenticated connection (or a test leg).
- Known tabs come only from the scope the person granted (the attached tab or workspace tabs); browser-level
  commands are policy-filtered instead of being routed blindly through any tab (invariant I9).
- Any change of scope (detach, another tab, reconnect, Chrome's Cancel button) closes the Playwright connection.
"""

import asyncio
import json
import logging
import secrets
import time
from dataclasses import dataclass, field

from websockets.asyncio.server import serve
from websockets.datastructures import Headers
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Response

from .cdp_policy import decide

logger = logging.getLogger(__name__)
MAX_PENDING_EVENTS = 4096


class RelayClosed(RuntimeError):
    pass


@dataclass
class TabSession:
    tab_id: int
    session_id: str
    target_info: dict
    child_sessions: set = field(default_factory=set)


class ScopedBrowserModel:
    """Playwright's BrowserModel limited to the tabs the person granted."""

    def __init__(self, leg, scope: dict, emit):
        self.leg, self.scope, self.emit = leg, scope, emit
        self.known_tabs: set[int] = set(scope.get("tabs") or [])
        self.created_tabs: set[int] = set()
        self.tab_sessions: dict[int, TabSession] = {}
        self.auto_attach = False
        self._next = 1

    # Extension → model -------------------------------------------------------
    async def on_tab_created(self, tab: dict):
        tab_id = tab.get("id")
        opener = tab.get("opener_tab_id")
        # Only tabs PANINI opened, or popups of a granted tab inside a workspace that allows new tabs.
        if tab_id is None or not (
            tab_id in self.created_tabs or (opener in self.known_tabs and self.scope.get("allow_new_tabs"))
        ):
            return
        self.known_tabs.add(tab_id)
        if self.auto_attach:
            await self._attach(tab_id)

    def on_tab_removed(self, tab_id: int):
        self.known_tabs.discard(tab_id)
        self._detach(tab_id)

    def on_debugger_event(self, tab_id: int, session_id: str | None, method: str, params: dict):
        tab = self.tab_sessions.get(tab_id)
        if tab is None:
            return  # Events from tabs outside the scope are dropped.
        child = (params or {}).get("sessionId")
        if method == "Target.attachedToTarget" and child:
            tab.child_sessions.add(child)
        elif method == "Target.detachedFromTarget" and child:
            tab.child_sessions.discard(child)
        self.emit({"sessionId": session_id or tab.session_id, "method": method, "params": params})

    def on_debugger_detach(self, tab_id: int):
        self._detach(tab_id)

    # Playwright → model ------------------------------------------------------
    async def enable_auto_attach(self):
        self.auto_attach = True
        for tab_id in sorted(self.known_tabs):
            await self._attach(tab_id)

    async def create_target(self, url: str) -> dict:
        if not self.scope.get("allow_new_tabs"):
            raise RelayClosed("This workspace does not allow PANINI to open new tabs.")
        tab = await self.leg.create_tab(url or "about:blank")
        tab_id = tab.get("id")
        if tab_id is None:
            raise RelayClosed("Failed to create tab")
        self.created_tabs.add(tab_id)
        self.known_tabs.add(tab_id)
        session = await self._attach(tab_id)
        return {"targetId": session.target_info.get("targetId")}

    async def close_target(self, target_id: str) -> dict:
        session = next(
            (s for s in self.tab_sessions.values() if s.target_info.get("targetId") == target_id), None
        )
        if session is None or session.tab_id not in self.created_tabs:
            raise RelayClosed("PANINI only closes tabs it opened.")
        await self.leg.close_tab(session.tab_id)
        self.known_tabs.discard(session.tab_id)
        self.created_tabs.discard(session.tab_id)
        self._detach(session.tab_id)  # Playwright waits for the detach before it considers the page closed.
        return {"success": True}

    def target_info(self, session_id: str | None):
        session = next((s for s in self.tab_sessions.values() if s.session_id == session_id), None)
        return session.target_info if session else None

    async def send(self, session_id: str | None, method: str, params: dict):
        allowed, reason = decide(method, params, self.scope)
        if not allowed:
            raise RelayClosed(reason)
        if session_id is None:
            # Browser-level commands: only what the policy allows, routed through the granted tab.
            session = next(iter(self.tab_sessions.values()), None)
            if session is None:
                raise RelayClosed(f"No attached tab for {method}.")
            return await self.leg.send(session.tab_id, None, method, params)
        session = next((s for s in self.tab_sessions.values() if s.session_id == session_id), None)
        child = None
        if session is None:
            session = next((s for s in self.tab_sessions.values() if session_id in s.child_sessions), None)
            child = session_id
        if session is None:
            raise RelayClosed(f"No tab found for sessionId: {session_id}")
        return await self.leg.send(session.tab_id, child, method, params)

    # Internals -----------------------------------------------------------------
    async def _attach(self, tab_id: int) -> TabSession:
        existing = self.tab_sessions.get(tab_id)
        if existing:
            return existing
        info = await self.leg.attach(tab_id)
        try:
            # A background tab gets no animation frames, so Playwright's "stable element" wait would never
            # finish; the tab PANINI now controls comes to the front, as v1's tab selection does.
            await self.leg.send(tab_id, None, "Page.bringToFront", {})
        except Exception:  # noqa: BLE001 -- Best effort: the action still runs if the tab is already visible.
            logger.debug("bringToFront failed for tab %s", tab_id)
        session = TabSession(tab_id, f"pw-tab-{self._next}", dict(info or {}))
        self._next += 1
        self.tab_sessions[tab_id] = session
        self.emit(
            {
                "method": "Target.attachedToTarget",
                "params": {
                    "sessionId": session.session_id,
                    "targetInfo": {**session.target_info, "attached": True},
                    "waitingForDebugger": False,
                },
            }
        )
        return session

    def _detach(self, tab_id: int):
        session = self.tab_sessions.pop(tab_id, None)
        if session is None:
            return
        self.emit(
            {
                "method": "Target.detachedFromTarget",
                "params": {"sessionId": session.session_id, "targetId": session.target_info.get("targetId")},
            }
        )


class CdpRelay:
    """The loopback CDP endpoint Playwright MCP connects to for your Chrome."""

    def __init__(self, leg, scope: dict, *, clock=time.monotonic):
        self.leg, self.scope, self.clock = leg, dict(scope), clock
        self.token = secrets.token_urlsafe(32)
        self.server = None
        self.client = None
        self.model = ScopedBrowserModel(leg, self.scope, self._emit)
        self.closed_reason = ""
        self.last_command = clock()
        self.outbox: asyncio.Queue = asyncio.Queue()
        self.methods: set[str] = set()  # Every method Playwright sent (the spike records these).
        self.port = 0

    @property
    def endpoint(self) -> str:
        return f"ws://127.0.0.1:{self.port}/cdp/{self.token}"

    @property
    def header(self) -> str:
        return f"Authorization: Bearer {self.token}"

    async def start(self):
        self.server = await serve(
            self._connection, "127.0.0.1", 0, process_request=self._check, max_size=16 * 1024 * 1024
        )
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    def _check(self, connection, request):
        expected_path = f"/cdp/{self.token}"
        if request.path != expected_path or request.headers.get("Authorization") != f"Bearer {self.token}":
            return Response(403, "Forbidden", Headers(), b"")
        if self.client is not None or self.closed_reason:
            return Response(409, "Conflict", Headers(), b"")
        return None

    async def _connection(self, ws):
        if self.client is not None:
            await ws.close(1008, "Another CDP client already connected")
            return
        self.client = ws
        pump = asyncio.get_running_loop().create_task(self._pump(ws))
        try:
            async for raw in ws:
                try:
                    message = json.loads(raw)
                except ValueError:
                    continue
                asyncio.get_running_loop().create_task(self._handle(message))
        except ConnectionClosed:
            pass
        finally:
            pump.cancel()
            if self.client is ws:
                self.client = None
            await self.close("Playwright disconnected")

    async def _handle(self, message: dict):
        identifier, session_id = message.get("id"), message.get("sessionId")
        method, params = str(message.get("method", "")), message.get("params") or {}
        self.last_command = self.clock()
        self.methods.add(method)
        try:
            result = await self._command(method, params, session_id)
            reply = {"id": identifier, "result": result if result is not None else {}}
        except Exception as exc:  # noqa: BLE001 -- Every failure goes back to Playwright as a CDP error.
            reply = {"id": identifier, "error": {"message": str(exc)[:500]}}
        if session_id:
            reply["sessionId"] = session_id
        await self._send(reply)

    async def _command(self, method, params, session_id):
        if method == "Browser.getVersion":
            return {
                "protocolVersion": "1.3",
                "product": "Chrome/PANINI-Claw-Bridge",
                "userAgent": "PANINI-CDP-Relay/1.0",
            }
        if method == "Browser.setDownloadBehavior":
            return {}  # Downloads stay with Chrome's own behaviour until V2-10 opens an approved window.
        if method == "Target.setDiscoverTargets" and not session_id:
            return {}  # Discovery would list tabs outside the scope; the relay reports granted tabs itself.
        if method == "Target.setAutoAttach" and not session_id:
            await self.model.enable_auto_attach()
            return {}
        if method == "Target.createTarget":
            return await self.model.create_target(params.get("url", ""))
        if method == "Target.closeTarget":
            return await self.model.close_target(params.get("targetId", ""))
        if method == "Target.getTargetInfo" and not session_id:
            return {"targetInfo": self.model.target_info(session_id) or {}}
        return await self.model.send(session_id, method, params)

    def _emit(self, message: dict):
        """Queue an event. Events and replies leave in order, as from Chrome: Playwright relies on, for
        example, Target.attachedToTarget arriving before the Target.createTarget reply."""
        if self.client is None:
            return
        if self.outbox.qsize() >= MAX_PENDING_EVENTS:
            asyncio.get_running_loop().create_task(self.close("overloaded"))
            return
        self.outbox.put_nowait(message)

    async def _send(self, message: dict):
        self.outbox.put_nowait(message)

    async def _pump(self, ws):
        while True:
            message = await self.outbox.get()
            try:
                await ws.send(json.dumps(message))
            except ConnectionClosed:
                return

    # Extension events --------------------------------------------------------
    def on_event(self, tab_id, session_id, method, params):
        self.model.on_debugger_event(tab_id, session_id, method, params)

    def on_detached(self, tab_id, reason):
        self.model.on_debugger_detach(tab_id)
        if not self.model.tab_sessions:
            asyncio.get_running_loop().create_task(self.close(f"detached: {reason}"))

    async def on_tab(self, event, tab):
        if event == "created":
            await self.model.on_tab_created(tab)
        elif event == "removed":
            self.model.on_tab_removed(tab.get("id"))

    def scope_changed(self):
        asyncio.get_running_loop().create_task(self.close("scope_changed"))

    async def close(self, reason: str):
        if self.closed_reason:
            return
        self.closed_reason = reason
        for tab_id in list(self.model.tab_sessions):
            try:
                await asyncio.wait_for(self.leg.detach(tab_id), 3)
            except Exception:  # noqa: BLE001 -- Closing is best effort; the extension detaches on disconnect.
                pass
        if self.client is not None:
            try:
                await self.client.close(1000, reason[:120])
            except Exception:  # noqa: BLE001
                pass
        if self.server is not None:
            self.server.close()
