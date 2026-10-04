import asyncio
import base64
import hashlib
import hmac
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.brain.threads import Threads
from backend.fabric.ingress import Ingress
from backend.harness.proactive import Notices
from backend.harness.sessions import Sessions
from backend.harness.store import Store
from backend.interrupts.service import Interrupts
from backend.ledger.artifacts import Artifacts
from backend.ledger.receipts import Ledger
from backend.personas import Personas
from backend.policy.engine import Policy
from backend.telephony.callers import from_headers
from backend.telephony.connection import PhoneConnection
from backend.telephony.inbound import CallService

NUMBER = "+14155550123"
HEADERS = [{"name": "From", "value": f"Owner <sip:{NUMBER}@trunk.example>"}]


@pytest.fixture
def anyio_backend():
    return "asyncio"


class Connection:
    def __init__(self, key, event, request, *, on_digits, model, voice):
        self.event, self.request, self.on_digits = event, request, on_digits
        self.attach, self.close, self.speak, self.send = AsyncMock(), AsyncMock(), AsyncMock(), AsyncMock()
        self.voice, self.usage = voice, {"seconds": 3}


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("PANINI_PHONE_LINE", "true")
    monkeypatch.setenv("PANINI_REDTEAM_ENFORCE", "false")
    store = Store(tmp_path / "calls.sqlite3")
    h = SimpleNamespace(
        store=store,
        policy=Policy(store),
        sessions=Sessions(store),
        interrupts=Interrupts(store),
        artifacts=Artifacts(store, tmp_path / "artifacts"),
        ledger=Ledger(store),
        notices=Notices(store),
    )
    h.personas = Personas(
        store, h.policy, identity_read=lambda: "PRIVATE OWNER IDENTITY", identity_write=lambda text: None
    )
    h.threads = Threads(store, h.sessions)
    service = CallService(h, provider=AsyncMock(), connection_factory=Connection)
    service.callers.put(NUMBER, "Owner", "123456")
    yield service, h
    store.close()


def test_caller_id_is_metadata_and_pin_is_salted(world):
    service, h = world
    caller = service.callers.get(NUMBER)
    assert service.callers.verify(caller, "123456")
    assert not service.callers.verify(caller, "654321")
    assert "123456" not in json.dumps(service.status())
    assert caller["pin_hash"] != "123456"
    service.callers.put("+14155550124", "Second", "123456")
    assert caller["pin_hash"] != service.callers.get("+14155550124")["pin_hash"]
    assert from_headers(HEADERS) == NUMBER
    assert from_headers([{"name": "From", "value": f"{NUMBER} <sip:attacker@host>"}]) is None
    assert from_headers(HEADERS + HEADERS) is None
    with pytest.raises(ValueError):
        service.callers.put(NUMBER, "Owner", "1234")
    with pytest.raises(ValueError):
        service.callers.put(NUMBER, "Contact", "123456", kind="contact")


