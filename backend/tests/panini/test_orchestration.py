import asyncio
from uuid import uuid4

import pytest

from backend.capabilities.contracts import Outcome, UnavailableAdapter
from backend.panini.contracts import Fault, Turn
from backend.panini.coordinator import Coordinator
from backend.panini.events import EventHub
from backend.panini.planning import Plan, Step, route
from backend.panini.settings import Settings


@pytest.fixture
def anyio_backend():
    return "asyncio"


class Adapter(UnavailableAdapter):
    def __init__(self, name, labels=(), outcome="verified_complete", hold=False):
        super().__init__(name, "")
        self.choices = {str(i): {"choice_id": str(i), "label": label} for i, label in enumerate(labels)}
        self.calls, self.releases, self.stops = [], 0, 0
        self.outcome, self.hold = outcome, hold
        self.entered = asyncio.Event()

    async def availability(self):
        return {"capability": self.name, "state": "ready", "reason": ""}

    async def targets(self):
        return list(self.choices.values())

    async def select(self, choice_id):
        if choice_id not in self.choices:
            raise Fault("TARGET_CHANGED", "The target closed. Refresh targets.")
        return dict(self.choices[choice_id])

    async def run(self, ctx, instruction, target):
        self.calls.append((instruction, target))
        self.entered.set()
        if self.hold:
            await asyncio.Event().wait()
        evidence = ctx.record_evidence("fixture", "Fixture state checked.", {"value": 42})
        return Outcome(self.outcome, "Fixture result 42.", evidence_ids=[evidence])

    async def release(self, _):
        self.releases += 1

    async def stop(self, _):
        self.stops += 1


async def setup(adapters, steps=None):
    async def router(*_):
        return {"steps": steps}

    c = Coordinator(
        Settings(enabled=True, bootstrap_token="x" * 40), adapters, route=router if steps else None
    )
    _, principal = c.auth.issue("controller")
    s = c.get((await c.create_session(principal, uuid4()))["session_id"])
    epoch = (await c.acquire(s, principal))["lease_epoch"]
    return c, principal, s, epoch


async def submit(c, p, s, epoch, text="Perform the fixture task", **kwargs):
    await c.submit(s, p, epoch, Turn(command_id=uuid4(), text=text, **kwargs))
    await s.active.runner
    return s.last_task


def step(capability, **kwargs):
    return {"capability": capability, "instruction": "Read fixture value", **kwargs}


@pytest.mark.anyio
async def test_code_automatically_uses_sole_workspace_without_desktop():
    code = Adapter("code", ["C:/approved/project"])
    c, p, s, e = await setup({"code": code}, [step("code")])
    t = await submit(c, p, s, e, "Run a defined Python simulation using VS Code")
    assert t.outcome == "verified_complete"
    assert code.calls[0][1]["label"] == "C:/approved/project"
    assert s.selected["code"]["target_ref"]
    assert t.steps[0]["target_label"] == "C:/approved/project"
    await c.close()


@pytest.mark.anyio
async def test_explicit_unavailable_target_never_falls_back_to_unrelated_sole_window():
    desktop = Adapter("desktop", ["Notepad"])
    c, p, s, e = await setup({"desktop": desktop}, [step("desktop", target_query="Calculator")])
    t = await submit(c, p, s, e)
    assert t.error_code == "TARGET_UNAVAILABLE" and not desktop.calls
    await c.close()


@pytest.mark.anyio
async def test_ambiguous_choices_resume_only_remaining_steps_and_preserve_observations():
    search, code = Adapter("search"), Adapter("code", ["Project A", "Project B"])
    c, p, s, e = await setup({"search": search, "code": code}, [step("search"), step("code")])
    first = await submit(c, p, s, e)
    assert first.state == "needs_input" and len(first.choices) == 2
    assert len(search.calls) == 1 and not code.calls
    assert first.steps[0]["state"] == "completed"
    second = await submit(c, p, s, e, "Project B", resume_choice_id="1", expected_task_id=first.task_id)
    assert second.outcome == "verified_complete"
    assert len(search.calls) == 1
    assert code.calls[0][1]["label"] == "Project B"
    assert "Fixture result 42" in code.calls[0][0]
    with pytest.raises(Fault, match="earlier task"):
        await submit(c, p, s, e, resume_choice_id="1", expected_task_id=first.task_id)
    await c.close()


