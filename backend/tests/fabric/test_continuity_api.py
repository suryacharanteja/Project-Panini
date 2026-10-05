import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from backend.brain.threads import Threads
from backend.fabric.router import router as remote_router
from backend.fabric.routing import Continuity
from backend.fabric.server import FabricServer
from backend.harness.proactive import Notices
from backend.harness.sessions import Sessions
from backend.interrupts import Interrupts
from backend.panini.contracts import Fault
from backend.panini.coordinator import Coordinator
from backend.panini.settings import Settings
from backend.routes.harness import shell_router
from backend.routes.panini import router as panini_router

from .conftest import paired
from .test_decisions import Phone


@pytest.fixture
def api(fabric, monkeypatch):
    monkeypatch.setenv("PANINI_PRESENCE", "true")
    devices, identity, _ = fabric
    store = devices.store
    h = SimpleNamespace(
        store=store, sessions=Sessions(store), interrupts=Interrupts(store), notices=Notices(store)
    )
    h.threads = Threads(store, h.sessions)
    h.continuity = Continuity(h)
    app = FastAPI()

    @app.exception_handler(Fault)
    async def fault(_request, error):
        return JSONResponse(error.payload("test"), error.status)

    app.include_router(remote_router)
    app.include_router(panini_router)
    app.include_router(shell_router)
    app.state.fabric = FabricServer(app, identity, devices)
    app.state.harness = h
    h.continuity.app = app
    settings = Settings(enabled=True, bootstrap_token="b" * 40)
    app.state.panini = Coordinator(settings, {"agent": SimpleNamespace(sessions={})})
    client = TestClient(app)
    registered = client.post(
        "/api/panini/clients", headers={"authorization": "Bearer " + settings.bootstrap_token}, json={}
    ).json()
    headers = {"authorization": "Bearer " + registered["token"], "x-panini-instance": settings.instance_id}
    session = client.post("/api/panini/sessions", headers=headers, json={"command_id": str(uuid4())}).json()
    sid = session["session_id"]
    lease = client.post(
        f"/api/panini/sessions/{sid}/lease/acquire", headers=headers, json={"command_id": str(uuid4())}
    ).json()
    headers["x-panini-lease-epoch"] = str(lease["lease_epoch"])
    return app, client, headers, sid, devices, identity


def test_encrypted_phone_heartbeat_handoff_and_revocation(api):
    app, _, _, _, devices, identity = api
    thread = app.state.harness.sessions.create("Original owner conversation")

    async def scenario():
        keys = paired(devices)
        phone = Phone(app, identity, keys)
        await phone.connect()
        signal = await phone.call("POST", "/presence", {"foreground": True})
        assert signal["status"] == 200
        assert signal["body"]["signals"][0]["source"] == "phone:" + keys.device_id
        assert (await phone.call("POST", "/presence", {"foreground": True, "timestamp": 1}))["status"] == 422
        assert (await phone.call("POST", "/presence/pc", {"idle_s": 0}))["status"] == 404
        assert (await phone.call("GET", "/presence/commands"))["status"] == 404
        handoff = await phone.call("POST", "/presence/handoff", {"thread_id": thread, "destination": "pc"})
        assert handoff["status"] == 200 and handoff["body"]["thread_id"] == thread
        assert (await phone.call("POST", "/presence/handoff", {"thread_id": "missing", "destination": "pc"}))[
            "status"
        ] == 404
        app.state.fabric.revoke(keys.device_id)
        assert (await phone.call("GET", "/presence"))["type"] == "error"

    asyncio.run(scenario())