@pytest.mark.parametrize(
    "method,path", [("GET", "/calls"), ("POST", "/calls/session/end"), ("POST", "/calls/session/verify")]
)
def test_call_controls_require_authenticated_fabric(method, path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.fabric.router import router

    class UnauthenticatedFabric:
        def verify(self, headers):
            raise PermissionError("A paired device session is required.")

    app = FastAPI()
    app.state.fabric = UnauthenticatedFabric()
    app.include_router(router)
    with TestClient(app) as client:
        assert client.request(method, "/api/remote" + path).status_code == 403
    assert not hasattr(app.state, "phone_line")


async def pin(service, digits="123456#"):
    for digit in digits:
        await service.digits(service.active, digit)


@pytest.mark.anyio
async def test_accept_verify_dedupe_and_receipt(world):
    service, h = world
    await service.incoming("sess_1", HEADERS)
    call = service.active
    await service.incoming("sess_1", HEADERS)
    assert service.provider.await_count == 1
    config = service.provider.call_args.args[2]["session"]
    assert config["audio"]["output"]["voice"] == "marin"
    assert "PRIVATE OWNER IDENTITY" not in config["instructions"]
    call.connection.speak.assert_awaited_once()
    assert "AI assistant" in call.connection.speak.call_args.args[0]
    service.compute = AsyncMock(return_value={"outcome": "reply", "message": "Actual backend reply"})
    await service.request(call, "yes", "unverified")
    service.compute.assert_not_awaited()
    assert not call.verified
    await pin(service)
    assert call.verified and call.digits == ""
    await service.event(call, {"type": "transcript", "role": "user", "delta": "Hello"})
    await service.request(call, "Find papers", "delegation_1")
    await service.request(call, "Find papers", "delegation_1")
    service.compute.assert_awaited_once()
    await service.end(call)
    await service.end(call)
    row = h.store.one("SELECT * FROM calls WHERE id='sess_1'")
    assert row["state"] == "ended" and row["receipt_id"] and row["transcript_artifact_id"]
    assert h.store.one("SELECT COUNT(*) AS n FROM receipts")["n"] == 1
    transcript = h.artifacts.get(row["transcript_artifact_id"])[1]
    assert b"123456" not in transcript and b"Hello" in transcript
    assert service.active is None


@pytest.mark.anyio
async def test_receipt_failure_keeps_duration_and_releases_live_slot(world, monkeypatch):
    service, h = world
    await service.incoming("receipt_failure", HEADERS)
    call = service.active
    call.started -= 10

    def storage_failure(*args, **kwargs):
        raise OSError("Artifact storage unavailable")

    monkeypatch.setattr(h.artifacts, "put", storage_failure)
    with pytest.raises(OSError, match="storage unavailable"):
        await service.end(call)
    assert service.active is None
    call.connection.close.assert_awaited_once()
    row = h.store.one("SELECT * FROM calls WHERE id='receipt_failure'")
    assert row["state"] == "failed" and row["duration_s"] >= 10
    assert row["receipt_id"] is None and "incomplete" in row["reason"]


@pytest.mark.anyio
async def test_unknown_blocked_busy_and_daily_cap(world, monkeypatch):
    service, h = world
    await service.incoming("unknown", [])
    assert service.provider.call_args.args == ("unknown", "reject", {"status_code": 603})
    assert service.active is None
    await service.incoming("one", HEADERS)
    await service.incoming("two", HEADERS)
    assert service.provider.call_args.args == ("two", "reject", {"status_code": 486})
    await service.end()
    monkeypatch.setenv("PANINI_CALLS_PER_DAY", "1")
    await service.incoming("three", HEADERS)
    assert service.active is None
    assert "limit" in h.store.one("SELECT reason FROM calls WHERE id='three'")["reason"]


@pytest.mark.anyio
async def test_call_duration_cannot_exceed_remaining_daily_minutes(world):
    service, h = world
    h.store.execute(
        "INSERT INTO calls(id,number,persona_id,state,started,duration_s) VALUES ('previous',?,'panini','ended',?,?)",
        (NUMBER, service.clock(), 118 * 60),
    )
    service.expire = AsyncMock()
    await service.incoming("remaining", HEADERS)
    await asyncio.sleep(0)
    assert service.expire.await_args_list[0].args[1] == 120
    await service.end()


@pytest.mark.anyio
async def test_pin_attempt_limit_and_revocation(world):
    service, h = world
    await service.incoming("bad_pin", HEADERS)
    call = service.active
    for _ in range(3):
        await pin(service, "000000#")
    await asyncio.sleep(0.01)
    assert call.closing
    assert not h.store.one("SELECT verified FROM calls WHERE id='bad_pin'")["verified"]
    await service.incoming("revoked", HEADERS)
    call = service.active
    service.callers.remove(NUMBER)
    await asyncio.sleep(0.6)
    assert call.closing and service.active is None


@pytest.mark.anyio
async def test_phone_confirmation_is_independent_of_speech_and_keypad(world):
    service, h = world
    await service.incoming("confirm", HEADERS)
    call = service.active
    service.request_verification()
    await asyncio.sleep(0)
    iid = next(iter(call.pending))
    await service.request(call, "approve this is me", "speech")
    assert not call.verified
    full = h.interrupts.get(iid, include_secret=True)
    h.interrupts.resolve(
        iid,
        nonce=full["nonce"],
        action_digest=full["action_digest"],
        status="resolved",
        payload={"approved": True},
        decided_by={"surface": "avatar", "method": "local"},
    )
    await asyncio.sleep(0)
    assert call.verified
    await service.end()


@pytest.mark.anyio
async def test_signed_webhook_and_cross_delivery_call_dedupe(world):
    service, h = world
    secret = "whsec_" + base64.b64encode(b"k" * 32).decode()
    ingress = Ingress(h.store, {"phone": {"kind": "openai", "path_secret": "path", "secret": secret}})
    ingress.on("openai", service.webhook)
    body = json.dumps(
        {
            "type": "live.transport.incoming",
            "data": {"type": "sip", "session_id": "signed", "sip_headers": HEADERS},
        }
    ).encode()

    def delivery(did):
        stamp = str(int(ingress.clock()))
        signature = base64.b64encode(
            hmac.new(b"k" * 32, f"{did}.{stamp}.".encode() + body, hashlib.sha256).digest()
        ).decode()
        return {
            "path": "/phone/path",
            "body": base64.b64encode(body).decode(),
            "headers": {
                "webhook-id": did,
                "webhook-timestamp": stamp,
                "webhook-signature": "v1," + signature,
            },
        }

    invalid = delivery("invalid")
    invalid["headers"]["webhook-signature"] = "v1,wrong"
    assert (await ingress(invalid))["status"] == 401
    assert service.active is None
    assert (await ingress(delivery("a")))["status"] == 200
    assert (await ingress(delivery("b")))["status"] == 200
    assert service.provider.await_count == 1
    await service.end()


@pytest.mark.anyio
async def test_attach_failure_and_restart_hang_up(world):
    service, h = world

    class BadConnection(Connection):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.attach.side_effect = RuntimeError("failed")

    service.connection_factory = BadConnection
    with pytest.raises(RuntimeError):
        await service.incoming("failure", HEADERS)
    assert service.active is None
    assert h.store.one("SELECT state FROM calls WHERE id='failure'")["state"] == "failed"
    h.store.execute(
        "INSERT INTO calls(id,number,persona_id,state,started) VALUES ('stale',?,'panini','active',0)",
        (NUMBER,),
    )
    await service.recover()
    assert service.provider.call_args.args == ("stale", "hangup")
    assert h.store.one("SELECT state FROM calls WHERE id='stale'")["state"] == "failed"


@pytest.mark.anyio
async def test_keypad_event_never_enters_live_transcript():
    events, requests, digits = AsyncMock(), AsyncMock(), AsyncMock()
    connection = PhoneConnection("test", events, requests, on_digits=digits)
    await connection.handle_event({"type": "transport.dtmf.received", "event": "7"})
    digits.assert_awaited_once_with("7")
    events.assert_not_awaited()
    requests.assert_not_awaited()
    assert not connection.input


@pytest.mark.anyio
@pytest.mark.parametrize(
    "command,level", [("Write-Output preview", "tap"), ("Remove-Item important.txt", "verify")]
)
async def test_actual_agent_policy_waits_for_a_separate_owner_decision(tmp_path, monkeypatch, command, level):
    from panini_cli.agent.model import ScriptedModel

    from backend.core_contracts import Readiness, ToolResult
    from backend.harness.runtime import Harness
    from backend.harness.skills import Skills
    from backend.ledger.hook import LedgerHook
    from backend.panini.contracts import Fault
    from backend.policy.hook import PolicyHook
    from backend.registry.registry import Registry

    monkeypatch.setenv("PANINI_PHONE_LINE", "true")
    monkeypatch.setenv("PANINI_REDTEAM_ENFORCE", "false")
    monkeypatch.setenv("PANINI_PERSONAS", "true")
    monkeypatch.setenv("PANINI_V2_POLICY", "shadow")
    monkeypatch.setenv("PANINI_JEV_GATES", "false")
    monkeypatch.setenv("PANINI_POLICY_PROFILE", "balanced")
    script = [
        {
            "calls": [
                {
                    "name": "run_command",
                    "arguments": {"command": command, "cwd": str(tmp_path), "timeout_seconds": 1},
                }
            ]
        },
        {"text": "The test hand returned."},
    ]
    h = Harness(
        Store(tmp_path / "engine.sqlite3"),
        skills=Skills(user_root=tmp_path / "skills"),
        model_factory=lambda: ScriptedModel(script),
    )
    h.artifacts = Artifacts(h.store, tmp_path / "artifacts")
    service = CallService(h, provider=AsyncMock(), connection_factory=Connection)
    service.callers.put(NUMBER, "Owner", "123456")
    registry = Registry(h.registry.runtime, sources=h.registry.sources, state=h.store)
    registry.hooks = [LedgerHook(), PolicyHook(mode="enforce", policy=h.policy)]
    effects = []

    async def ready(*args):
        return Readiness("ready")

    async def run(ctx, spec, args):
        effects.append(ctx.principal)
        return ToolResult(
            "ok", "Test hand returned", data={"stdout": "preview"}, evidence=[{"kind": "exit_code"}]
        )

    registry.hands["run_command"] = SimpleNamespace(readiness=ready, call=run)
    service._registry = registry
    errors = []
    actual_compute = service.compute

    async def tracked_compute(*args):
        try:
            return await actual_compute(*args)
        except Exception as exc:
            errors.append(repr(exc))
            raise

    service.compute = tracked_compute
    try:
        await service.incoming("engine", HEADERS)
        await pin(service)
        call = service.active
        task = asyncio.create_task(service.request(call, "Run the test hand", "once"))
        for _ in range(150):
            opened = h.interrupts.list(status="open")
            if opened:
                break
            await asyncio.sleep(0.01)
        assert opened and not effects, errors or h.store.all("SELECT * FROM call_requests")
        iid = opened[0]["id"]
        full = h.interrupts.get(iid, include_secret=True)
        assert full["level"] == level
        await service.request(call, "yes approve it", "spoken")
        await service.digits(call, "#")
        assert not effects
        if level == "verify":
            with pytest.raises(Fault):
                h.interrupts.resolve(
                    iid,
                    nonce=full["nonce"],
                    action_digest=full["action_digest"],
                    status="resolved",
                    payload={"approved": True},
                    decided_by={"surface": "phone_app", "method": "tap"},
                )
        h.interrupts.resolve(
            iid,
            nonce=full["nonce"],
            action_digest=full["action_digest"],
            status="resolved",
            payload={"approved": True},
            decided_by={"surface": "avatar", "method": "local"},
        )
        await asyncio.wait_for(task, 5)
        assert len(effects) == 1 and effects[0].surface == "phone_line"
        await service.request(call, "repeat", "once")
        assert len(effects) == 1
        await service.end()
        await service.incoming("new-call", HEADERS)
        assert service.active.thread != call.thread
    finally:
        await service.close()
        h.store.close()
