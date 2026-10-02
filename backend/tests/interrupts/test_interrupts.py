import asyncio
import time

import httpx
import pytest

from backend.harness.store import Store
from backend.interrupts import Interrupts, InterruptSpec, decided_by, digest
from backend.panini.contracts import Fault

LOCAL = decided_by("avatar", "local")
PHONE = decided_by("phone_app", "tap", device_id="p1")


@pytest.fixture
def service(tmp_path):
    store = Store(tmp_path / "h.sqlite3")
    yield Interrupts(store)
    store.close()


def approval(service, level="tap", ttl=60, action=None):
    return service.open(
        InterruptSpec(
            "approval",
            "tool_call",
            "Send email",
            "Send this email to a@b.com?",
            preview={"to": "a@b.com"},
            action=action or {"tool": "send", "to": "a@b.com"},
            level=level,
            ttl_s=ttl,
        ),
        run_id="run-1",
    )


def resolve(service, view, approved=True, by=LOCAL, nonce=None, digest_=None):
    return service.resolve(
        view["id"],
        nonce=nonce or view["nonce"],
        action_digest=digest_ or view["action_digest"],
        status="resolved",
        payload={"approved": approved},
        decided_by=by,
    )


def test_resolve_and_idempotent_replay(service):
    view = approval(service)
    first = resolve(service, view)
    assert first["status"] == "resolved" and first["decision"]["payload"] == {"approved": True}
    assert resolve(service, view)["decided_at"] == first["decided_at"]  # Same decision again: no change.
    with pytest.raises(Fault, match="already answered"):
        resolve(service, view, approved=False)


def test_digest_and_nonce_must_match(service):
    """Invariant I3: a decision for a different action is refused."""
    view = approval(service)
    with pytest.raises(Fault, match="changed"):
        resolve(service, view, digest_=digest({"tool": "send", "to": "evil@x"}))
    with pytest.raises(Fault, match="changed"):
        resolve(service, view, nonce="n" * 40)
    assert service.get(view["id"])["status"] == "open"


def test_expiry(service):
    view = approval(service, ttl=0.01)
    time.sleep(0.02)
    with pytest.raises(Fault, match="expired"):
        resolve(service, view)
    assert service.get(view["id"])["status"] == "expired"


def test_levels_decide_who_can_answer(service):
    tap = approval(service, level="tap")
    assert resolve(service, tap, by=PHONE)["status"] == "resolved"
    verify = approval(service, level="verify")
    with pytest.raises(Fault, match="passkey"):
        resolve(service, verify, by=PHONE)
    assert resolve(service, verify, by=decided_by("phone_app", "passkey"))["status"] == "resolved"
    local = approval(service, level="local")
    with pytest.raises(Fault, match="the PC"):
        resolve(service, local, by=decided_by("phone_app", "passkey"))
    assert resolve(service, local, by=LOCAL)["status"] == "resolved"


def test_voice_channels_and_background_cannot_approve(service):
    """Invariants I4 and I8."""
    for by in (
        decided_by("phone_line", "tap"),
        decided_by("slack", "tap"),
        decided_by("avatar", "voice"),
        decided_by("background", "local", principal_kind="system"),
        decided_by("mcp", "local", principal_kind="mcp_client"),
    ):
        view = approval(service)
        with pytest.raises(Fault):
            resolve(service, view, by=by)


def test_questions_can_be_answered_from_any_owner_surface(service):
    view = service.open(
        InterruptSpec(
            "question",
            "clarification",
            "Which file?",
            "Which file should I send?",
            response_schema={
                "type": "object",
                "properties": {"answer": {"type": "string"}},
                "required": ["answer"],
            },
        ),
        run_id="r",
    )
    done = service.resolve(
        view["id"],
        nonce=view["nonce"],
        action_digest=None,
        status="resolved",
        payload={"answer": "report.pdf"},
        decided_by=decided_by("slack", "tap"),
    )
    assert done["decision"]["payload"]["answer"] == "report.pdf"
    bad = service.open(
        InterruptSpec(
            "question",
            "clarification",
            "Q",
            "Q?",
            response_schema={
                "type": "object",
                "properties": {"answer": {"type": "string"}},
                "required": ["answer"],
            },
        ),
        run_id="r",
    )
    with pytest.raises(Fault, match="Invalid answer"):
        service.resolve(
            bad["id"], nonce=bad["nonce"], action_digest=None, status="resolved", payload={}, decided_by=LOCAL
        )


def test_first_decision_wins_under_a_race(service):
    view = approval(service)

    async def both():
        async def attempt(approved, by):
            await asyncio.sleep(0)
            try:
                return resolve(service, view, approved=approved, by=by)["decision"]["payload"]["approved"]
            except Fault as exc:
                return exc.code

        return await asyncio.gather(attempt(True, LOCAL), attempt(False, PHONE))

    outcomes = asyncio.run(both())
    assert sorted(map(str, outcomes)) == sorted(["True", "DECISION_ALREADY_MADE"])


def test_wait_wakes_on_resolution_and_expires(service):
    async def scenario():
        view = approval(service)
        waiter = asyncio.ensure_future(service.wait(view["id"], 5))
        await asyncio.sleep(0.01)
        resolve(service, view, by=PHONE)
        woke = await waiter
        late = approval(service)
        with pytest.raises(Fault, match="expired"):
            await service.wait(late["id"], 0.01)
        return woke

    assert asyncio.run(scenario())["status"] == "resolved"


