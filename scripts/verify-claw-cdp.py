"""Live check for V2-09: JevRev Claw v4 full control, end to end, in an isolated browser.

Starts an isolated PANINI app on a free loopback port, loads a disposable copy of JevRev Claw v4 into a
throwaway Microsoft Edge profile (headless; Edge still supports --load-extension), pairs it with a
real pairing code, attaches the trusted-input demo tab, and drives it through PANINI's chrome hand: Playwright MCP
→ scoped CDP relay → Claw's authenticated socket → chrome.debugger. It never touches your browser or your app.

Checks: clicks and key presses arrive as trusted input; the I9 policy refuses a cookie dump in the extension;
detaching the tab in Claw stops PANINI with a clear message.

    .venv\\Scripts\\python.exe scripts/verify-claw-cdp.py
"""

import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
WORK = Path(tempfile.mkdtemp(prefix="panini-claw-cdp-"))
os.environ.update(
    PANINI_DATA_DIR=str(WORK / "data"),
    PANINI_CLAW_CDP="true",
    PANINI_JEV_GATES="false",
    PANINI_CLI_ENABLED="false",
    JEV_REV_DESKTOP_ENABLED="false",
    PANINI_CAPABILITY_ROUTER_ENABLED="false",
)
TOKEN = "c" * 32


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_browser():
    """Microsoft Edge still accepts --load-extension; a throwaway profile keeps it isolated."""
    for path in (
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ):
        if Path(path).exists():
            return Path(path)
    raise SystemExit("Microsoft Edge is needed for this check.")


class Page:
    """A tiny CDP client for one page target of the test browser."""

    def __init__(self, ws):
        self.ws, self.ids = ws, 0

    async def evaluate(self, expression):
        self.ids += 1
        await self.ws.send(
            json.dumps(
                {
                    "id": self.ids,
                    "method": "Runtime.evaluate",
                    "params": {"expression": expression, "awaitPromise": True, "returnByValue": True},
                }
            )
        )
        while True:
            message = json.loads(await self.ws.recv())
            if message.get("id") == self.ids:
                result = message.get("result", {})
                if "exceptionDetails" in result:
                    raise RuntimeError(json.dumps(result["exceptionDetails"])[:500])
                return result.get("result", {}).get("value")


