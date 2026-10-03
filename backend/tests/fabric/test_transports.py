"""V2-14 transports: the gate keeps remote access off; LAN serves pairing and sessions; Tailscale needs the owner's
login header; the Tailscale driver configures Serve or reports needs_setup."""

import asyncio
import json
import socket

import httpx
import pytest
import websockets
from fastapi import FastAPI

from backend import v2flags
from backend.fabric import crypto, envelope
from backend.fabric.envelope import DeviceKeys
from backend.fabric.router import router as remote_router
from backend.fabric.runtime import Fabric
from backend.fabric.transports import listener
from backend.fabric.transports.tailscale import Tailscale

from .conftest import MemorySecrets


@pytest.fixture
def runtime(tmp_path, fabric, monkeypatch):
    devices, _, _ = fabric
    app = FastAPI()
    app.include_router(remote_router)
    from types import SimpleNamespace

    app.state.harness = SimpleNamespace(status=lambda: {"runs": 2})
    settings = tmp_path / "fabric.json"
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    settings.write_text(json.dumps({"port": port}))
    return Fabric(app, devices.store, MemorySecrets(), settings_path=settings), port


def gate(monkeypatch, passed):
    monkeypatch.setattr(
        v2flags,
        "redteam_gate",
        lambda path=None: {"passed": passed, "reason": "" if passed else "No red-team result yet."},
    )


def test_remote_access_stays_off_until_the_red_team_gate_passes(runtime, monkeypatch):
    fabric, _ = runtime
    monkeypatch.setenv("PANINI_REMOTE_MODE", "lan")
    gate(monkeypatch, False)
    state = asyncio.run(fabric.start())
    assert state["state"] == "blocked" and "red-team" in state["reason"] and fabric.listener is None


def test_lan_mode_pairs_a_phone_and_carries_a_session(runtime, monkeypatch):
    fabric, port = runtime
    monkeypatch.setenv("PANINI_REMOTE_MODE", "lan")
    gate(monkeypatch, True)

    async def scenario():
        state = await fabric.start()
        assert state["state"] == "ready"
        sign, agree = crypto.new_key(), crypto.new_key()
        code = fabric.devices.new_code()["code"]
        async with httpx.AsyncClient() as client:
            paired = (await client.post(f"http://127.0.0.1:{port}/fabric/pair", json={
                "code": code, "name": "Pixel", "sign_pub": crypto.public_raw(sign), "agree_pub": crypto.public_raw(agree)})).json()  # fmt: skip
        fabric.devices.confirm(paired["device_id"])
        keys = DeviceKeys(paired["device_id"], sign, agree)
        i = fabric.identity
        async with websockets.connect(f"ws://127.0.0.1:{port}/fabric") as ws:
            hello, eph = envelope.hello(keys, i.pc_id)
            await ws.send(json.dumps({"type": "hello", "message": hello}))
            session = envelope.finish(
                keys, eph, json.loads(await ws.recv())["message"], i.pc_id, i.sign_pub, i.agree_pub
            )
            await ws.send(
                json.dumps(
                    {"type": "env", "envelope": session.seal("req", {"method": "GET", "path": "/status"})}
                )
            )
            reply = session.open(json.loads(await ws.recv())["envelope"])[2]
        await fabric.stop()
        return reply

    assert asyncio.run(scenario())["body"]["runs"] == 2


def test_tailscale_mode_requires_the_owner_login_header():
    assert listener.allowed(
        "tailscale", "127.0.0.1", {"tailscale-user-login": "me@example.com"}, "me@example.com"
    )
    assert not listener.allowed(
        "tailscale", "127.0.0.1", {"tailscale-user-login": "x@example.com"}, "me@example.com"
    )
    assert not listener.allowed("tailscale", "127.0.0.1", {}, "me@example.com")
    assert not listener.allowed(
        "tailscale", "100.64.0.9", {"tailscale-user-login": "me@example.com"}, "me@example.com"
    )
    assert listener.allowed("lan", "192.168.1.20", {}, "") and not listener.allowed("lan", "8.8.8.8", {}, "")


def test_the_tailscale_driver_configures_serve_or_says_what_to_do():
    status = {"BackendState": "Running", "Self": {"DNSName": "pc.tail1.ts.net.", "UserID": 7},
              "User": {"7": {"LoginName": "me@example.com"}}}  # fmt: skip
    calls = []

    def run(args, timeout=30):
        calls.append(args[1:])
        if args[1] == "status":
            return 0, json.dumps(status)
        if args[1:3] == ["serve", "status"]:
            return 0, '{"Web":{"pc.tail1.ts.net:443":{"Handlers":{"/":{"Proxy":"http://127.0.0.1:3211"}}}}}'
        return 0, ""

    served = Tailscale(run=run, cli="tailscale").serve(3211)
    assert served["origin"] == "https://pc.tail1.ts.net" and served["owner_login"] == "me@example.com"
    assert ["serve", "--bg", "--https=443", "http://127.0.0.1:3211"] in calls
    assert Tailscale(run=run, cli=None).status()["state"] == "needs_setup"
    stopped = Tailscale(run=lambda a, timeout=30: (0, '{"BackendState":"Stopped"}'), cli="tailscale").serve(
        3211
    )
    assert stopped["state"] == "needs_setup" and "tailscale up" in stopped["reason"]
