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
import subprocess

from ..extension_pairing import data_directory

logger = logging.getLogger(__name__)
PROTOCOL = "2025-06-18"
MAX_RESULT = 20000


class ConnectorError(RuntimeError):
    pass


class Server:
    def __init__(self, name, spec):
        self.name, self.spec = name, spec
        self.process = None
        self.pending = {}
        self.sequence = 0
        self.tools = []
        self.reader = None
        self.lock = asyncio.Lock()
        self.error = ""

    async def start(self):
        if self.process and self.process.returncode is None:
            return
        env = {**os.environ, **{k: str(v) for k, v in (self.spec.get("env") or {}).items()}}
        try:
            self.process = await asyncio.create_subprocess_exec(
                self.spec["command"],
                *[str(a) for a in self.spec.get("args", [])],
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                env=env,
                cwd=self.spec.get("cwd") or None,
                limit=8 * 1024 * 1024,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except (OSError, KeyError) as exc:
            self.error = f"Could not start: {exc}"
            raise ConnectorError(f"Connector {self.name} could not start.") from None
        self.reader = asyncio.create_task(self._read())
        await self.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL,
                "capabilities": {},
                "clientInfo": {"name": "PANINI", "version": "1.0.0"},
            },
        )
        self.notify("notifications/initialized", {})
        listed = await self.request("tools/list", {})
        self.tools = [
            {
                "name": t["name"],
                "description": (t.get("description") or "")[:500],
                "read_only": bool((t.get("annotations") or {}).get("readOnlyHint")),
                "input_schema": t.get("inputSchema") or {"type": "object"},
            }
            for t in (listed or {}).get("tools", [])[:100]
        ]
        self.error = ""

    async def _read(self):
        try:
            while line := await self.process.stdout.readline():
                try:
                    message = json.loads(line)
                except ValueError:
                    continue
                future = self.pending.pop(message.get("id"), None)
                if future and not future.done():
                    if "error" in message:
                        future.set_exception(
                            ConnectorError(str(message["error"].get("message", "error"))[:500])
                        )
                    else:
                        future.set_result(message.get("result"))
        finally:
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(ConnectorError(f"Connector {self.name} stopped."))
            self.pending.clear()

    def _send(self, message):
        self.process.stdin.write((json.dumps(message) + "\n").encode())

    def notify(self, method, params):
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    async def request(self, method, params, timeout=60):
        self.sequence += 1
        identifier = self.sequence
        future = asyncio.get_running_loop().create_future()
        self.pending[identifier] = future
        self._send({"jsonrpc": "2.0", "id": identifier, "method": method, "params": params})
        await self.process.stdin.drain()
        try:
            return await asyncio.wait_for(future, timeout)
        except TimeoutError:
            self.pending.pop(identifier, None)
            raise ConnectorError(f"Connector {self.name} did not answer in time.") from None

    async def call(self, tool, arguments):
        async with self.lock:
            await self.start()
            result = await self.request("tools/call", {"name": tool, "arguments": arguments}, timeout=120)
        parts = []
        for part in (result or {}).get("content", []):
            if part.get("type") == "text":
                parts.append(part.get("text", ""))
            else:
                parts.append(f"[{part.get('type', 'content')} omitted]")
        text = "\n".join(parts)[:MAX_RESULT]
        return {"is_error": bool((result or {}).get("isError")), "text": text}

    async def close(self):
        if self.process and self.process.returncode is None:
            self.process.stdin.close()
            try:
                await asyncio.wait_for(self.process.wait(), 3)
            except TimeoutError:
                self.process.kill()
                await self.process.wait()
        if self.reader:
            self.reader.cancel()
            await asyncio.gather(self.reader, return_exceptions=True)


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
        from .mcp_http import SignInRequired

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
