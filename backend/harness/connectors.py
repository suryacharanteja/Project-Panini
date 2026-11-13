"""Connectors: Model Context Protocol (MCP) servers the agent can use as tools.

Configure them in PANINI's data directory, connectors.json:

    {"servers": {
        "files": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "D:/notes"]},
        "github": {"command": "github-mcp-server", "args": ["stdio"], "env": {"GITHUB_TOKEN": "..."}}
    }}

Each server runs as a local stdio process, started on first use. Every tool call that
is not annotated read-only needs the person's confirmation in the foreground, and is
refused in background runs. Results are data, never instructions.
"""

import asyncio
import json
import logging
import os

from ..extension_pairing import data_directory
from .mcp_stdio import PROTOCOL, McpStdio
from .mcp_tools import list_tools, tool_result

logger = logging.getLogger(__name__)
__all__ = ["PROTOCOL", "ConnectorError", "Connectors", "Server"]


class ConnectorError(RuntimeError):
    pass


class Server(McpStdio):
    def __init__(self, name, spec):
        super().__init__(f"Connector {name}", ConnectorError)
        self.name, self.spec = name, spec
        self.tools = []
        self.lock = asyncio.Lock()
        self.error = ""

    async def start(self):
        from .. import private_mode

        if private_mode.active():
            raise ConnectorError(private_mode.reason())
        if self.alive:
            return
        env = {**os.environ, **{k: str(v) for k, v in (self.spec.get("env") or {}).items()}}
        try:
            await self.spawn(
                self.spec["command"], self.spec.get("args", []), env=env, cwd=self.spec.get("cwd")
            )
        except (OSError, KeyError) as exc:
            self.error = f"Could not start: {exc}"
            raise ConnectorError(f"Connector {self.name} could not start.") from None
        self.tools = []
        try:
            await self.initialize()
            self.tools = await list_tools(self.request)
        except asyncio.CancelledError:
            await self.close()
            raise
        except (ConnectorError, OSError, ValueError) as exc:
            await self.close()
            self.error = str(exc)
            raise ConnectorError(self.error) from None
        self.error = ""

    async def call(self, tool, arguments):
        async with self.lock:
            await self.start()
            result = await self.request("tools/call", {"name": tool, "arguments": arguments}, timeout=120)
        try:
            return tool_result(result)
        except ValueError as exc:
            raise ConnectorError(str(exc)) from None


