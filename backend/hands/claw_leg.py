"""The extension leg of the CDP relay over JevRev Claw's authenticated socket (V2-09).

Each primitive becomes a `cdp` frame carrying the grant it was issued under (attached tab, attachment id,
workspace revision); the extension re-checks that grant and the method policy before touching Chrome. Events
come back as unsolicited `cdp_event` / `cdp_detached` / `cdp_tab` frames; a scope change closes the relay.
"""

import asyncio

from .cdp_relay import RelayClosed


class ClawLeg:
    def __init__(self, bridge, binding: dict):
        self.bridge, self.binding = bridge, dict(binding)
        self.relay = None

    async def attach(self, tab_id: int) -> dict:
        result = await self.bridge.cdp("attach", self.binding, tab_id=tab_id)
        return (result or {}).get("target_info", {})

    async def send(self, tab_id: int, session_id, method: str, params: dict):
        return await self.bridge.cdp(
            "send", self.binding, tab_id=tab_id, session_id=session_id, method=method, params=params or {}
        )

    async def detach(self, tab_id: int):
        await self.bridge.cdp("detach", self.binding, tab_id=tab_id)

    async def create_tab(self, url: str) -> dict:
        raise RelayClosed(
            "Open new tabs from your workspace in JevRev Claw; PANINI works in the attached tab."
        )

    async def close_tab(self, tab_id: int):
        raise RelayClosed("PANINI does not close your tabs.")

    def listen(self, relay):
        self.relay = relay
        self.bridge.cdp_listeners.append(self._frame)
        self.bridge.scope_listeners.append(self._scope)

    def close(self):
        for listeners, fn in (
            (self.bridge.cdp_listeners, self._frame),
            (self.bridge.scope_listeners, self._scope),
        ):
            if fn in listeners:
                listeners.remove(fn)

    def _frame(self, message: dict):
        relay = self.relay
        if relay is None:
            return
        kind = message.get("type")
        tab_id = message.get("tab_id")
        if kind == "cdp_event":
            relay.on_event(
                tab_id, message.get("session_id"), str(message.get("method", "")), message.get("params") or {}
            )
        elif kind == "cdp_detached":
            relay.on_detached(tab_id, str(message.get("reason", "detached")))
        elif kind == "cdp_tab":
            asyncio.get_running_loop().create_task(
                relay.on_tab(message.get("event"), message.get("tab") or {})
            )

    def _scope(self, reason: str):
        if self.relay is not None:
            self.relay.scope_changed()
