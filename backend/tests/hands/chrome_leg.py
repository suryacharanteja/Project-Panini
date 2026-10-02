"""A stand-in for JevRev Claw's chrome.debugger primitives, for tests and the V2-09 spike only.

It drives a real Edge/Chrome on a throwaway profile through remote debugging (never the person's browser: PANINI
does not offer whole-browser remote debugging, LLD §5.9). Each "tab" is a page target reached over its own
DevTools WebSocket, which behaves like chrome.debugger: commands are scoped to that page and child targets
(iframes, workers) appear as flattened sessions.
"""

import asyncio
import itertools
import json
import shutil
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

from websockets.asyncio.client import connect

BROWSERS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
]


def browser_path():
    return next((p for p in BROWSERS if Path(p).exists()), None)


class DirectChromeLeg:
    def __init__(self):
        self.profile = Path(tempfile.mkdtemp(prefix="panini-cdp-leg-"))
        self.process = None
        self.port = 0
        self.targets: dict[int, dict] = {}  # tab id → DevTools target
        self.sockets: dict[int, object] = {}
        self.readers: dict[int, asyncio.Task] = {}
        self.pending: dict[int, asyncio.Future] = {}
        self.ids = itertools.count(1)
        self.tab_ids = itertools.count(100)
        self.relay = None
        self.sent: list[str] = []  # Every method that reached the "extension" (for I9 assertions).

    async def start(self, url="about:blank"):
        exe = browser_path()
        self.process = subprocess.Popen(
            [
                exe,
                "--headless=new",
                "--remote-debugging-port=0",
                f"--user-data-dir={self.profile}",
                "--no-first-run",
                "--no-default-browser-check",
                "about:blank",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        active = self.profile / "DevToolsActivePort"
        deadline = time.monotonic() + 20
        while not active.exists() and time.monotonic() < deadline:
            await asyncio.sleep(0.1)
        self.port = int(active.read_text().splitlines()[0])
        tab = await self.create_tab(url)
        return tab["id"]

    def _http(self, path, method="GET"):
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", method=method)
        with urllib.request.urlopen(request, timeout=10) as response:
            body = response.read()
        try:
            return json.loads(body or b"null")
        except ValueError:
            return body.decode(errors="replace")  # /json/close answers in plain text.

    async def create_tab(self, url):
        target = await asyncio.to_thread(self._http, f"/json/new?{url}", "PUT")
        tab_id = next(self.tab_ids)
        self.targets[tab_id] = target
        return {"id": tab_id, "url": url}

    async def close_tab(self, tab_id):
        target = self.targets.pop(tab_id)
        await asyncio.to_thread(self._http, f"/json/close/{target['id']}")

    async def attach(self, tab_id):
        target = self.targets[tab_id]
        ws = await connect(target["webSocketDebuggerUrl"], max_size=64 * 1024 * 1024)
        self.sockets[tab_id] = ws
        self.readers[tab_id] = asyncio.get_running_loop().create_task(self._read(tab_id, ws))
        info = await self.send(tab_id, None, "Target.getTargetInfo", {})
        return info["targetInfo"]

    async def _read(self, tab_id, ws):
        try:
            async for raw in ws:
                message = json.loads(raw)
                if "id" in message:
                    future = self.pending.pop(message["id"], None)
                    if future and not future.done():
                        if "error" in message:
                            future.set_exception(RuntimeError(message["error"].get("message", "error")))
                        else:
                            future.set_result(message.get("result", {}))
                elif self.relay is not None:
                    self.relay.on_event(
                        tab_id, message.get("sessionId"), message["method"], message.get("params", {})
                    )
        except Exception:  # noqa: BLE001 -- The socket closed: the tab detached.
            pass

    async def send(self, tab_id, session_id, method, params):
        self.sent.append(method)
        identifier = next(self.ids)
        future = asyncio.get_running_loop().create_future()
        self.pending[identifier] = future
        message = {"id": identifier, "method": method, "params": params or {}}
        if session_id:
            message["sessionId"] = session_id
        await self.sockets[tab_id].send(json.dumps(message))
        return await asyncio.wait_for(future, 30)

    async def detach(self, tab_id):
        ws = self.sockets.pop(tab_id, None)
        if ws is not None:
            await ws.close()
        reader = self.readers.pop(tab_id, None)
        if reader:
            reader.cancel()

    async def close(self):
        for tab_id in list(self.sockets):
            await self.detach(tab_id)
        if self.process:
            self.process.kill()
            self.process.wait(10)
        shutil.rmtree(self.profile, ignore_errors=True)