def test_native_handoff_requires_controller_lease_and_restores_exact_agent_thread(api):
    app, client, headers, sid, _, _ = api
    h = app.state.harness
    thread = h.sessions.create("From the phone")
    h.sessions.save(thread, [{"role": "user", "content": "Preserved turn"}])
    seq = h.continuity.handoff(thread, "pc")["command_seq"]
    path = "/api/panini/harness/presence/handoff/accept"
    payload = {"seq": seq, "session_id": sid}
    assert client.post(path, json=payload).status_code == 401
    bad_lease = {**headers, "x-panini-lease-epoch": "999"}
    assert client.post(path, headers=bad_lease, json=payload).status_code == 409
    observer, _ = app.state.panini.auth.issue("observer", session_id=sid)
    assert (
        client.get(
            "/api/panini/harness/presence/commands",
            headers={**headers, "authorization": "Bearer " + observer},
        ).status_code
        == 403
    )
    accepted = client.post(path, headers=headers, json=payload)
    assert accepted.status_code == 200 and "Preserved turn" in accepted.json()["transcript"]
    assert app.state.panini.adapters["agent"].sessions[sid] == thread
    assert (
        h.store.one("SELECT thread_id FROM thread_bindings WHERE surface='avatar' AND external_id=?", (sid,))[
            "thread_id"
        ]
        == thread
    )
    assert len(h.sessions.list()) == 1
    # Retry restores the same thread, never creates a duplicate.
    assert client.post(path, headers=headers, json=payload).json()["thread_id"] == thread


def test_handoff_cannot_change_an_active_task_or_voice_history(api):
    app, client, headers, sid, _, _ = api
    h, coordinator = app.state.harness, app.state.panini
    target = h.sessions.create("Phone")
    seq = h.continuity.handoff(target, "pc")["command_seq"]
    current = coordinator.get(sid)
    for field in ("active", "media"):
        setattr(current, field, object())
        result = client.post(
            "/api/panini/harness/presence/handoff/accept",
            headers=headers,
            json={"seq": seq, "session_id": sid},
        )
        assert result.status_code == 409
        assert sid not in coordinator.adapters["agent"].sessions
        setattr(current, field, None)


def test_agui_alias_handoff_uses_actual_thread_and_does_not_rebind_a_collision(api):
    app, client, headers, _, _, _ = api
    h = app.state.harness
    target = h.threads.bind("agui", "client-conversation")
    result = client.post(
        "/api/panini/harness/presence/handoff",
        headers=headers,
        json={"thread_id": "client-conversation", "destination": "pc"},
    )
    assert result.status_code == 200 and result.json()["thread_id"] == target
    other = h.sessions.create("Other")
    with pytest.raises(ValueError):
        h.threads.attach("agui", target, other)


def test_verified_source_call_drains_before_pc_handoff_is_published(api):
    app, client, headers, _, _, _ = api
    h = app.state.harness
    thread = h.sessions.create("Owner call")
    call = SimpleNamespace(thread=thread, verified=False, caller={"kind": "owner"})

    async def end(**_):
        assert h.continuity.commands() == []
        await asyncio.sleep(0)
        app.state.phone_line.active = None

    app.state.phone_line = SimpleNamespace(active=call, end=AsyncMock(side_effect=end))
    payload = {"thread_id": thread, "destination": "pc"}
    path = "/api/panini/harness/presence/handoff"
    assert client.post(path, headers=headers, json=payload).status_code == 409
    assert not h.continuity.commands()
    call.verified = True
    assert client.post(path, headers=headers, json=payload).status_code == 200
    assert app.state.phone_line.end.await_count == 1
    assert h.continuity.commands()[0]["data"]["thread_id"] == thread


def test_disabled_presence_does_not_collect_pc_signals_or_attach_new_bindings(api, monkeypatch):
    app, client, headers, _, _, _ = api
    monkeypatch.setenv("PANINI_PRESENCE", "false")
    pc = {"idle_s": 0, "locked": False, "suspended": False, "avatar_visible": True}
    assert client.post("/api/panini/harness/presence/pc", headers=headers, json=pc).json() == {
        "enabled": False
    }
    assert app.state.harness.store.all("SELECT source FROM presence_signals") == []
    thread = app.state.harness.sessions.create("Original")
    result = client.post(
        "/api/panini/harness/presence/handoff",
        headers=headers,
        json={"thread_id": thread, "destination": "pc"},
    )
    assert result.status_code == 409
    assert app.state.harness.threads.bindings(thread) == []
