"""The remote fabric runtime (V2-14): one object that owns identity, devices, sessions, push and ingress, and starts
the transport chosen by PANINI_REMOTE_MODE (off | lan | tailscale | relay).

Red-team qualification is deferred by default; explicit future enforcement uses the V2-13 gate.
`off` closes listeners and the relay socket; devices stay registered.
"""

import asyncio
import base64
import json
import logging
import secrets

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
        from .passkeys import Passkeys

        self.server.passkeys = Passkeys(
            store,
            self.devices,
            lambda: self.settings().get("passkey_origin") or self.state.get("origin", ""),
        )
        self.push = Push(store, secrets_store)
        self.server.push_key = self.push.public_key
        self.settings_path = settings_path
        hooks = self.settings().get("hooks", {})
        self.ingress = Ingress(
            store,
            {
                key: {**hook, "secret": self.secrets.get(hook["secret_ref"]) or ""}
                if hook.get("secret_ref")
                else hook
                for key, hook in hooks.items()
            },
        )
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
        return v2flags.value("PANINI_REMOTE_MODE")

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
        allowed = {"relay_url", "port", "funnel", "passkey_origin"}
        if set(patch) - allowed:
            raise ValueError("Settings are relay_url, port, funnel and passkey_origin.")
        if patch.get("passkey_origin"):
            from .passkeys import Passkeys

            origin = str(patch["passkey_origin"]).rstrip("/")
            Passkeys(self.store, self.devices, lambda: origin).configuration(origin)
            patch = {**patch, "passkey_origin": origin}
        value = {**self.settings(), **patch}
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings_path.write_text(json.dumps(value), encoding="utf-8")
        return value

    def configure_phone_webhook(self, secret):
        if not isinstance(secret, str) or not secret.startswith("whsec_"):
            raise ValueError("Enter the OpenAI webhook signing secret.")
        try:
            if len(base64.b64decode(secret[6:], validate=True)) < 16:
                raise ValueError()
        except ValueError:
            raise ValueError("Invalid webhook signing secret.") from None
        settings = self.settings()
        hooks = settings.setdefault("hooks", {})
        path_secret = hooks.get("phone", {}).get("path_secret") or secrets.token_urlsafe(24)
        self.secrets.set("phone_webhook", secret)
        hooks["phone"] = {"kind": "openai", "path_secret": path_secret, "secret_ref": "phone_webhook"}
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings_path.write_text(json.dumps(settings), encoding="utf-8")
        self.ingress.hooks["phone"] = {**hooks["phone"], "secret": secret}
        return {"path": f"/hooks/phone/{path_secret}"}

    def configure_trigger_hook(self, name):
        from ..triggers.rules import SLUG

        if not isinstance(name, str) or not SLUG.fullmatch(name):
            raise ValueError("Choose a short webhook name with letters, numbers or hyphens.")
        hook_id = "generic:" + name
        hooks = self.settings().get("hooks", {})
        if hook_id in hooks:
            raise ValueError("This webhook already exists; its signing secret cannot be shown again.")
        secret, path_secret = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
        hooks[hook_id] = {"kind": "generic", "path_secret": path_secret, "secret_ref": "trigger:" + name}
        self.secrets.set("trigger:" + name, secret)
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.settings_path.with_suffix(".tmp")
        temporary.write_text(json.dumps({**self.settings(), "hooks": hooks}), encoding="utf-8")
        temporary.replace(self.settings_path)
        self.ingress.hooks[hook_id] = {**hooks[hook_id], "secret": secret}
        return {"path": f"/hooks/{hook_id}/{path_secret}", "signing_secret": secret}

    def watch_interrupts(self, interrupts) -> None:
        """Push each new approval or question to paired phones (title and level only; details over the fabric)."""

        def opened(event, view):
            if (
                event not in {"interrupt.opened", "interrupt.resolved", "interrupt.expired"}
                or not self.devices.any_active()
            ):
                return
            if v2flags.enabled("PANINI_PRESENCE"):
                return
            message = {
                "type": "interrupt" if event == "interrupt.opened" else "interrupt_resolved",
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
            "passkey_origin": self.settings().get("passkey_origin") or self.state.get("origin", ""),
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
        h.fabric = fabric  # Channels (WhatsApp) register their webhooks with its ingress.
        if hasattr(h, "continuity"):
            h.continuity.fabric = fabric
        if hasattr(h, "triggers"):
            from ..triggers.sources.webhooks import handler

            fabric.ingress.on("generic", handler(h.triggers))
        app.state.fabric_runtime = fabric
    return fabric
