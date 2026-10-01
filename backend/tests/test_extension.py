import asyncio
import secrets
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.app import create_app
from backend.extension_bridge import ExtensionBridge, ExtensionUnavailable
from backend.extension_loop import ExtensionLoop
from backend.extension_pairing import PairingError, PairingStore, proof


def pair(app, origin="chrome-extension://claw"):
    code = app.state.pairing.create_code()["code"]
    return app.state.pairing.redeem(code, origin)


def handshake(ws, device, *, tab=7, client_proof=None):
    """Run the extension side of the mutual handshake; return the server's reply."""
    nonce = secrets.token_urlsafe(32)
    ws.send_json(
        {
            "type": "hello",
            "source": "jev-rev-browxtension",
            "protocol": 4,
            "version": "3.0.0",
            "device_id": device["device_id"],
            "nonce": nonce,
            "tab": tab,
            "attachment_id": "chosen-tab",
        }
    )
    challenge = ws.receive_json()
    assert challenge["type"] == "challenge"
    # The extension verifies the server before it will accept any action.
    assert challenge["proof"] == proof(device["secret"], "server", nonce, challenge["nonce"])
    ws.send_json(
        {
            "type": "auth",
            "proof": client_proof or proof(device["secret"], "client", challenge["nonce"], nonce),
        }
    )
    return ws.receive_json()


def rejected(client, send, headers=None):
    with client.websocket_connect("ws://localhost/ws/extension", headers=headers or {}) as ws:
        with pytest.raises(WebSocketDisconnect) as error:
            send(ws)
            while True:
                ws.receive_json()
        return error.value


def test_only_a_paired_mutually_authenticated_extension_can_connect():
    app = create_app()
    client = TestClient(app, base_url="http://localhost")
    device = pair(app)

    def status():
        return client.get("/api/browser/extension/status").json()

    stranger = {"device_id": secrets.token_hex(16), "secret": secrets.token_hex(32)}
    hello = {"type": "hello", "source": "jev-rev-browxtension", "protocol": 4, "version": "3.0.0"}
    # No pairing, legacy protocol, or a missing nonce: refused before any challenge.
    for message in (
        {**hello, "device_id": stranger["device_id"], "nonce": secrets.token_urlsafe(32)},
        {**hello, "protocol": 3, "device_id": device["device_id"], "nonce": secrets.token_urlsafe(32)},
        {**hello, "device_id": device["device_id"]},
        [],
    ):
        assert rejected(client, lambda ws, m=message: ws.send_json(m)).code == 1008
    # A client that knows the device ID but not the secret fails the proof.
    assert rejected(client, lambda ws: handshake(ws, device, client_proof="0" * 64)).code == 1008
    # A different extension origin cannot use this device's pairing.
    other = {"origin": "chrome-extension://other"}
    assert rejected(client, lambda ws: handshake(ws, device), other).code == 1008
    assert not status()["connected"]
    with client.websocket_connect(
        "ws://localhost/ws/extension", headers={"origin": "chrome-extension://claw"}
    ) as current:
        assert handshake(current, device)["type"] == "connected"
        before = status()
        assert before["connected"] and before["paired"] and before["compatible"]
        # A second paired browser cannot displace the connected one.
        second = pair(app)
        assert rejected(client, lambda ws: handshake(ws, second, tab=9)).code == 1008
        assert status() == before
    assert not status()["connected"]


def test_revoking_a_pairing_disconnects_that_extension():
    app = create_app()
    client = TestClient(app, base_url="http://localhost")
    device = pair(app)
    with client.websocket_connect("ws://localhost/ws/extension") as ws:
        assert handshake(ws, device)["type"] == "connected"
        assert client.delete(f"/api/extension/devices/{device['device_id']}").json() == {"status": "revoked"}
        with pytest.raises(WebSocketDisconnect) as error:
            ws.send_json({"type": "result", "id": "x", "ok": True})
            ws.receive_json()
        assert error.value.code == 1008
    assert not client.get("/api/browser/extension/status").json()["connected"]