def test_restart_cancels_foreground_but_keeps_background(service):
    fg = approval(service)
    bg = service.open(
        InterruptSpec("approval", "tool_call", "t", "m", action={"a": 1}, ttl_s=3600),
        run_id="task",
        task_id="task",
    )
    service.cancel_foreground_after_restart()
    assert service.get(fg["id"])["status"] == "cancelled"
    assert service.get(bg["id"])["status"] == "open"


def test_shell_confirmation_flow_runs_on_interrupts(monkeypatch, tmp_path):
    """The avatar's dialog and v1 routes are unchanged; the decision lands in the interrupt store."""
    from uuid import uuid4

    from backend import interrupts as module
    from backend.panini.contracts import ConfirmationDecision
    from backend.tests.panini.test_coordinator import FixtureAdapter, setup, turn

    monkeypatch.setenv("PANINI_V2_INTERRUPTS", "true")
    store = Store(tmp_path / "h.sqlite3")
    service = module.install(Interrupts(store))

    async def scenario():
        adapter = FixtureAdapter(confirm=True)
        c, p, s, epoch = await setup(adapter)
        await c.submit(s, p, epoch, turn())
        await adapter.entered.wait()
        await asyncio.sleep(0)
        item = next(iter(s.confirmations.values()))
        assert item["interrupt"] and service.get(item["confirmation_id"])["status"] == "open"
        c.decide(
            s,
            item["confirmation_id"],
            ConfirmationDecision(
                command_id=uuid4(), nonce=item["nonce"], digest=item["digest"], decision="approve"
            ),
        )
        await asyncio.wait_for(s.active.runner, 5)
        decided = service.get(item["confirmation_id"])
        await c.close()
        return decided, s.last_task

    decided, task = asyncio.run(scenario())
    assert decided["status"] == "resolved" and decided["decided_by"]["surface"] == "avatar"
    assert task.state == "completed"
    store.close()


def test_background_task_parks_resumes_and_does_not_repeat_effects(monkeypatch, tmp_path):
    from panini_cli.agent.model import ScriptedModel

    from backend.harness.mobile import build_app
    from backend.harness.runtime import Harness
    from backend.harness.skills import Skills

    monkeypatch.setenv("PANINI_V2_INTERRUPTS", "true")
    monkeypatch.setenv("PANINI_V2_REGISTRY", "true")
    monkeypatch.setenv("PANINI_V2_LEDGER", "true")
    monkeypatch.setenv("PANINI_APPROVALS", "risky")
    work = tmp_path / "work"
    work.mkdir()
    (work / "keep.txt").write_text("x", encoding="utf-8")
    command = {
        "name": "run_command",
        "arguments": {
            "command": "Remove-Item old.log -ErrorAction SilentlyContinue",
            "cwd": str(work),
            "timeout_seconds": 20,
        },
    }
    writer = {
        "name": "run_command",
        "arguments": {"command": "Set-Content step1.txt 'one'", "cwd": str(work), "timeout_seconds": 20},
    }
    finish = {"name": "finish", "arguments": {"outcome": "verified_complete", "message": "Cleaned up."}}
    scripts = [
        [{"calls": [writer]}, {"calls": [command]}],
        [{"calls": [writer]}, {"calls": [command]}, {"calls": [finish]}],
    ]

    def model():
        return ScriptedModel(scripts.pop(0))

    h = Harness(
        Store(tmp_path / "h.sqlite3"), model_factory=model, skills=Skills(user_root=tmp_path / "skills")
    )
    h.adapters = {}
    task = h.tasks.submit("Clean up the work folder", origin="eval")

    async def first_attempt():
        await h.worker.run_once()

    asyncio.run(first_attempt())
    parked = h.tasks.get(task["id"])
    assert parked["status"] == "waiting_input" and parked["waiting_on"]
    assert (work / "step1.txt").exists()

    async def approve_from_phone():
        transport = httpx.ASGITransport(app=build_app(h, h.mobile.devices), client=("192.168.1.5", 50000))
        async with httpx.AsyncClient(transport=transport, base_url="http://192.168.1.5") as phone:
            code = h.mobile.devices.new_code()["code"]
            await phone.post("/api/pair", json={"code": code, "name": "Test phone"})
            pending = (await phone.get("/api/interrupts")).json()["interrupts"]
            assert len(pending) == 1 and "Remove-Item" in pending[0]["preview"]["detail"]
            answer = await phone.post(
                f"/api/interrupts/{pending[0]['id']}",
                json={
                    "nonce": pending[0]["nonce"],
                    "action_digest": pending[0]["action_digest"],
                    "approved": True,
                },
            )
            return answer.json()

    assert asyncio.run(approve_from_phone())["interrupt"]["status"] == "resolved"
    assert h.tasks.get(task["id"])["status"] == "queued"
    (work / "step1.txt").unlink()
    asyncio.run(first_attempt())
    done = h.tasks.get(task["id"])
    assert done["status"] == "completed", (done.get("result"), h.tasks.events(task["id"]))
    # The earlier effect (step1.txt) was replayed from its receipt, not executed again.
    assert not (work / "step1.txt").exists()
    h.store.close()
