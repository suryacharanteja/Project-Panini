"""V2-09 full control of your Chrome: the I9 method policy (Python and JS tables identical), the scoped relay,
the bridge's full-control frames, the chrome hand, and real Playwright → relay → Edge runs (trusted input,
cookie isolation, revocation)."""

import asyncio
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.hands.cdp_policy import TABLE, decide
from backend.hands.cdp_relay import CdpRelay, RelayClosed, ScopedBrowserModel

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).parent))


# Policy ------------------------------------------------------------------------------
@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_the_python_and_extension_policy_tables_are_identical():
    script = (
        "import('./panini-core/capabilities/browser/extension/cdp-policy.mjs')"
        ".then(m => process.stdout.write(JSON.stringify(m.TABLE)))"
    )
    js = json.loads(
        subprocess.run(["node", "-e", script], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    )
    assert js == TABLE


@pytest.mark.parametrize(
    "method",
    [
        "Network.getAllCookies",
        "Network.clearBrowserCookies",
        "Network.setCookie",
        "Network.setCookies",
        "Network.deleteCookies",
        "Storage.getCookies",
        "Storage.clearDataForOrigin",
        "Browser.close",
        "Browser.grantPermissions",
        "Target.createBrowserContext",
        "Target.attachToBrowserTarget",
        "Debugger.enable",
        "Security.setIgnoreCertificateErrors",
        "Page.setBypassCSP",
        "Fetch.enable",
        "Unknown.method",
    ],
)
def test_methods_beyond_the_tab_are_denied(method):
    assert decide(method, {}, {})[0] is False


def test_navigation_and_cookie_rules():
    assert decide("Page.navigate", {"url": "https://example.com"})[0]
    assert decide("Page.navigate", {"url": "about:blank"})[0]
    for url in (
        "javascript:alert(1)",
        "file:///C:/x",
        "chrome://settings",
        "data:text/html,x",
        "devtools://x",
    ):
        assert not decide("Page.navigate", {"url": url})[0], url
    scope = {"origin": "https://shop.example"}
    assert decide("Network.getCookies", {"urls": ["https://shop.example/cart"]}, scope)[0]
    assert not decide("Network.getCookies", {"urls": ["https://bank.example/"]}, scope)[0]
    assert not decide("Network.getCookies", {}, scope)[0]
    assert decide("Fetch.enable", {}, {"network_tools": True})[0]


def test_every_method_playwright_needs_is_allowed_or_handled_by_the_relay():
    recorded = json.loads(
        (ROOT / "panini-core/capabilities/browser/playwright/cdp-methods-1.63.0.json").read_text()
    )
    handled = {
        "Browser.getVersion",
        "Browser.setDownloadBehavior",
        "Target.setAutoAttach",
        "Target.createTarget",
        "Target.closeTarget",
        "Target.getTargetInfo",
        "Target.setDiscoverTargets",
    }
    missing = [
        m for m in recorded["methods"] if m not in handled and not decide(m, {"url": "https://a.test"}, {})[0]
    ]
    assert missing == [], f"Review these methods against I9 before allowing them: {missing}"


# Scoped model with a fake leg ----------------------------------------------------------
class FakeLeg:
    def __init__(self):
        self.sent, self.attached, self.detached = [], [], []

    async def attach(self, tab_id):
        self.attached.append(tab_id)
        return {"targetId": f"T{tab_id}", "type": "page", "url": "https://shop.example/"}

    async def send(self, tab_id, session_id, method, params):
        self.sent.append((tab_id, session_id, method))
        return {"ok": True}

    async def detach(self, tab_id):
        self.detached.append(tab_id)

    async def create_tab(self, url):
        return {"id": 99}

    async def close_tab(self, tab_id):
        self.sent.append((tab_id, None, "close"))


def test_the_model_only_knows_granted_tabs():
    async def scenario():
        emitted = []
        leg = FakeLeg()
        model = ScopedBrowserModel(leg, {"tabs": [7], "origin": "https://shop.example"}, emitted.append)
        await model.enable_auto_attach()
        assert leg.attached == [7] and emitted[-1]["method"] == "Target.attachedToTarget"
        model.on_debugger_event(8, None, "Page.loadEventFired", {})  # Not granted: dropped.
        model.on_debugger_event(7, None, "Target.attachedToTarget", {"sessionId": "child-1"})
        await model.send("child-1", "Runtime.evaluate", {})
        assert leg.sent[-1] == (7, "child-1", "Runtime.evaluate")
        await model.send(None, "Page.getFrameTree", {})  # Browser-level, allowed: routed via the granted tab.
        with pytest.raises(RelayClosed):
            await model.send(None, "Storage.getCookies", {})
        with pytest.raises(RelayClosed):
            await model.send("pw-tab-1", "Network.getAllCookies", {})
        assert not any(m in ("Storage.getCookies", "Network.getAllCookies") for _, _, m in leg.sent)
        with pytest.raises(RelayClosed):
            await model.create_target("https://x.example")  # No new tabs without a workspace that allows it.
        with pytest.raises(RelayClosed):
            await model.close_target("T7")  # PANINI never closes the person's tab.
        await model.on_tab_created({"id": 12, "opener_tab_id": 7})  # Popups need allow_new_tabs.
        assert 12 not in model.known_tabs
        model.on_debugger_detach(7)
        assert emitted[-1]["method"] == "Target.detachedFromTarget"

    asyncio.run(scenario())


def test_the_relay_accepts_one_authenticated_client():
    from websockets.asyncio.client import connect
    from websockets.exceptions import InvalidStatus

    async def scenario():
        relay = await CdpRelay(FakeLeg(), {"tabs": [1]}).start()
        try:
            with pytest.raises(InvalidStatus):
                async with connect(relay.endpoint):  # No bearer header.
                    pass
            with pytest.raises(InvalidStatus):
                async with connect(
                    relay.endpoint.replace(relay.token, "x" * 43),
                    additional_headers={"Authorization": f"Bearer {relay.token}"},
                ):
                    pass
            headers = {"Authorization": f"Bearer {relay.token}"}
            async with connect(relay.endpoint, additional_headers=headers) as ws:
                await ws.send(json.dumps({"id": 1, "method": "Browser.getVersion"}))
                reply = json.loads(await ws.recv())
                assert reply["result"]["product"].startswith("Chrome/PANINI")
                with pytest.raises(InvalidStatus):
                    async with connect(relay.endpoint, additional_headers=headers):
                        pass
        finally:
            await relay.close("test")

    asyncio.run(scenario())


# Bridge ---------------------------------------------------------------------------------
class FakeSocket:
    def __init__(self):
        self.frames = []

    async def send_json(self, frame):
        self.frames.append(frame)

    async def close(self, code=1000):
        pass


def test_the_bridge_negotiates_full_control_and_binds_every_frame():
    from backend.extension_bridge import ExtensionBridge, ExtensionUnavailable

    async def scenario():
        bridge = ExtensionBridge()
        socket = FakeSocket()
        await bridge.attach(socket, "dev")
        hello = {
            "type": "hello",
            "protocol": 4,
            "version": "4.0.0",
            "tab": 5,
            "attachment_id": "g1",
            "capabilities": ["cdp-v1"],
        }
        bridge.on_message(hello, socket)
        assert bridge.cdp_supported
        frames, scopes = [], []
        bridge.cdp_listeners.append(frames.append)
        bridge.scope_listeners.append(scopes.append)
        binding = bridge.binding()
        pending = asyncio.create_task(
            bridge.cdp("send", binding, tab_id=5, method="Runtime.evaluate", params={})
        )
        await asyncio.sleep(0)
        frame = socket.frames[-1]
        assert frame["action"] == {
            "kind": "cdp",
            "op": "send",
            "expected_tab": 5,
            "expected_attachment": "g1",
            "tab_id": 5,
            "method": "Runtime.evaluate",
            "params": {},
        }
        bridge.on_message({"type": "result", "id": frame["id"], "ok": True, "result": {"value": 1}}, socket)
        assert await pending == {"value": 1}
        bridge.on_message(
            {"type": "cdp_event", "tab_id": 5, "method": "Page.loadEventFired", "params": {}}, socket
        )
        assert frames[-1]["method"] == "Page.loadEventFired"
        bridge.on_message(
            {**hello, "tab": 6, "attachment_id": "g2"}, socket
        )  # The person attached another tab.
        assert scopes == ["scope_changed"]
        with pytest.raises(ExtensionUnavailable, match="changed"):
            await bridge.cdp("send", binding, tab_id=5, method="Runtime.evaluate", params={})
        old = {**hello, "capabilities": []}
        bridge.on_message(old, socket)
        assert not bridge.cdp_supported  # Claw v3 (no debugger permission): basic controls only.

    asyncio.run(scenario())


def test_the_chrome_hand_explains_what_is_missing(monkeypatch):
    from backend.hands.browser import ChromeBrowserHand

    monkeypatch.setenv("PANINI_CLAW_CDP", "true")
    bridge = SimpleNamespace(connected=True, cdp_supported=False, attached_tab=5)
    hand = ChromeBrowserHand(None, {})
    hand.bridge = lambda: bridge
    assert "v4" in asyncio.run(hand.readiness()).reason
    bridge.cdp_supported, bridge.attached_tab = True, None
    assert "Attach" in asyncio.run(hand.readiness()).reason
    monkeypatch.setenv("PANINI_CLAW_CDP", "false")
    assert asyncio.run(hand.readiness()).state == "unavailable"


# Real Playwright → relay → Edge ----------------------------------------------------------
needs_browser = pytest.mark.skipif(
    shutil.which("node") is None
    or not Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe").exists()
    or bool(os.getenv("PANINI_SKIP_BROWSER_TESTS")),
    reason="needs Node.js and Microsoft Edge",
)


def _serve():
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(ROOT / "frontend/public"))
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