def test_pairing_codes_are_one_time_expiring_and_rate_limited(tmp_path):
    now = [1000.0]
    store = PairingStore(tmp_path / "pairings.json", clock=lambda: now[0])
    code = store.create_code()["code"]
    device = store.redeem(code.lower().replace("-", " "), "chrome-extension://claw")
    assert store.secret(device["device_id"]) == device["secret"]
    # Persisted for the next launch, and the code cannot be reused.
    assert PairingStore(tmp_path / "pairings.json").secret(device["device_id"]) == device["secret"]
    with pytest.raises(PairingError):
        store.redeem(code)
    expired = store.create_code()["code"]
    now[0] += 301
    with pytest.raises(PairingError):
        store.redeem(expired)
    pending = store.create_code()["code"]
    # The failure window has moved past the earlier guess; nine more reach the limit of ten.
    for _ in range(9):
        with pytest.raises(PairingError):
            store.redeem("WRONGCODE")
    # Too many guesses burn every outstanding code, including the real one.
    with pytest.raises(PairingError, match="Too many"):
        store.redeem(pending)


def test_pairing_endpoint_accepts_extension_origins_only_for_redemption():
    app = create_app(console_token="t" * 43)
    client = TestClient(app, base_url="http://localhost")
    extension = {"origin": "chrome-extension://claw"}
    # Creating a code is a console (or PANINI shell) action.
    assert client.post("/api/extension/pairing-code").status_code == 401
    code = client.post("/api/extension/pairing-code", headers={"x-panini-console": "t" * 43}).json()["code"]
    assert (
        client.post("/api/extension/pair", json={"code": "ABCD-EFGH"}, headers=extension).status_code == 403
    )
    paired = client.post("/api/extension/pair", json={"code": code}, headers=extension).json()
    assert len(paired["secret"]) == 64 and paired["protocol"] == 4
    # Other cross-site origins remain blocked.
    evil = {"origin": "https://evil.test"}
    assert client.post("/api/extension/pair", json={"code": code}, headers=evil).status_code == 403


def test_previous_socket_cannot_detach_or_change_new_connection():
    async def run():
        bridge = ExtensionBridge()
        old, current = object(), object()
        await bridge.attach(old)
        await bridge.attach(current)
        bridge.on_message({"type": "hello", "tab": 7}, current)
        await bridge.detach(socket=old)
        bridge.on_message({"type": "hello", "tab": 9}, old)
        assert bridge.connected and bridge.attached_tab == 7
        bridge.on_message({"type": "hello", "tab": None}, current)
        assert bridge.attached_tab is None

    asyncio.run(run())


def test_stop_interrupts_pending_extension_work_and_releases_slot(monkeypatch):
    async def run():
        entered = asyncio.Event()
        bridge = ExtensionBridge()
        await bridge.attach(object())
        bridge.on_message({"type": "hello", "tab": 7})
        loop = ExtensionLoop(bridge)

        async def events(*args):
            yield {"event": "initialized"}
            entered.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(loop, "_run_events", events)
        stream = loop.run_instruction("test", max_steps=None, timeout_seconds=None)
        assert (await anext(stream))["event"] == "initialized"
        await entered.wait()
        loop.cancel()
        assert (await anext(stream))["event"] == "cancelled"
        await stream.aclose()
        assert not loop.loop_active

    asyncio.run(run())


def test_extension_deadline_is_enforced_during_pending_work(monkeypatch):
    async def run():
        bridge = ExtensionBridge()
        await bridge.attach(object())
        bridge.on_message({"type": "hello", "tab": 7})
        loop = ExtensionLoop(bridge)

        async def events(*args):
            yield {"event": "initialized"}
            await asyncio.Event().wait()

        monkeypatch.setattr(loop, "_run_events", events)
        events = [event async for event in loop.run_instruction("test", timeout_seconds=0.01)]
        assert events[-1]["event"] == "timeout"
        assert not loop.loop_active

    asyncio.run(run())


def test_http_status_stop_and_reset_include_extension():
    app = create_app()
    calls = []
    loop = SimpleNamespace(
        loop_active=True,
        instruction_history=["old"],
        cancel=lambda: calls.append("stop"),
        get_stats=lambda: {"mode": "extension"},
    )
    app.state.extension_loop = loop
    client = TestClient(app, base_url="http://localhost")
    assert client.get("/api/browser/status?mode=extension").json() == {
        "active": True,
        "stats": {"mode": "extension"},
    }
    assert client.post("/api/browser/reset").status_code == 409
    assert client.post("/api/browser/stop").json()["status"] == "stopping"
    assert calls == ["stop"]
    loop.loop_active = False
    assert client.post("/api/browser/reset").status_code == 200
    assert not loop.instruction_history


