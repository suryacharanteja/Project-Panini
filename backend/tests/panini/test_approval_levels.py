"""Preserve policy approval strength through the real foreground agent/coordinator path."""

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from panini_cli.agent.model import ScriptedModel

from backend.capabilities.agent import AgentAdapter
from backend.core_contracts import Readiness, ToolResult
from backend.harness.runtime import Harness
from backend.harness.skills import Skills
from backend.harness.store import Store
from backend.interrupts import decided_by
from backend.panini.contracts import Fault, Turn
from backend.policy import new_grant
from backend.tests.panini.test_orchestration import setup, step


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
@pytest.mark.parametrize("level", ["verify", "local"])
async def test_agent_policy_review_rejects_phone_tap_and_runs_once_after_pc_review(
    tmp_path, monkeypatch, level
):
    for flag in ("PANINI_V2_REGISTRY", "PANINI_V2_LEDGER", "PANINI_V2_INTERRUPTS"):
        monkeypatch.setenv(flag, "true")
    monkeypatch.setenv("PANINI_V2_POLICY", "enforce")
    monkeypatch.setenv("PANINI_CLI_SCRIPTED_MODEL", "true")
    monkeypatch.delenv("PANINI_V2_ONE_BRAIN", raising=False)
    model = ScriptedModel(
        [
            {
                "calls": [
                    {
                        "name": "run_command",
                        "arguments": {"command": "Write-Output fixture", "cwd": "", "timeout_seconds": 5},
                    }
                ]
            },
            {"text": "Fixture completed."},
        ]
    )
    h = Harness(
        Store(tmp_path / "h.sqlite3"),
        model_factory=lambda: model,
        skills=Skills(user_root=tmp_path / "skills"),
    )
    h.policy.put_grant(new_grant(tools=("run_command",), levels={"local_exec": level}))
    dispatched = []

    async def ready(*_):
        return Readiness("ready")

    async def execute(ctx, spec, args):
        dispatched.append(args["command"])
        return ToolResult(data={"exit_code": 0, "output": "fixture"})

    h.registry.hands["run_command"] = SimpleNamespace(readiness=ready, call=execute)
    adapter = AgentAdapter(h, {})
    c, p, s, epoch = await setup({"agent": adapter}, [step("agent")])
    try:
        await c.submit(s, p, epoch, Turn(command_id=uuid4(), text="Run the reviewed fixture"))
        for _ in range(200):
            pending = h.interrupts.list("open")
            if pending:
                break
            await asyncio.sleep(0.01)
        assert len(pending) == 1 and dispatched == [], (s.last_task, model.requests)
        review = pending[0]
        assert review["level"] == level and review["action"]["approval_level"] == level
        proof = dict(
            nonce=review["nonce"],
            action_digest=review["action_digest"],
            status="resolved",
            payload={"approved": True},
        )
        with pytest.raises(Fault):
            h.interrupts.resolve(review["id"], **proof, decided_by=decided_by("phone_app", "tap"))
        assert h.interrupts.get(review["id"])["status"] == "open" and not dispatched
        h.interrupts.resolve(review["id"], **proof, decided_by=decided_by("avatar", "local"))
        await asyncio.wait_for(s.active.runner, 5)
        assert dispatched == ["Write-Output fixture"] and not s.confirmations
    finally:
        await c.close()
        await h.close()
