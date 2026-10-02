"""Bridge to the Jev-Rev browser extension.

The extension holds a WebSocket to us and executes what we ask against the one
tab the user attached. Protocol:

    us  -> ext:  {"id": "<uuid>", "action": {"kind": "...", ...}}
    ext -> us:   {"type": "result", "id": "<uuid>", "ok": true, "result": ...}
                 {"type": "result", "id": "<uuid>", "ok": false, "error": "..."}
    ext -> us:   {"type": "hello", "source": ..., "version": ..., "tab": ...}

A snapshot returns the page's action space already enumerated and labelled
(`e1: click "Sign in"`), plus a `marker` that acts as a freshness token: the
extension refuses an `act` carrying a stale marker, so a decision made about a
page that has since changed can never be executed.
"""

import asyncio
import contextlib
import logging
import uuid
from contextvars import ContextVar
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

CALL_TIMEOUT = 30.0
PROTOCOL = 4


class ExtensionUnavailable(RuntimeError):
    """No extension is connected, or the attached tab went away."""


class ExtensionBridge:
    """Owns the single extension socket and correlates request/response ids."""

    def __init__(self) -> None:
        self._socket = None
        self._pending: Dict[str, asyncio.Future] = {}
        self._lock = asyncio.Lock()
        self.attached_tab: Optional[int] = None
        self.version: str = ""
        self.protocol = 0
        self.tab_title = ""
        self.tab_url = ""
        self.attachment_id = ""
        self.target_id = ""
        self.workspace = None
        self.device_id = ""
        self._bound_target = ContextVar("extension_target", default=None)
        # V2-09 full control: what this extension can do, and who wants its CDP frames and scope changes.
        self.capabilities: set = set()
        self.cdp_listeners: list = []  # callables(message) for cdp_event / cdp_detached / cdp_tab
        self.scope_listeners: list = []  # callables(reason) when the attached tab, workspace or socket changes
        self.connection = 0  # Increments per adopted socket (part of a relay's binding).

    @property
    def connected(self) -> bool:
        return self._socket is not None

    @property
    def compatible(self) -> bool:
        # Protocol 4 is the first with device pairing and mutual authentication.
        try:
            version = tuple(int(part) for part in self.version.split("."))
            return self.protocol == PROTOCOL and (version + (0, 0, 0))[:3] >= (3, 0, 0)
        except ValueError:
            return False

    @property
    def cdp_supported(self) -> bool:
        """JevRev Claw v4 with the debugger permission (`cdp-v1`)."""
        return self.connected and self.compatible and "cdp-v1" in self.capabilities

    def binding(self) -> Dict[str, Any]:
        """The current grant, as a full-control relay is bound to it."""
        if self.workspace:
            return {
                "connection": self.connection,
                "expected_tab": self.attached_tab,
                "expected_attachment": self.attachment_id,
                "expected_workspace": self.workspace["id"],
                "expected_revision": self.workspace["revision"],
            }
        return {
            "connection": self.connection,
            "expected_tab": self.attached_tab,
            "expected_attachment": self.attachment_id,
        }

    def _scope_changed(self, reason: str) -> None:
        for listener in list(self.scope_listeners):
            with contextlib.suppress(Exception):
                listener(reason)

    async def cdp(self, op: str, binding: Dict[str, Any], **args: Any) -> Any:
        """One full-control frame. Unlike call(), frames run concurrently (Playwright sends many small
        commands); each carries the grant it was issued under and is refused once the scope moved on."""
        socket = self._socket
        if socket is None:
            raise ExtensionUnavailable("No browser extension is connected.")
        if binding != self.binding():
            raise ExtensionUnavailable("The attached tab changed. Attach it again to continue.")
        request_id = uuid.uuid4().hex
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        expected = {k: v for k, v in binding.items() if k != "connection"}
        try:
            await socket.send_json(
                {"id": request_id, "action": {"kind": "cdp", "op": op, **expected, **args}}
            )
            return await asyncio.wait_for(future, timeout=CALL_TIMEOUT)
        except asyncio.TimeoutError as exc:
            raise ExtensionUnavailable(f"Extension did not answer '{op}' within {CALL_TIMEOUT:.0f}s") from exc
        finally:
            self._pending.pop(request_id, None)
            if not future.done():
                future.cancel()

    # ---- socket lifecycle ---------------------------------------------

    async def attach(self, socket, device_id: str = "") -> None:
        """Adopt a newly connected extension socket, replacing any previous one."""
        old = self._socket
        await self.detach(reason="another extension connected")
        self._socket = socket
        self.device_id = device_id
        self.connection += 1
        if old is not None:
            with contextlib.suppress(Exception):
                await old.close(code=1000)

    async def detach(self, reason: str = "extension disconnected", socket=None) -> None:
        if socket is not None and socket is not self._socket:
            return
        self._socket = None
        self.device_id = ""
        self.attached_tab = None
        self.target_id = ""
        self.tab_title = self.tab_url = self.attachment_id = ""
        self.protocol = 0
        self.workspace = None
        self.capabilities = set()
        self._scope_changed(reason)
        # Nothing will answer these now; fail them rather than let callers hang.
        for future in list(self._pending.values()):
            if not future.done():
                future.set_exception(ExtensionUnavailable(reason))
        self._pending.clear()

    def on_message(self, message: Dict[str, Any], socket=None) -> None:
        """Route one decoded frame from the extension."""
        if socket is not None and socket is not self._socket:
            return
        if not isinstance(message, dict):
            return
        if message.get("type") == "hello":
            tab = message.get("tab")
            tab = tab if type(tab) is int and tab >= 0 else None
            attachment = str(message.get("attachment_id", ""))[:100]
            workspace_protocol = message.get("protocol") in (3, PROTOCOL)
            workspace = self._workspace(message.get("workspace")) if workspace_protocol else None
            if (
                workspace_protocol
                and message.get("workspace") is not None
                and (workspace is None or workspace["selected_tab"] != tab)
            ):
                workspace = None
                tab = None
            old_scope = self._scope(self.workspace, self.attached_tab, self.attachment_id)
            new_scope = self._scope(workspace, tab, attachment)
            if old_scope != new_scope:
                self.target_id = uuid.uuid4().hex if tab is not None else ""
                for future in self._pending.values():
                    if not future.done():
                        future.set_exception(
                            ExtensionUnavailable("Attached tab changed. Review the target and run again.")
                        )
                self._pending.clear()
                self._scope_changed("scope_changed")
            self.workspace = workspace
            self.attached_tab = tab
            self.attachment_id = attachment
            self.protocol = message.get("protocol", 0)
            self.tab_title = str(message.get("tab_title", ""))[:300]
            self.tab_url = str(message.get("tab_url", ""))[:2048]
            self.version = str(message.get("version", ""))
            caps = message.get("capabilities")
            self.capabilities = {c for c in caps if isinstance(c, str)} if isinstance(caps, list) else set()
            logger.info("Extension connected (tab=%s)", self.attached_tab)
            return
        if message.get("type") in ("cdp_event", "cdp_detached", "cdp_tab"):
            for listener in list(self.cdp_listeners):
                with contextlib.suppress(Exception):
                    listener(message)
            return
        if message.get("type") != "result":
            return
        future = self._pending.pop(str(message.get("id")), None)
        if future is None or future.done():
            return
        if message.get("ok"):
            future.set_result(message.get("result"))
        else:
            future.set_exception(ExtensionUnavailable(str(message.get("error") or "extension error")))

    @staticmethod
    def _workspace(value):
        if not isinstance(value, dict):
            return None
        tabs = value.get("tabs")
        if (
            not isinstance(value.get("id"), str)
            or not value["id"]
            or len(value["id"]) > 100
            or not isinstance(value.get("revision"), str)
            or not value["revision"]
            or len(value["revision"]) > 100
            or not isinstance(tabs, list)
            or not 1 <= len(tabs) <= 24
        ):
            return None
        if any(not isinstance(t, dict) or type(t.get("id")) is not int or t["id"] < 0 for t in tabs):
            return None
        if len({t["id"] for t in tabs}) != len(tabs) or value.get("selected_tab") not in [
            t["id"] for t in tabs
        ]:
            return None
        return {
            "id": value["id"],
            "revision": value["revision"],
            "selected_tab": value["selected_tab"],
            "allow_new_tabs": value.get("allow_new_tabs") is True,
            "tabs": [
                {"id": t["id"], "title": str(t.get("title", ""))[:300], "url": str(t.get("url", ""))[:2048]}
                for t in tabs
            ],
        }

    @staticmethod
    def _scope(workspace, tab, attachment):
        return (
            ("workspace", workspace["id"], workspace["revision"]) if workspace else ("tab", tab, attachment)
        )

    # ---- calls ---------------------------------------------------------

    @contextlib.contextmanager
    def bind_target(self, target_id):
        """Keep one run on its approved tab and connection, even across awaits."""
        if not target_id or target_id != self.target_id:
            raise ExtensionUnavailable("Attached tab changed. Review the target and run again.")
        token = self._bound_target.set((self._socket, target_id, self.attached_tab, self.attachment_id))
        try:
            yield
        finally:
            self._bound_target.reset(token)

    async def call(self, kind: str, **args: Any) -> Any:
        """Send one action and wait for its result."""
        # One in-flight action at a time: the extension drives a single tab, and
        # interleaving requests would race the page against its own marker.
        async with self._lock:
            socket = self._socket
            if socket is None:
                raise ExtensionUnavailable("No browser extension is connected.")
            bound = self._bound_target.get()
            if bound:
                if bound[0] is not socket or bound[1] != self.target_id:
                    raise ExtensionUnavailable("Attached tab changed. Review the target and run again.")
                if self.workspace:
                    args = {
                        **args,
                        "expected_tab": self.attached_tab,
                        "expected_attachment": self.attachment_id,
                        "expected_workspace": self.workspace["id"],
                        "expected_revision": self.workspace["revision"],
                    }
                else:
                    args = {**args, "expected_tab": bound[2], "expected_attachment": bound[3]}
            request_id = uuid.uuid4().hex
            future: asyncio.Future = asyncio.get_running_loop().create_future()
            self._pending[request_id] = future
            try:
                await socket.send_json({"id": request_id, "action": {"kind": kind, **args}})
                return await asyncio.wait_for(future, timeout=CALL_TIMEOUT)
            except asyncio.CancelledError:
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(socket.send_json({"type": "cancel", "id": request_id}), timeout=1)
                raise
            except asyncio.TimeoutError as exc:
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(socket.send_json({"type": "cancel", "id": request_id}), timeout=1)
                raise ExtensionUnavailable(
                    f"Extension did not answer '{kind}' within {CALL_TIMEOUT:.0f}s"
                ) from exc
            finally:
                self._pending.pop(request_id, None)
                if not future.done():
                    future.cancel()

    async def ping(self) -> Dict[str, Any]:
        return await self.call("ping")

    async def navigate(self, url: str) -> Any:
        return await self.call("navigate", url=url)

    async def snapshot(self, limit: int = 120) -> Dict[str, Any]:
        """Page state plus its enumerated, labelled action space."""
        for attempt in range(4):
            try:
                page = await self.call("snapshot", limit=limit)
                if self.workspace:
                    page["workspace"] = self.workspace
                    actions = list(page.get("actions", []))
                    actions.extend(
                        {
                            "id": f"workspace_tab_{t['id']}",
                            "kind": "select_tab",
                            "tab_id": t["id"],
                            "label": f"Switch to {t['title']} ({t['url']})",
                        }
                        for t in self.workspace["tabs"]
                        if t["id"] != self.attached_tab
                    )
                    if self.workspace["allow_new_tabs"] and len(self.workspace["tabs"]) < 24:
                        actions.append(
                            {
                                "id": "workspace_open_tab",
                                "kind": "open_tab",
                                "label": "Open a website in a new workspace tab",
                            }
                        )
                    page["actions"] = actions
                return page
            except ExtensionUnavailable as exc:
                # Navigation may unload the content script between injection and
                # observation. Retry only the read; never repeat a form submission.
                transient = any(
                    text in str(exc).lower()
                    for text in ("receiving end does not exist", "message port closed", "frame was removed")
                )
                if not transient or attempt == 3:
                    raise
                await asyncio.sleep(0.3 * (attempt + 1))

    async def act(self, action_id: str, op: str, marker: str = "", text: str = "", value: str = "") -> Any:
        return await self.call("act", id=action_id, op=op, marker=marker, text=text, value=value)

    async def select_tab(self, tab_id: int):
        return await self.call("select_tab", tab_id=tab_id)

    async def open_tab(self, url: str):
        return await self.call("open_tab", url=url)

    async def text(self) -> Dict[str, Any]:
        return await self.call("text")

    async def screenshot(self) -> str:
        """Returns a data: URL, or '' when the tab cannot be captured."""
        with contextlib.suppress(Exception):
            result = await self.call("screenshot")
            return (result or {}).get("dataUrl", "") or ""
        return ""

    def status(self) -> Dict[str, Any]:
        return {
            "connected": self.connected,
            "attached_tab": self.attached_tab,
            "version": self.version,
            "protocol": self.protocol,
            "compatible": self.compatible,
            "target_id": self.target_id,
            "tab_title": self.tab_title,
            "tab_url": self.tab_url,
            "workspace": self.workspace,
            "paired": bool(self.device_id),
            "full_control": self.cdp_supported,
        }
