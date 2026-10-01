"""The agent is the default brain: general computer tools, agent-first routing and recovery."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from backend.harness import computer
from backend.panini.planning import route
from backend.tests.panini.test_orchestration import Adapter, setup, step, submit


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(computer.Path, "home", lambda: tmp_path)
    (tmp_path / "Downloads").mkdir()
    (tmp_path / "Downloads" / "report.pdf").write_bytes(b"x")
    (tmp_path / "notes.txt").write_text("hello", encoding="utf-8")
    (tmp_path / ".env").write_text("SECRET=1", encoding="utf-8")
    monkeypatch.setattr(
        "backend.capabilities.desktop.known_folder", lambda name: tmp_path / name.capitalize()
    )
    return tmp_path


def test_restricted_mode_reads_the_home_folder_but_not_secrets_or_outside(
    home, tmp_path_factory, monkeypatch
):
    monkeypatch.setenv("PANINI_COMPUTER_ACCESS", "restricted")
    assert computer.list_files("Downloads")["entries"] == ["report.pdf  (1 bytes)"]
    assert computer.read_file("notes.txt")["text"] == "hello"
    with pytest.raises(computer.ComputerError, match="secrets"):
        computer.read_file(".env")
    outside = tmp_path_factory.mktemp("elsewhere") / "x.txt"
    outside.write_text("no", encoding="utf-8")
    with pytest.raises(computer.ComputerError, match="outside"):
        computer.read_file(str(outside))


def test_full_access_is_the_default_and_still_never_returns_secrets(home, tmp_path_factory, monkeypatch):
    monkeypatch.delenv("PANINI_COMPUTER_ACCESS", raising=False)
    elsewhere = tmp_path_factory.mktemp("elsewhere")
    (elsewhere / "x.txt").write_text("anywhere", encoding="utf-8")
    (elsewhere / "id_rsa").write_text("key", encoding="utf-8")
    assert computer.read_file(str(elsewhere / "x.txt"))["text"] == "anywhere"
    assert computer.list_files(str(elsewhere))["count"] == 2
    with pytest.raises(computer.ComputerError, match="secrets"):
        computer.read_file(str(elsewhere / "id_rsa"))
    assert computer.run_command("Write-Output ok", cwd=str(elsewhere))["output"].strip() == "ok"


def test_commands_run_and_report_output(home):
    result = computer.run_command("Write-Output 42")
    assert result["exit_code"] == 0 and "42" in result["output"]


def agent_with(tmp_path, steps, confirm=None):
    from panini_cli.agent.model import ScriptedModel

    from backend.harness.runtime import Harness
    from backend.harness.skills import Skills
    from backend.harness.store import Store

    model = ScriptedModel(steps)
    h = Harness(
        Store(tmp_path / "h.sqlite3"), model_factory=lambda: model, skills=Skills(user_root=tmp_path / "sk")
    )

    async def delegate(*_):
        return {"outcome": "blocked", "message": "unused"}

    return h.agent(delegate=delegate, capabilities=("search",), confirm=confirm), model


def call(name, **arguments):
    return {"calls": [{"name": name, "arguments": arguments}]}


def test_agent_lists_a_folder_itself_and_answers(home, tmp_path):
    agent, model = agent_with(
        tmp_path, [call("list_files", path="Downloads", pattern="*"), {"text": "Downloads has report.pdf."}]
    )
    result = asyncio.run(agent.run("What's in my Downloads folder?"))
    assert result.outcome == "verified_complete" and "report.pdf" in result.message
    output = json.loads(model.requests[1]["items"][-1]["output"])
    assert output["entries"] == ["report.pdf  (1 bytes)"]


def test_risky_commands_need_approval_and_are_refused_without_it(home, tmp_path, monkeypatch):
    monkeypatch.setenv("PANINI_APPROVALS", "risky")
    asked = []

    async def deny(summary, detail):
        asked.append(summary)
        return False

    agent, model = agent_with(
        tmp_path, [call("run_command", command="rm -rf Downloads"), {"text": "Not done."}], deny
    )
    asyncio.run(agent.run("clean up"))
    assert asked and (home / "Downloads").exists()
    assert "did not approve" in model.requests[1]["items"][-1]["output"]


def test_requests_jev_is_unsure_about_go_to_the_agent():
    ready = {"state": "ready"}
    session = SimpleNamespace(pending=None, inventory={"agent": ready, "desktop": ready}, history=[])
    turn = SimpleNamespace(
        text="Open the Downloads folder and list all files", capability_hint=None, selected_result_id=None
    )
    plan = asyncio.run(route(session, turn))
    assert plan["steps"][0]["capability"] == "agent" and plan["decided_by"] == "agent"


@pytest.mark.anyio
async def test_a_failed_capability_is_retried_by_the_agent_instead_of_stopping():
    desktop, agent = Adapter("desktop", ["Explorer"], outcome="blocked"), Adapter("agent")
    c, p, s, e = await setup({"desktop": desktop, "agent": agent}, [step("desktop")])
    t = await submit(c, p, s, e)
    assert len(desktop.calls) == 1 and len(agent.calls) == 1
    assert "failed" in agent.calls[0][0] and t.outcome == "verified_complete"
    await c.close()


@pytest.mark.anyio
async def test_without_an_agent_the_original_failure_stands():
    desktop = Adapter("desktop", ["Explorer"], outcome="blocked")
    c, p, s, e = await setup({"desktop": desktop}, [step("desktop")])
    t = await submit(c, p, s, e)
    assert t.outcome == "blocked"
    await c.close()


def barge(monkeypatch, stop=0.05, status=0.05):
    from backend import jev_client

    monkeypatch.setenv("PANINI_JEV_GATES", "true")
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "test-gateway-key")
    monkeypatch.setattr(
        jev_client,
        "ask",
        lambda state, q: {
            "answers": {"stop": {"probability": stop}, "status": {"probability": status}},
            "usage": {},
        },
    )


@pytest.mark.anyio
async def test_a_new_instruction_replaces_a_stuck_task(monkeypatch):
    from uuid import uuid4

    from backend.panini.contracts import Turn

    barge(monkeypatch)
    stuck = Adapter("desktop", ["App"], hold=True)
    c, p, s, e = await setup({"desktop": stuck}, [step("desktop")])
    await c.submit(s, p, e, Turn(command_id=uuid4(), text="Do the slow thing"))
    await stuck.entered.wait()
    first = s.active
    stuck.hold = False
    receipt = await c.submit(s, p, e, Turn(command_id=uuid4(), text="Pause the video instead"))
    assert receipt["status"] == "accepted" and first.cancelled.is_set()
    await s.active.runner
    assert s.last_task.instruction == "Pause the video instead"
    await c.close()


@pytest.mark.anyio
@pytest.mark.parametrize("stop,status,expected", [(0.9, 0.1, None), (0.1, 0.9, "busy")])
async def test_stop_and_status_while_busy_are_understood(monkeypatch, stop, status, expected):
    from uuid import uuid4

    from backend.panini.contracts import Turn

    barge(monkeypatch, stop, status)
    stuck = Adapter("desktop", ["App"], hold=True)
    c, p, s, e = await setup({"desktop": stuck}, [step("desktop")])
    await c.submit(s, p, e, Turn(command_id=uuid4(), text="Do the slow thing"))
    await stuck.entered.wait()
    receipt = await c.submit(
        s, p, e, Turn(command_id=uuid4(), text="Stop that nonsense task / what are you doing?")
    )
    if expected == "busy":
        assert receipt["status"] == "busy" and "Do the slow thing" in receipt["message"]
        assert s.active is not None
        await c.stop(s)
    else:
        assert s.active is None
    await c.close()