def test_extension_requires_attached_tab():
    app = create_app()
    app.state.extension_bridge = SimpleNamespace(connected=True, attached_tab=None)
    client = TestClient(app, base_url="http://localhost")
    assert (
        client.post("/api/browser/run-stream", json={"instruction": "test", "mode": "extension"}).status_code
        == 409
    )


def test_browser_requires_explicit_mode_and_current_extension_target():
    app = create_app()
    bridge = ExtensionBridge()
    app.state.extension_bridge = bridge
    asyncio.run(bridge.attach(object()))
    bridge.on_message(
        {"type": "hello", "tab": 7, "protocol": 4, "version": "3.0.0", "attachment_id": "grant"}
    )
    client = TestClient(app, base_url="http://localhost")
    assert client.post("/api/browser/run-stream", json={"instruction": "test"}).status_code == 422
    for target in ("", "previous-target"):
        response = client.post(
            "/api/browser/run-stream",
            json={
                "instruction": "test",
                "mode": "extension",
                "target_id": target,
            },
        )
        assert response.status_code == 409
        assert app.state.browser_loop is None
    bridge.protocol = 0
    response = client.post(
        "/api/browser/run-stream",
        json={
            "instruction": "test",
            "mode": "extension",
            "target_id": bridge.target_id,
        },
    )
    assert response.status_code == 409
    assert "Reload" in response.json()["detail"]


def test_old_extension_is_connected_but_cannot_run_new_actions():
    bridge = ExtensionBridge()
    asyncio.run(bridge.attach(object()))
    bridge.on_message({"type": "hello", "tab": 7, "protocol": 2, "version": "1.2.0"})
    assert bridge.status()["connected"] and not bridge.status()["compatible"]
    app = create_app()
    app.state.extension_bridge = bridge
    client = TestClient(app, base_url="http://localhost")
    response = client.post(
        "/api/browser/run-stream",
        json={
            "instruction": "open AI jobs search",
            "mode": "extension",
            "target_id": bridge.target_id,
        },
    )
    assert response.status_code == 409
    assert "Reload" in response.json()["detail"]
    bridge.protocol = 4
    for version in ("3", "3.0", "3.0.0", "3.1.2"):
        bridge.version = version
        assert bridge.compatible
    for version in ("2.9.9", "invalid"):
        bridge.version = version
        assert not bridge.compatible


def test_bound_instruction_never_follows_a_changed_attachment():
    async def run():
        bridge = ExtensionBridge()
        sent = []

        class Socket:
            async def send_json(self, message):
                sent.append(message)
                bridge.on_message({"type": "result", "id": message["id"], "ok": True, "result": {}})

        await bridge.attach(Socket())
        bridge.on_message({"type": "hello", "tab": 7, "attachment_id": "first", "protocol": 2})
        original = bridge.target_id
        with bridge.bind_target(original):
            await bridge.snapshot()
            assert sent[0]["action"]["expected_tab"] == 7
            assert sent[0]["action"]["expected_attachment"] == "first"
            bridge.on_message({"type": "hello", "tab": 7, "attachment_id": "first", "tab_title": "New page"})
            assert bridge.target_id == original
            # Reattaching even the same tab creates a different grant.
            bridge.on_message({"type": "hello", "tab": 7, "attachment_id": "second"})
            with pytest.raises(ExtensionUnavailable, match="changed"):
                await bridge.act("e1", "CLICK")
        assert len(sent) == 1

    asyncio.run(run())


def test_navigation_retries_only_snapshot_and_never_changed_attachment(monkeypatch):
    async def run():
        bridge = ExtensionBridge()
        calls = []

        async def call(kind, **args):
            calls.append(kind)
            if len(calls) < 3:
                raise ExtensionUnavailable("Could not establish connection. Receiving end does not exist.")
            return {"url": "https://example.test/receipt", "text": "Submitted"}

        monkeypatch.setattr(bridge, "call", call)
        assert (await bridge.snapshot())["text"] == "Submitted"
        assert calls == ["snapshot"] * 3

        async def changed(kind, **args):
            calls.append(kind)
            raise ExtensionUnavailable("Attached tab changed. Review the target and run again.")

        monkeypatch.setattr(bridge, "call", changed)
        with pytest.raises(ExtensionUnavailable, match="Attached tab changed"):
            await bridge.snapshot()
        assert len(calls) == 4

    asyncio.run(run())