async def _connected(url, tmp_path, caps="pdf,devtools,testing"):
    from chrome_leg import DirectChromeLeg

    from backend.hands.playwright_mcp import PlaywrightSession, chrome_preset

    leg = DirectChromeLeg()
    tab = await leg.start(url)
    origin = url.split("/", 3)
    relay = await CdpRelay(leg, {"tabs": [tab], "origin": f"{origin[0]}//{origin[2]}"}).start()
    leg.relay = relay
    session = PlaywrightSession(chrome_preset(relay, tmp_path, caps=caps))
    return leg, relay, session, tab


@needs_browser
def test_full_control_sends_trusted_input(tmp_path):
    from backend.hands.playwright_mcp import text_of

    server, base = _serve()

    async def scenario():
        leg, relay, session, _ = await _connected(f"{base}/trusted-input-demo.html", tmp_path)
        try:
            snap = text_of(await session.call("browser_snapshot", {}))
            lines = snap.splitlines()
            button = next(x.split("[ref=")[1].split("]")[0] for x in lines if 'button "Press me"' in x)
            field = next(x.split("[ref=")[1].split("]")[0] for x in lines if "textbox" in x)
            await session.call("browser_click", {"element": "Press me", "target": button})
            # slowly: real key events (a plain fill uses Input.insertText, which fires no keydown at all).
            await session.call(
                "browser_type", {"element": "field", "target": field, "text": "hi", "slowly": True}
            )
            click = await session.call("browser_verify_text_visible", {"text": "click trusted: yes"})
            key = await session.call("browser_verify_text_visible", {"text": "key trusted: yes"})
            return click, key
        finally:
            await session.close()
            await relay.close("done")
            await leg.close()

    try:
        click, key = asyncio.run(scenario())
        assert not click.get("isError") and not key.get("isError")
    finally:
        server.shutdown()