class Connectors:
    def __init__(self, path=None, *, secrets=None, http_client=None):
        self.path = path or data_directory() / "connectors.json"
        self._secrets = secrets
        self.http_client = http_client  # Tests inject a mock transport.
        self.servers = {}
        self.load()

    @property
    def secrets(self):
        if self._secrets is None:
            from .secrets_store import SecretStore

            self._secrets = SecretStore(self.path.parent / "connector-secrets.bin")
        return self._secrets

    def load(self):
        from .mcp_http import HttpServer

        try:
            config = json.loads(self.path.read_text(encoding="utf-8")) if self.path.is_file() else {}
        except (OSError, ValueError) as exc:
            logger.warning("connectors.json is invalid: %s", exc)
            config = {}
        specs = config.get("servers") or {}
        self.servers = {}
        for name, spec in list(specs.items())[:20]:
            if not isinstance(spec, dict):
                continue
            if spec.get("url"):
                shared = {**spec, "_shared_client": self.http_client is not None}
                self.servers[name] = HttpServer(name, shared, self.secrets, client=self.http_client)
            elif spec.get("command"):
                secret_env = self.secrets.get(f"connector-env:{name}") or {}
                self.servers[name] = Server(name, {**spec, "env": {**(spec.get("env") or {}), **secret_env}})

    def specs(self):
        try:
            config = json.loads(self.path.read_text(encoding="utf-8")) if self.path.is_file() else {}
        except (OSError, ValueError):
            config = {}
        return dict(config.get("servers") or {})

    def save(self, name, spec, secret_env=None):
        """Add or replace one server in connectors.json; secret env values go to the vault."""
        specs = self.specs()
        specs[name] = spec
        self._write(specs)
        if secret_env is not None:
            self.secrets.set(f"connector-env:{name}", secret_env or None)

    def remove(self, name):
        specs = self.specs()
        if name not in specs:
            raise KeyError(f"No connector named {name}.")
        del specs[name]
        self._write(specs)
        self.secrets.set(f"connector-env:{name}", None)
        self.secrets.set(f"oauth:{name}", None)
        self.secrets.set(f"client:{name}", None)

    def set_background_grant(self, name, tool_name, allowed):
        """Owner grant for an MCP tool already declared read-only by its server."""
        server = self.servers.get(name)
        if not server:
            raise KeyError(f"No connector named {name}.")
        tool = next((item for item in server.tools if item["name"] == tool_name), None)
        if not tool or not tool["read_only"]:
            raise ValueError("Only an available read-only tool can be granted background access.")
        specs = self.specs()
        granted = set(specs[name].get("background_read_only") or [])
        if allowed:
            granted.add(tool_name)
        else:
            granted.discard(tool_name)
        specs[name]["background_read_only"] = sorted(granted)
        self._write(specs)
        server.spec["background_read_only"] = sorted(granted)
        return sorted(granted)

    def _write(self, specs):
        import os as _os

        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"servers": specs}, indent=2), encoding="utf-8")
        _os.replace(temporary, self.path)

    async def reload(self):
        await self.close()
        self.load()
        return await self.status()

    async def status(self):
        from .. import private_mode
        from .mcp_http import SignInRequired

        if private_mode.active():
            return [
                {
                    "name": server.name,
                    "kind": "remote" if hasattr(server, "url") else "local",
                    "state": "unavailable",
                    "error": private_mode.reason(),
                    "signed_in": False,
                    "tools": [],
                }
                for server in self.servers.values()
            ]

        rows = []
        for server in self.servers.values():
            state = "ready"
            try:
                async with server.lock:
                    await server.start()
            except SignInRequired as exc:
                server.error, state = str(exc), "sign_in"
            except ConnectorError as exc:
                server.error = server.error or str(exc)
            if server.error and state == "ready":
                state = "error"
            rows.append(
                {
                    "name": server.name,
                    "kind": "remote" if hasattr(server, "url") else "local",
                    "state": state,
                    "error": server.error,
                    "signed_in": bool(hasattr(server, "url") and self.secrets.get(f"oauth:{server.name}")),
                    "tools": [{k: t[k] for k in ("name", "description", "read_only")} for t in server.tools],
                }
            )
        return rows

    async def sign_in(self, name, redirect_uri):
        server = self.servers.get(name)
        if not server or not hasattr(server, "sign_in"):
            raise KeyError(f"No remote connector named {name}.")
        return await server.sign_in(redirect_uri)

    async def complete(self, state, code):
        for server in self.servers.values():
            if getattr(server, "pending_auth", None) and server.pending_auth.get("state") == state:
                await server.complete(state, code)
                return server.name
        raise ConnectorError("This sign-in has expired or was already used. Start it again from the console.")

    def sign_out(self, name):
        server = self.servers.get(name)
        if not server or not hasattr(server, "sign_out"):
            raise KeyError(f"No remote connector named {name}.")
        server.sign_out()

    def catalog(self, only=None):
        """Prompt text listing tools of servers that have started (optionally only some servers)."""
        lines = [
            f"- {s.name}.{t['name']}{' (read-only)' if t['read_only'] else ''}: {t['description'][:160]}"
            for s in self.servers.values()
            if only is None or s.name in only
            for t in s.tools
        ]
        if not lines:
            return ""
        return "Connector tools (call use_connector with server, tool and JSON arguments):\n" + "\n".join(
            lines[:80]
        )

    def tool(self, server, name):
        entry = self.servers.get(server)
        if not entry:
            raise KeyError(f"No connector named {server}.")
        return entry, next((t for t in entry.tools if t["name"] == name), None)

    async def close(self):
        await asyncio.gather(*(s.close() for s in self.servers.values()), return_exceptions=True)
