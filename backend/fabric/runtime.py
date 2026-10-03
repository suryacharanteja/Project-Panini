"""The remote fabric runtime (V2-14): one object that owns identity, devices, sessions, push and ingress, and starts
the transport chosen by PANINI_REMOTE_MODE (off | lan | tailscale | relay).

Remote modes refuse to start until the V2-13 red-team gate passes (v2flags reads gated flags as off until then).
`off` closes listeners and the relay socket; devices stay registered.
"""

import asyncio
import json
import logging

from .. import v2flags
from .devices import Devices, PcIdentity
from .ingress import Ingress
from .push import Push
from .server import FabricServer

logger = logging.getLogger("panini.fabric")
LISTEN_PORT = 3211


class Fabric:
    def __init__(self, app, store, secrets_store, *, settings_path=None, tailscale=None):
        self.app, self.store, self.secrets = app, store, secrets_store
        self.identity = PcIdentity.load(secrets_store)
        self.devices = Devices(store, self.identity)
        self.server = FabricServer(app, self.identity, self.devices).install()
        self.push = Push(store, secrets_store)
        self.settings_path = settings_path
        self.ingress = Ingress(store, self.settings().get("hooks", {}))
        self._tailscale = tailscale
        self.listener = None
        self.listener_job = None
        self.relay = None
        self.state = {"mode": "off", "state": "off", "reason": "", "origin": ""}

    # Settings --------------------------------------------------------------------------------------
    def settings(self) -> dict:
        if self.settings_path is None:
            from ..extension_pairing import data_directory

            self.settings_path = data_directory() / "fabric.json"
        try:
            return json.loads(self.settings_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    @property
    def mode(self) -> str:
        return v2flags.value("PANINI_REMOTE_MODE")  # "off" until the red-team gate passes.

    @property
    def tailscale(self):
        if self._tailscale is None:
            from .transports.tailscale import Tailscale

            self._tailscale = Tailscale()
        return self._tailscale

    # Lifecycle -------------------------------------------------------------------------------------
    async def start(self) -> dict:
        await self.stop()
        mode = self.mode
        blocked = v2flags.blocked_by_gate("PANINI_REMOTE_MODE")
        if mode == "off":
            reason = blocked or "Remote access is off."
            self.state = {
                "mode": "off",
                "state": "blocked" if blocked else "off",
                "reason": reason,
                "origin": "",
            }
            return self.state
        port = int(self.settings().get("port") or LISTEN_PORT)
        if mode == "lan":
            from ..harness.mobile import lan_addresses

            await self._listen("0.0.0.0", port, mode)
            addresses = lan_addresses()
            origin = f"http://{addresses[0]}:{port}" if addresses else f"http://localhost:{port}"
            self.state = {"mode": mode, "state": "ready", "reason": "", "origin": origin}
        elif mode == "tailscale":
            served = await asyncio.to_thread(
                self.tailscale.serve, port, funnel=bool(self.settings().get("funnel"))
            )
            if served["state"] != "ready":
                self.state = {"mode": mode, "state": "needs_setup", "reason": served["reason"], "origin": ""}
                return self.state
            await self._listen("127.0.0.1", port, mode, owner_login=served["owner_login"])
            self.state = {"mode": mode, "state": "ready", "reason": "", "origin": served["origin"]}
        elif mode == "relay":
            await self._relay()
        return self.state

    async def _listen(self, host, port, mode, owner_login=""):
        import uvicorn

        from .transports.listener import build

        config = uvicorn.Config(build(self, mode, owner_login), host=host, port=port, log_level="warning",
                                access_log=False, server_header=False)  # fmt: skip
        self.listener = uvicorn.Server(config)
        self.listener.install_signal_handlers = lambda: None
        self.listener_job = asyncio.ensure_future(self.listener.serve())
        for _ in range(50):
            if self.listener.started or self.listener_job.done():
                break
            await asyncio.sleep(0.1)

    async def _relay(self):
        import httpx

        from .relay_client import RelayClient

        url = str(self.settings().get("relay_url") or "").rstrip("/")
        if not url.startswith(("https://", "http://127.0.0.1", "http://localhost")):
            self.state = {
                "mode": "relay",
                "state": "needs_setup",
                "reason": "Set the relay address in Admin.",
                "origin": "",
            }
            return
        channel = self.secrets.get("fabric_relay_channel")
        if not channel:
            async with httpx.AsyncClient(timeout=15) as client:
                created = (await client.post(f"{url}/channels")).json()
            channel = json.dumps(created)
            self.secrets.set("fabric_relay_channel", channel)
        record = json.loads(channel)
        ws_url = url.replace("https://", "wss://").replace("http://", "ws://")
        self.relay = RelayClient(
            ws_url, record["channel_id"], record["pc_secret"], self.server, ingress=self.ingress
        )
        self.relay.start()
        self.state = {"mode": "relay", "state": "ready", "reason": "", "origin": f"{url}/app/",
                      "channel": record["channel_id"]}  # fmt: skip

    async def stop(self):
        if self.listener is not None:
            self.listener.should_exit = True
            try:
                await asyncio.wait_for(self.listener_job, 10)
            except (TimeoutError, asyncio.CancelledError):
                self.listener_job.cancel()
            self.listener = self.listener_job = None
        if self.relay is not None:
            await self.relay.stop()
            self.relay = None
        self.state = {**self.state, "state": "off"}

    def save_settings(self, patch: dict) -> dict:
        allowed = {"relay_url", "port", "funnel"}
        if set(patch) - allowed:
            raise ValueError("Settings are relay_url, port and funnel.")
        value = {**self.settings(), **patch}
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings_path.write_text(json.dumps(value), encoding="utf-8")
        return value

    def watch_interrupts(self, interrupts) -> None:
        """Push each new approval or question to paired phones (title and level only; details over the fabric)."""

        def opened(event, view):
            if event != "interrupt.opened" or not self.devices.any_active():
                return
            message = {
                "type": "interrupt",
                "id": view["id"],
                "title": view.get("title", ""),
                "level": view.get("level"),
            }
            try:
                asyncio.get_running_loop().create_task(self.push.send(message))
            except RuntimeError:
                pass

        interrupts.listeners.append(opened)

    def status(self) -> dict:
        return {
            **self.state,
            "pc_id": self.identity.pc_id,
            "gate": v2flags.redteam_gate(),
            "devices": self.devices.list(),
            "push_public_key": self.push.public_key,
            "hooks": sorted(self.ingress.hooks),
        }


def runtime(app) -> "Fabric":
    """The app's fabric, created on first use (keys are generated only when remote access is used)."""
    fabric = getattr(app.state, "fabric_runtime", None)
    if fabric is None:
        from ..extension_pairing import data_directory
        from ..harness.secrets_store import SecretStore

        h = app.state.harness
        fabric = Fabric(app, h.store, SecretStore(data_directory() / "fabric-secrets.bin"))
        fabric.watch_interrupts(h.interrupts)
        app.state.fabric_runtime = fabric
    return fabric