@needs_browser
def test_i9_playwright_cannot_read_cookies_beyond_the_tab(tmp_path):
    server, base = _serve()

    async def scenario():
        leg, relay, session, _ = await _connected(
            f"{base}/browser-demo.html", tmp_path, caps="pdf,devtools,testing,storage"
        )
        try:
            await session.call("browser_snapshot", {})
            result = await session.call("browser_cookie_list", {})
            return result, list(leg.sent)
        finally:
            await session.close()
            await relay.close("done")
            await leg.close()

    try:
        result, sent = asyncio.run(scenario())
        assert result.get("isError"), result
        assert not any(m in ("Storage.getCookies", "Network.getAllCookies") for m in sent)
    finally:
        server.shutdown()


@needs_browser
def test_detaching_stops_a_running_action_within_seconds(tmp_path):
    from backend.hands.playwright_mcp import BrowserDriverError

    server, base = _serve()

    async def scenario():
        leg, relay, session, tab = await _connected(f"{base}/browser-demo.html", tmp_path)
        try:
            await session.call("browser_snapshot", {})
            waiting = asyncio.create_task(
                session.call("browser_wait_for", {"text": "never appears"}, timeout_s=30)
            )
            await asyncio.sleep(1)
            started = time.monotonic()
            relay.on_detached(tab, "canceled_by_user")  # The person pressed Cancel on Chrome's bar.
            try:
                result = await waiting
                failed = bool(result.get("isError"))
            except BrowserDriverError:
                failed = True
            return failed, time.monotonic() - started, relay.closed_reason
        finally:
            await session.close()
            await relay.close("done")
            await leg.close()

    try:
        failed, seconds, reason = asyncio.run(scenario())
        assert failed and seconds < 5 and reason.startswith("detached")
    finally:
        server.shutdown()
