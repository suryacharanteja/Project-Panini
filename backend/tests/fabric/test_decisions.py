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


def test_phone_reads_task_history_stops_exact_task_and_handles_missing_task(world):
    from backend.harness.tasks import Tasks

    app, devices, identity, _ = world
    tasks = Tasks(devices.store)
    app.state.harness.tasks = tasks
    task = tasks.submit("Prepare my status", origin="phone")
    other = tasks.submit("Keep this task", origin="user")

    async def scenario():
        phone = Phone(app, identity, paired(devices))
        await phone.connect()
        listing = await phone.call("GET", "/threads")
        assert len(listing["body"]["threads"]) == 2
        assert "lease_id" not in listing["body"]["threads"][0]
        detail = await phone.call("GET", f"/threads/{task['id']}")
        assert detail["body"]["thread"]["goal"] == "Prepare my status"
        assert detail["body"]["events"]
        stopped = await phone.call("POST", f"/threads/{task['id']}/stop")
        assert stopped["body"]["thread"]["status"] == "cancelled"
        assert tasks.get(other["id"])["status"] == "queued"
        assert (await phone.call("GET", "/threads/missing"))["status"] == 404
        assert (await phone.call("POST", "/threads/missing/stop"))["status"] == 404

    asyncio.run(scenario())


def test_task_history_requires_a_paired_session(world):
    from fastapi.testclient import TestClient

    app, _, _, _ = world
    client = TestClient(app)
    for method, path in [
        ("GET", "/personas"),
        ("GET", "/threads"),
        ("GET", "/threads/task"),
        ("POST", "/threads/task/stop"),
    ]:
        assert client.request(method, f"/api/remote{path}").status_code == 403


def test_phone_personas_are_presentation_only_and_messages_are_task_scoped(world, monkeypatch):
    from backend.harness.tasks import Tasks
    from backend.personas import Personas
    from backend.policy import Policy

    app, devices, identity, _ = world
    h = app.state.harness
    h.store = devices.store
    h.tasks = Tasks(devices.store)
    h.personas = Personas(
        devices.store, Policy(devices.store), identity_read=lambda: "PRIVATE", identity_write=lambda _: None
    )
    h.personas.create("scout", name="Scout", color="cyan", instructions="PRIVATE_INSTRUCTIONS")
    h.personas.bind_surface("phone_app", "scout")
    task = h.tasks.submit("@scout Research", origin="phone")
    for thread_id, text in [(task["id"], "Research result"), ("other-task", "PRIVATE_OTHER_TASK")]:
        devices.store.execute(
            "INSERT INTO persona_messages(thread_id,persona_id,kind,text,created) VALUES (?,?,?,?,?)",
            (thread_id, "scout", "assistant", text, 1),
        )
    monkeypatch.setenv("PANINI_PERSONAS", "true")

    async def scenario():
        phone = Phone(app, identity, paired(devices))
        await phone.connect()
        metadata = (await phone.call("GET", "/personas"))["body"]
        assert metadata["default_id"] == "scout"
        assert all(set(p) == {"id", "name", "color"} for p in metadata["personas"])
        detail = (await phone.call("GET", f"/threads/{task['id']}"))["body"]
        assert [m["text"] for m in detail["messages"]] == ["Research result"]
        assert detail["messages"][0]["persona"]["name"] == "Scout"
        h.personas.archive("scout")
        assert len((await phone.call("GET", "/personas"))["body"]["personas"]) == 1
        assert (await phone.call("GET", f"/threads/{task['id']}"))["body"]["messages"][0]["persona"][
            "name"
        ] == "Scout"
        monkeypatch.setenv("PANINI_PERSONAS", "false")
        assert (await phone.call("GET", "/personas"))["body"]["enabled"] is False
        assert (await phone.call("GET", f"/threads/{task['id']}"))["body"]["messages"] == []

    asyncio.run(scenario())
