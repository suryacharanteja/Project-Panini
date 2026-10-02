"""JSON-RPC over stdio for Model Context Protocol servers, shared by connectors and hands (V2-08).

One process, newline-delimited JSON-RPC messages, request/response matching by id. A process that exits fails
every request still waiting, so callers never hang.
"""

import asyncio
import json
import os
import subprocess

PROTOCOL = "2025-06-18"


class McpError(RuntimeError):
    pass


class McpStdio:
    def __init__(self, name: str, error=McpError):
        self.name, self.error_class = name, error
        self.process = None
        self.pending: dict[int, asyncio.Future] = {}
        self.sequence = 0
        self.reader = None

    @property
    def alive(self) -> bool:
        return self.process is not None and self.process.returncode is None

    async def spawn(self, command, args, *, env, cwd=None):
        self.process = await asyncio.create_subprocess_exec(
            command,
            *[str(a) for a in args],
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=env,
            cwd=cwd or None,
            limit=8 * 1024 * 1024,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        self.reader = asyncio.create_task(self._read())

    async def initialize(self, timeout=60):
        await self.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL,
                "capabilities": {},
                "clientInfo": {"name": "PANINI", "version": "1.0.0"},
            },
            timeout=timeout,
        )
        self.notify("notifications/initialized", {})

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
                            self.error_class(str(message["error"].get("message", "error"))[:500])
                        )
                    else:
                        future.set_result(message.get("result"))
        finally:
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(self.error_class(f"{self.name} stopped."))
            self.pending.clear()

    def _send(self, message):
        self.process.stdin.write((json.dumps(message) + "\n").encode())

    def notify(self, method, params):
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    async def request(self, method, params, timeout=60):
        if not self.alive:
            raise self.error_class(f"{self.name} is not running.")
        self.sequence += 1
        identifier = self.sequence
        future = asyncio.get_running_loop().create_future()
        self.pending[identifier] = future
        try:
            self._send({"jsonrpc": "2.0", "id": identifier, "method": method, "params": params})
            await self.process.stdin.drain()
            return await asyncio.wait_for(future, timeout)
        except TimeoutError:
            raise self.error_class(f"{self.name} did not answer in time.") from None
        except (OSError, ConnectionError):
            raise self.error_class(f"{self.name} disconnected.") from None
        finally:
            self.pending.pop(identifier, None)
            if not future.done():
                future.cancel()

    async def close(self, grace_s=3.0):
        if self.alive:
            try:
                self.process.stdin.close()
            except OSError:
                pass
            try:
                await asyncio.wait_for(self.process.wait(), grace_s)
            except TimeoutError:
                self.process.kill()
                await self.process.wait()
        if self.reader:
            self.reader.cancel()
            await asyncio.gather(self.reader, return_exceptions=True)
            self.reader = None

    async def kill(self):
        """Hard stop (tests and crash handling)."""
        if self.alive:
            self.process.kill()
            await self.process.wait()
        if self.reader:
            await asyncio.gather(self.reader, return_exceptions=True)
            self.reader = None