@pytest.mark.anyio
async def test_spoken_number_resolves_pending_choice_without_replanning():
    code = Adapter("code", ["Project A", "Project B"])
    c, p, s, e = await setup({"code": code}, [step("code")])
    await submit(c, p, s, e)
    t = await submit(c, p, s, e, "2")
    assert t.outcome == "verified_complete" and code.calls[0][1]["label"] == "Project B"
    await c.close()


@pytest.mark.anyio
async def test_target_closed_between_question_and_answer_does_not_run():
    code = Adapter("code", ["Project A", "Project B"])
    c, p, s, e = await setup({"code": code}, [step("code")])
    first = await submit(c, p, s, e)
    code.choices.pop("1")
    t = await submit(c, p, s, e, resume_choice_id="1", expected_task_id=first.task_id)
    assert t.error_code == "TARGET_CHANGED" and not code.calls
    await c.close()


@pytest.mark.anyio
@pytest.mark.parametrize("outcome", ["partial", "unknown_effect", "blocked", "failed"])
async def test_unverified_step_never_advances_to_another_capability(outcome):
    search, code = Adapter("search", outcome=outcome), Adapter("code", ["Project"])
    c, p, s, e = await setup({"search": search, "code": code}, [step("search"), step("code")])
    t = await submit(c, p, s, e)
    assert t.outcome == outcome and not code.calls
    assert t.steps[1]["state"] == "not_run"
    await c.close()


@pytest.mark.anyio
async def test_stop_during_first_step_prevents_later_steps_and_clears_continuation():
    search, code = Adapter("search", hold=True), Adapter("code", ["Project"])
    c, p, s, e = await setup({"search": search, "code": code}, [step("search"), step("code")])
    await c.submit(s, p, e, Turn(command_id=uuid4(), text="Search then write"))
    await search.entered.wait()
    await c.stop(s)
    assert s.last_task.outcome == "cancelled" and not code.calls and s.pending is None
    assert search.releases == 1
    await c.close()


@pytest.mark.anyio
async def test_clarification_keeps_context_and_router_sees_readiness(monkeypatch):
    code = Adapter("code", ["Project"])
    c, p, s, e = await setup({"code": code})
    seen = []

    async def parse(rules, payload, schema):
        seen.append(payload)
        if len(seen) == 1:
            return Plan(reply="What should the simulation model?", clarification=True)
        return Plan(steps=[Step(capability="code", instruction="Run a seeded dice simulation")])

    monkeypatch.setattr("backend.panini.planning.parse", parse)
    c.route = route
    first = await submit(c, p, s, e, "Run a simulation in VS Code")
    assert first.state == "needs_input" and not code.calls
    second = await submit(c, p, s, e, "A seeded dice simulation")
    assert second.outcome == "verified_complete"
    assert seen[1]["pending_clarification"]["instruction"] == "Run a simulation in VS Code"
    assert seen[0]["inventory"]["code"]["target_count"] == 1
    assert "targets" not in seen[0]["inventory"]["code"]
    await c.close()


def test_plan_schema_rejects_unbounded_or_contradictory_actions():
    with pytest.raises(ValueError):
        Plan(steps=[Step(capability="search", instruction="Find sources")] * 7)
    with pytest.raises(ValueError):
        Plan(reply="Done", steps=[Step(capability="search", instruction="Find sources")])
    with pytest.raises(ValueError):
        Step(capability="code", operation="open_application", instruction="open", application_id="cmd")


@pytest.mark.anyio
async def test_unregistered_evidence_cannot_advance_a_workflow():
    search, code = Adapter("search"), Adapter("code", ["Project"])

    async def forged(*_):
        return Outcome("verified_complete", "Trust me", evidence_ids=["invented"])

    search.run = forged
    c, p, s, e = await setup({"search": search, "code": code}, [step("search"), step("code")])
    t = await submit(c, p, s, e)
    assert t.outcome == "unknown_effect" and not code.calls
    await c.close()