async def main():
    import uvicorn
    from websockets.asyncio.client import connect

    from backend.app import create_app
    from backend.hands.browser import ChromeBrowserHand
    from backend.hands.playwright_mcp import PlaywrightPool
    from backend.panini.settings import Settings

    app_port, site_port = free_port(), free_port()
    app = create_app(panini_settings=Settings(enabled=False, bootstrap_token="e" * 40), console_token=TOKEN)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=app_port, log_level="warning"))
    serving = asyncio.create_task(server.serve())
    site = ThreadingHTTPServer(
        ("127.0.0.1", site_port), partial(SimpleHTTPRequestHandler, directory=str(ROOT / "frontend/public"))
    )
    threading.Thread(target=site.serve_forever, daemon=True).start()
    while not server.started:
        await asyncio.sleep(0.05)

    # A disposable copy of Claw pointed at the isolated app. Test-only change: the popup page is opened as a tab
    # here (headless browsers have no toolbar popup), so the copy accepts messages from its own extension pages.
    extension = WORK / "extension"
    shutil.copytree(ROOT / "panini-core/capabilities/browser/extension", extension)
    background = extension / "background.js"
    text = background.read_text(encoding="utf-8")
    text = text.replace("ws://127.0.0.1:3200/ws/extension", f"ws://127.0.0.1:{app_port}/ws/extension")
    text = text.replace(
        "if (sender.tab || (sender.id && sender.id !== chrome.runtime.id))",
        "if (sender.id && sender.id !== chrome.runtime.id)",
    )
    background.write_text(text, encoding="utf-8")

    profile = WORK / "profile"
    browser = subprocess.Popen(
        [
            str(test_browser()),
            "--headless=new",
            "--remote-debugging-port=0",
            f"--user-data-dir={profile}",
            f"--load-extension={extension}",
            f"--disable-extensions-except={extension}",
            "--no-first-run",
            f"http://127.0.0.1:{site_port}/trusted-input-demo.html",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    checks = []
    try:
        active = profile / "DevToolsActivePort"
        deadline = time.monotonic() + 30
        while not active.exists() and time.monotonic() < deadline:
            await asyncio.sleep(0.1)
        debug = int(active.read_text().splitlines()[0])

        def listing():
            try:
                return json.loads(urllib.request.urlopen(f"http://127.0.0.1:{debug}/json/list").read())
            except OSError:
                return []  # The debugging port opens a moment after the file appears.

        worker = None
        while worker is None and time.monotonic() < deadline:
            worker = next(
                (
                    t
                    for t in listing()
                    if t["url"].startswith("chrome-extension://") and t["url"].endswith("/background.js")
                ),
                None,
            )
            await asyncio.sleep(0.2)
        extension_id = worker["url"].split("/")[2]
        request = urllib.request.Request(
            f"http://127.0.0.1:{debug}/json/new?chrome-extension://{extension_id}/popup.html", method="PUT"
        )
        popup_target = json.loads(urllib.request.urlopen(request).read())
        async with connect(popup_target["webSocketDebuggerUrl"], max_size=None) as ws:
            popup = Page(ws)
            send = lambda message: popup.evaluate(f"chrome.runtime.sendMessage({json.dumps(message)})")  # noqa: E731
            import httpx

            async with httpx.AsyncClient(
                base_url=f"http://127.0.0.1:{app_port}", headers={"x-panini-console": TOKEN}
            ) as client:
                code = (await client.post("/api/extension/pairing-code")).json()["code"]
            paired = await send({"kind": "pair", "code": code})
            assert paired.get("paired"), paired
            bridge = None
            while time.monotonic() < deadline + 30:
                bridge = app.state.extension_bridge
                state = await send({"kind": "state"})
                if bridge is not None and bridge.connected and state.get("connected"):
                    break
                await asyncio.sleep(0.3)
            demo = next(t for t in (await send({"kind": "state"}))["tabs"] if "Trusted input" in t["title"])
            attached = await send({"kind": "attach", "on": True, "tabId": demo["id"]})
            assert attached.get("tab") == demo["id"], attached
            while bridge.attached_tab != demo["id"]:
                await asyncio.sleep(0.1)
            assert bridge.cdp_supported, "Claw v4 did not advertise cdp-v1"
            checks.append("paired and attached; Claw v4 advertises full control (cdp-v1)")

            hand = ChromeBrowserHand(
                None,
                {
                    "adapters": {"browser": SimpleNamespace(request=SimpleNamespace(app=app))},
                    "harness": SimpleNamespace(browsers=PlaywrightPool()),
                },
            )
            spec = lambda name: SimpleNamespace(name=name, timeout_ms=30000)  # noqa: E731
            snap = await hand.call(None, spec("chrome_snapshot"), {})
            assert snap.outcome == "ok", snap.content
            button = next(r for r, n in hand.names.items() if "Press me" in n)
            field = next(r for r, n in hand.names.items() if "textbox" in n)
            clicked = await hand.call(
                None, spec("chrome_click"), {"element": hand.names[button], "ref": button}
            )
            assert clicked.outcome == "ok", clicked.content
            focused = await hand.call(
                None, spec("chrome_click"), {"element": hand.names[field], "ref": field}
            )
            assert focused.outcome == "ok", focused.content
            pressed = await hand.call(None, spec("chrome_press_key"), {"key": "a"})
            assert pressed.outcome == "ok", pressed.content
            for text in ("click trusted: yes", "key trusted: yes"):
                verified = await hand.call(None, spec("chrome_verify_text"), {"text": text})
                assert verified.outcome == "ok", (text, verified.content)
            checks.append("trusted input: the page saw isTrusted=true for the click and the key press")

            binding = bridge.binding()
            try:
                await bridge.cdp(
                    "send", binding, tab_id=demo["id"], method="Network.getAllCookies", params={}
                )
                raise AssertionError("The extension allowed Network.getAllCookies")
            except Exception as exc:  # noqa: BLE001
                assert "not allowed" in str(exc), exc
            checks.append("I9 in the extension: Network.getAllCookies refused")

            await send({"kind": "attach", "on": False})
            await asyncio.sleep(0.5)
            stopped = await hand.call(None, spec("chrome_snapshot"), {})
            assert stopped.outcome in ("blocked", "failed") and (
                "Attach" in stopped.content or "attach" in stopped.content
            ), stopped.content
            checks.append(f"detaching in Claw stops PANINI: {stopped.content}")
            await hand.release()
    finally:
        # Only this test browser's own process tree; never other browser windows.
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(browser.pid)], capture_output=True)
        site.shutdown()
        server.should_exit = True
        await serving
        shutil.rmtree(WORK, ignore_errors=True)
    for check in checks:
        print("PASS", check)
    print(f"{len(checks)}/4 checks passed")


if __name__ == "__main__":
    asyncio.run(main())
