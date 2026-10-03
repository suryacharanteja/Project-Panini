"""V2-14 end to end through the Node reference relay: pair, ask, resolve an interrupt, stream a live frame.
Also invariant I6: the relay's log of the whole session contains no plaintext request paths or bodies."""

import asyncio
import json
import re
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest
import websockets
from fastapi import FastAPI

from backend import live
from backend.fabric import crypto, envelope
from backend.fabric.relay_client import RelayClient
from backend.fabric.router import decision_statement
from backend.fabric.router import router as remote_router
from backend.fabric.server import FabricServer
from backend.interrupts import Interrupts, InterruptSpec
from backend.routes import live as live_routes

from .conftest import paired

RELAY = Path(__file__).resolve().parents[3] / "panini-core/relay/node/server.mjs"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed.")


@pytest.fixture
def relay(tmp_path):
    log = tmp_path / "relay.log"
    proc = subprocess.Popen(["node", str(RELAY), "0"], stdout=subprocess.PIPE, text=True,
                            env={**__import__("os").environ, "RELAY_LOG": str(log)})  # fmt: skip
    port = int(re.search(r"listening on (\d+)", proc.stdout.readline()).group(1))
    yield f"127.0.0.1:{port}", log
    proc.kill()


def test_a_phone_works_through_the_blind_relay(fabric, relay, monkeypatch):
    from types import SimpleNamespace

    from backend import policy

    monkeypatch.setenv("PANINI_LIVE_VIEW", "true")
    devices, identity, _ = fabric
    address, log = relay
    app = FastAPI()
    app.include_router(remote_router)
    app.include_router(live_routes.remote_router)
    interrupts = Interrupts(devices.store)
    app.state.harness = SimpleNamespace(interrupts=interrupts, status=lambda: {"runs": 1},
                                        notices=SimpleNamespace(list=lambda: []))  # fmt: skip
    policy.install(policy.Policy())
    server = FabricServer(app, identity, devices).install()
    keys = paired(devices)
    card = interrupts.open(
        InterruptSpec("approval", "tool_call", "Pay", "Pay the invoice?", action={"t": 1}), run_id="r"
    )
    live.hub().publish(None, "sandbox", "panini", b"\xff\xd8sandbox-frame")

    async def scenario():
        channel = httpx.post(f"http://{address}/channels").json()
        client = RelayClient(f"ws://{address}", channel["channel_id"], channel["pc_secret"], server)
        client.start()
        await asyncio.wait_for(client.connected.wait(), 10)
        async with websockets.connect(f"ws://{address}/d/{channel['channel_id']}") as ws:
            hello, eph = envelope.hello(keys, identity.pc_id)
            await ws.send(json.dumps({"type": "hello", "message": hello}))
            welcome = json.loads(await ws.recv())
            session = envelope.finish(
                keys, eph, welcome["message"], identity.pc_id, identity.sign_pub, identity.agree_pub
            )

            async def call(method, path, body=None):
                await ws.send(
                    json.dumps(
                        {
                            "type": "env",
                            "envelope": session.seal("req", {"method": method, "path": path, "body": body}),
                        }
                    )
                )
                return session.open(json.loads(await ws.recv())["envelope"])[2]

            status = await call("GET", "/status")
            full = (await call("GET", f"/interrupts/{card['id']}"))["body"]
            statement = decision_statement(
                card["id"], full["nonce"], full["action_digest"], "cancelled", None
            )
            decided = await call("POST", f"/interrupts/{card['id']}/resolve", {
                "nonce": full["nonce"], "action_digest": full["action_digest"], "status": "cancelled",
                "signature": crypto.sign(keys.sign_key, statement)})  # fmt: skip
            frame = await call("GET", "/harness/live/sandbox/panini/latest.jpg")
            chrome = await call("GET", "/harness/live/chrome/attached/latest.jpg")
        await client.stop()
        return status, decided, frame, chrome

    status, decided, frame, chrome = asyncio.run(scenario())
    assert status["body"]["runs"] == 1
    assert decided["body"]["status"] == "cancelled"
    assert frame["body"]["base64"] and frame["body"]["content_type"] == "image/jpeg"
    assert chrome["status"] == 403  # Remote viewing of your Chrome is off by default (V2-12 grants).
    # I6: the relay saw only ciphertext.
    text = log.read_text()
    assert text and "/status" not in text and "interrupts" not in text and "Pay the invoice" not in text
    assert "sandbox-frame" not in text and card["id"] not in text