@pytest.mark.anyio
async def test_stop_invalidates_an_unanswered_target_choice():
    code = Adapter("code", ["Project A", "Project B"])
    c, p, s, e = await setup({"code": code}, [step("code")])
    first = await submit(c, p, s, e)
    await c.stop(s)
    with pytest.raises(Fault, match="earlier task"):
        await submit(c, p, s, e, resume_choice_id="1", expected_task_id=first.task_id)
    assert not code.calls
    await c.close()


@pytest.mark.anyio
async def test_model_cannot_choose_an_unrequested_workspace_from_inventory(monkeypatch):
    code = Adapter("code", ["Project A", "Project B"])
    c, p, s, e = await setup({"code": code})
    calls = 0

    async def parse(*_):
        nonlocal calls
        calls += 1
        return Plan(steps=[Step(capability="code", instruction="Run the test", target_query="Project A")])

    monkeypatch.setattr("backend.panini.planning.parse", parse)
    c.route = route
    t = await submit(c, p, s, e, "Run a defined CLI task in my workspace")
    assert t.error_code == "INVALID_PLAN" and not code.calls and calls == 3
    await c.close()


@pytest.mark.anyio
async def test_jev_evaluates_llm_route_before_execution(monkeypatch):
    code = Adapter("code", ["Project"])
    c, p, s, e = await setup({"code": code})
    calls = []

    async def parse(*_):
        return Plan(steps=[Step(capability="code", instruction="Run the verified CLI task")])

    def jev_approves(payload, questions):
        calls.append((payload, questions))
        return {
            "answers": {
                "fits_request": {"probability": 0.9},
                "needs_clarification": {"probability": 0.1},
                "wrong_capability": {"probability": 0.05},
            }
        }

    monkeypatch.setenv("PANINI_AGENT_DEFAULT", "false")
    monkeypatch.setattr("backend.config.jev_gates", lambda: True)
    monkeypatch.setattr("backend.jev_client.ask", jev_approves)
    monkeypatch.setattr("backend.panini.planning.parse", parse)
    c.route = route

    t = await submit(c, p, s, e, "Run the verified CLI task")
    assert t.outcome == "verified_complete"
    assert code.calls and calls
    decision = next(item for item in t.trace if item["type"] == "route.decision")
    assert decision["decided_by"] == "llm_proposer+jev_evaluator"
    assert decision["evaluation"]["probabilities"]["fits_request"] == 0.9
    await c.close()


@pytest.mark.anyio
async def test_jev_rejected_route_falls_back_to_agent(monkeypatch):
    code, agent = Adapter("code", ["Project"]), Adapter("agent")
    c, p, s, e = await setup({"code": code, "agent": agent})

    async def parse(*_):
        return Plan(steps=[Step(capability="code", instruction="Run a simulation")])

    def jev_rejects(*_):
        return {
            "answers": {
                "fits_request": {"probability": 0.2},
                "needs_clarification": {"probability": 0.8},
                "wrong_capability": {"probability": 0.6},
            }
        }

    monkeypatch.setenv("PANINI_AGENT_DEFAULT", "false")
    monkeypatch.setattr("backend.config.jev_gates", lambda: True)
    monkeypatch.setattr("backend.jev_client.ask", jev_rejects)
    monkeypatch.setattr("backend.panini.planning.parse", parse)
    c.route = route

    t = await submit(c, p, s, e, "Run a simulation")
    assert t.outcome == "verified_complete"
    assert not code.calls and agent.calls
    decision = next(item for item in t.trace if item["type"] == "route.decision")
    assert decision["decided_by"] == "jev_evaluator_fallback_agent"
    assert decision["evaluation"]["accepted"] is False
    await c.close()


def test_combined_workflow_snapshot_fits_event_budget_without_mutating_stored_output():
    import json

    from backend.panini.coordinator import Task

    task = Task("task", "fixture", "code", None, 1)
    task.execution = {"items": [{"output": "漢" * 1500} for _ in range(12)]}
    task.steps = [{"instruction": "漢" * 500, "message": "漢" * 1000} for _ in range(6)]
    snapshot = task.view()
    assert len(json.dumps(snapshot, ensure_ascii=False).encode()) <= 48000
    assert len(task.execution["items"]) == 12
    hub = EventHub("instance", "session")
    assert hub.publish("task.blocked", snapshot)["sequence"] == 1
