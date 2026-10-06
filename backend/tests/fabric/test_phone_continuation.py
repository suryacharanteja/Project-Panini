"""V2-15 phone input: photos and files as untrusted artifacts, dictation, and continuing any owner conversation."""

import asyncio
import base64

import pytest
from fastapi import FastAPI
from panini_cli.agent.model import ScriptedModel

from backend.fabric import envelope
from backend.fabric.router import router as remote_router
from backend.fabric.server import FabricServer
from backend.harness.runtime import Harness
from backend.harness.store import Store

from .conftest import paired


@pytest.fixture
def phone(fabric, tmp_path, monkeypatch):
    devices, identity, _ = fabric
    model = ScriptedModel([{"text": "First answer."}, {"text": "Second answer."}])
    h = Harness(Store(tmp_path / "h.sqlite3"), model_factory=lambda: model)
    app = FastAPI()
    app.include_router(remote_router)
    app.state.harness = h
    # The fabric's device registry lives in the harness store for this test.
    devices.store = h.store
    server = FabricServer(app, identity, devices).install()
    keys = paired(devices)

    async def call(method, path, body=None):
        if not hasattr(call, "session"):
            hello, eph = envelope.hello(keys, identity.pc_id)
            reply = await server.handle("c", {"type": "hello", "message": hello})
            call.session = envelope.finish(
                keys, eph, reply["message"], identity.pc_id, identity.sign_pub, identity.agree_pub
            )
        sealed = call.session.seal("req", {"method": method, "path": path, "body": body})
        reply = await server.handle("c", {"type": "env", "envelope": sealed})
        return call.session.open(reply["envelope"])[2]

    yield h, call, model, app
    asyncio.run(h.close())


def test_photos_and_files_become_untrusted_artifacts_on_the_next_request(phone):
    h, call, _, _ = phone

    async def scenario():
        png = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 64).decode()
        uploaded = await call(
            "POST", "/attachments", {"name": "receipt.png", "mime": "image/png", "base64": png}
        )
        refused = await call(
            "POST", "/attachments", {"name": "a.exe", "mime": "application/x-msdownload", "base64": png}
        )
        asked = await call(
            "POST", "/ask", {"text": "File this receipt", "attachments": [uploaded["body"]["id"]]}
        )
        missing = await call("POST", "/ask", {"text": "x", "attachments": ["nope"]})
        return uploaded, refused, asked, missing

    uploaded, refused, asked, missing = asyncio.run(scenario())
    assert uploaded["status"] == 200 and uploaded["body"]["bytes"] == 72 and refused["status"] == 415
    assert missing["status"] == 404
    goal = h.tasks.get(asked["body"]["task_id"])["goal"]
    assert goal.startswith("File this receipt") and '<untrusted source="shared">' in goal
    assert uploaded["body"]["id"] in goal


def test_dictation_is_transcribed_on_the_pc(phone, monkeypatch):
    _, call, _, app = phone
    import backend.routes.speech as speech

    async def transcribe(data, request):
        assert len(data) >= 500
        return {"text": "remind me to call Asha"}

    monkeypatch.setattr(speech, "transcribe", transcribe)
    audio = base64.b64encode(b"\x1aE\xdf\xa3" + b"0" * 600).decode()
    reply = asyncio.run(call("POST", "/dictation", {"mime": "audio/webm", "base64": audio}))
    assert reply == {"status": 200, "body": {"text": "remind me to call Asha"}}
    assert asyncio.run(call("POST", "/dictation", {"mime": "text/plain", "base64": audio}))["status"] == 415


def test_a_phone_turn_continues_a_conversation_started_elsewhere(phone):
    h, call, model, _ = phone
    avatar_thread = h.threads.bind("avatar", "pc-voice-1", title="Trip planning", continue_recent=False)
    h.sessions.save(avatar_thread, [{"role": "user", "content": "Plan a trip to Lisbon"},
                                    {"role": "assistant", "content": "Here is a Lisbon plan."}])  # fmt: skip

    async def scenario():
        listed = await call("GET", "/conversations")
        asked = await call("POST", "/ask", {"text": "Add a day in Sintra", "thread_id": avatar_thread})
        stranger = await call("POST", "/ask", {"text": "x", "thread_id": "not-a-thread"})
        task = h.tasks.claim()
        await h.run_background(task, h.tasks)
        return listed, asked, stranger

    listed, asked, stranger = asyncio.run(scenario())
    conversation = next(c for c in listed["body"]["conversations"] if c["id"] == avatar_thread)
    assert conversation["surfaces"] == ["avatar"] and conversation["title"] == "Trip planning"
    assert asked["body"]["thread_id"] == avatar_thread and stranger["status"] == 404
    # The run saw the earlier avatar conversation: the phone continued it, not a fresh session.
    assert "Here is a Lisbon plan." in str(model.requests[-1]["items"])
    assert "Add a day in Sintra" in str(h.sessions.get(avatar_thread)["items"])
