"""V2-14 remote API over a fabric session: signed decisions, pause (I10), rate limits, immediate revocation."""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import FastAPI

from backend.fabric import crypto, envelope
from backend.fabric.router import decision_statement
from backend.fabric.router import router as remote_router
from backend.fabric.server import FabricServer
from backend.interrupts import Interrupts, InterruptSpec

from .conftest import paired


@pytest.fixture
def world(fabric, tmp_path):
    devices, identity, _ = fabric
    from backend import policy

    app = FastAPI()
    app.include_router(remote_router)
    interrupts = Interrupts(devices.store)
    app.state.harness = SimpleNamespace(
        interrupts=interrupts, status=lambda: {"runs": 0}, notices=SimpleNamespace(list=lambda: [])
    )
    app.state.fabric = FabricServer(app, identity, devices)
    policy.install(policy.Policy())
    return app, devices, identity, interrupts


class Phone:
    def __init__(self, app, identity, keys, conn="c1"):
        self.app, self.identity, self.keys, self.conn = app, identity, keys, conn
        self.session = None

    async def connect(self):
        hello, eph = envelope.hello(self.keys, self.identity.pc_id)
        reply = await self.app.state.fabric.handle(self.conn, {"type": "hello", "message": hello})
        assert reply["type"] == "welcome", reply
        i = self.identity
        self.session = envelope.finish(self.keys, eph, reply["message"], i.pc_id, i.sign_pub, i.agree_pub)

    async def call(self, method, path, body=None):
        sealed = self.session.seal("req", {"method": method, "path": path, "body": body})
        reply = await self.app.state.fabric.handle(self.conn, {"type": "env", "envelope": sealed})
        if reply["type"] != "env":
            return reply
        return self.session.open(reply["envelope"])[2]


def test_a_paired_phone_reads_status_and_resolves_an_approval_with_its_signature(world):
    app, devices, identity, interrupts = world
    keys = paired(devices)
    card = interrupts.open(
        InterruptSpec(
            "approval", "tool_call", "Send email", "Send it?", action={"tool": "send"}, level="tap"
        ),
        run_id="r1",
    )

    async def scenario():
        phone = Phone(app, identity, keys)
        await phone.connect()
        assert (await phone.call("GET", "/status"))["body"]["runs"] == 0
        full = (await phone.call("GET", f"/interrupts/{card['id']}"))["body"]
        decision = {"nonce": full["nonce"], "action_digest": full["action_digest"], "status": "resolved",
                    "payload": {"approved": True}}  # fmt: skip
        forged = dict(decision, signature=crypto.sign(crypto.new_key(), b"x"))
        assert (await phone.call("POST", f"/interrupts/{card['id']}/resolve", forged))["status"] == 403
        statement = decision_statement(
            card["id"], full["nonce"], full["action_digest"], "resolved", {"approved": True}
        )
        signed = dict(decision, signature=crypto.sign(keys.sign_key, statement))
        return await phone.call("POST", f"/interrupts/{card['id']}/resolve", signed)

    reply = asyncio.run(scenario())
    assert reply["status"] == 200 and reply["body"]["status"] == "resolved"
    stored = interrupts.get(card["id"], include_secret=True)["decided_by"]
    assert stored["surface"] == "phone_app" and stored["device_id"] == keys.device_id and stored["signature"]


def test_a_phone_can_always_pause_panini(world):
    from backend import policy

    app, devices, identity, _ = world

    async def scenario():
        phone = Phone(app, identity, paired(devices))
        await phone.connect()
        return await phone.call("POST", "/pause")

    assert asyncio.run(scenario())["body"] == {"paused": True} and policy.current().paused()


def test_revocation_closes_open_sessions_at_once(world):
    app, devices, identity, _ = world
    keys = paired(devices)

    async def scenario():
        phone = Phone(app, identity, keys)
        await phone.connect()
        assert (await phone.call("GET", "/devices/self"))["body"]["status"] == "active"
        app.state.fabric.revoke(keys.device_id)
        refused = await phone.call("GET", "/status")
        hello, _ = envelope.hello(keys, identity.pc_id)
        rejoin = await app.state.fabric.handle("c2", {"type": "hello", "message": hello})
        return refused, rejoin

    refused, rejoin = asyncio.run(scenario())
    assert refused["type"] == "error" and rejoin["type"] == "error"


def test_writes_are_rate_limited_per_device(world):
    app, devices, identity, _ = world

    async def scenario():
        phone = Phone(app, identity, paired(devices))
        await phone.connect()
        statuses = [(await phone.call("POST", "/pause"))["status"] for _ in range(31)]
        return statuses

    statuses = asyncio.run(scenario())
    assert statuses[:30] == [200] * 30 and statuses[30] == 429


def test_nothing_reaches_the_remote_routes_without_a_session(world):
    from fastapi.testclient import TestClient

    app, _, _, _ = world
    client = TestClient(app)
    assert client.get("/api/remote/status").status_code == 403
    assert (
        client.get(
            "/api/remote/status", headers={"x-panini-fabric": "guess", "x-panini-device": "x"}
        ).status_code
        == 403
    )
