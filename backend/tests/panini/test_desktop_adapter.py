"""No OS input: exercise desktop admission, confirmations and readback boundaries."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest

from backend.capabilities import desktop
from backend.panini.contracts import Fault


@pytest.fixture
def anyio_backend():
    return "asyncio"


class Context:
    def __init__(self):
        self.task = SimpleNamespace(task_id="test-task")
        self.cancelled = False
        self.events, self.evidence = [], []
        self.confirmations = self.dispatched = 0

    def check(self):
        if self.cancelled:
            raise asyncio.CancelledError

    def emit(self, phase, message, **details):
        self.check()
        self.events.append((phase, details))

    def record_evidence(self, *args):
        self.evidence.append(args)
        return "verified-readback"

    def dispatch(self):
        self.check()
        self.dispatched += 1

    async def confirm(self, *_):
        self.confirmations += 1


class Pipe:
    def __init__(self):
        self.value, self.name = "", "Draft text"
        self.actions = []
        self.readback = True
        self.foreground = True

    async def call(self, request):
        if request["op"] == "observe":
            return {
                "observation_id": "observation",
                "observed_at": 1,
                "foreground": self.foreground,
                "elements": [
                    {
                        "element_id": "element",
                        "automation_id": "draft",
                        "name": self.name,
                        "enabled": True,
                        "value": self.value,
                        "patterns": ["ValuePatternIdentifiers.Pattern"],
                    }
                ],
            }
        if request["op"] == "action":
            self.actions.append(deepcopy(request))
            if self.readback:
                self.value = request["action"]["text"]
            return {"dispatched": True, "verified": True}
        return {"stopped": True}


@pytest.fixture
def harness(monkeypatch):
    monkeypatch.setenv("PANINI_DESKTOP_VISUAL", "0")
    adapter = desktop.DesktopAdapter(True)
    adapter.pipe = Pipe()
    context = Context()
    target = {"label": "Fixture", "application": "panini.desktop.fixture"}
    action = desktop.Action(
        kind="set_value",
        element_id="draft",
        text="exact draft",
        direction="down",
        explanation="Set the authorized draft",
    )
    goal = desktop.Goal(
        supported=True, explanation="", field="value", expected="exact draft", automation_id="draft"
    )

    async def parse(_rules, _payload, schema):
        return goal if schema is desktop.Goal else action

    monkeypatch.setattr(desktop, "parse", parse)
    monkeypatch.setattr(desktop.jev_client, "ask", lambda *_: {"answers": {"advance": {"probability": 0.99}}})
    return adapter, context, target, action, goal


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["click", "type_text"])
async def test_physical_input_requires_confirmation_even_for_calculator(harness, kind):
    adapter, context, target, action, _ = harness
    target["application"] = "calculatorapp"
    action.kind = kind
    result = await adapter.run(context, "Set the draft", target)
    assert result.outcome == "verified_complete"
    assert context.confirmations == 1
    assert adapter.pipe.actions[0]["action"]["kind"] == kind


@pytest.mark.anyio
async def test_completion_requires_actual_postcondition_not_action_ack(harness):
    adapter, context, target, *_ = harness
    adapter.pipe.readback = False
    outcome = await adapter.run(context, "Set the draft", target)
    assert outcome.outcome == "unknown_effect"
    assert len(adapter.pipe.actions) == 1
    assert not context.evidence


@pytest.mark.anyio
async def test_confirmed_action_has_exact_readback_and_evidence(harness):
    adapter, context, target, *_ = harness
    outcome = await adapter.run(context, "Set the draft", target)
    assert outcome.outcome == "verified_complete"
    assert outcome.evidence_ids == ["verified-readback"]
    assert context.confirmations == context.dispatched == len(adapter.pipe.actions) == 1
    assert adapter.pipe.actions[0]["action"]["element_id"] == "element"
    assert context.evidence[0][2]["matched_elements"][0]["value"] == "exact draft"


@pytest.mark.anyio
async def test_rejected_jev_candidates_never_dispatch(harness, monkeypatch):
    adapter, context, target, *_ = harness
    monkeypatch.setattr(desktop.jev_client, "ask", lambda *_: {"answers": {"advance": {"probability": 0.1}}})
    result = await adapter.run(context, "Set the draft", target)
    assert result.outcome == "blocked"
    assert not adapter.pipe.actions and not context.confirmations
    assert len([phase for phase, _ in context.events if phase == "evaluating"]) == 3


@pytest.mark.anyio
async def test_control_changes_during_confirmation_prevent_dispatch(harness):
    adapter, context, target, *_ = harness

    async def confirm(*_):
        adapter.pipe.name = "Different field"

    context.confirm = confirm
    with pytest.raises(Fault, match="control changed"):
        await adapter.run(context, "Set the draft", target)
    assert not adapter.pipe.actions


@pytest.mark.anyio
async def test_stop_during_confirmation_prevents_dispatch(harness):
    adapter, context, target, *_ = harness

    async def confirm(*_):
        context.cancelled = True

    context.confirm = confirm
    with pytest.raises(asyncio.CancelledError):
        await adapter.run(context, "Set the draft", target)
    assert not adapter.pipe.actions


@pytest.mark.anyio
async def test_unsupported_task_does_not_request_admission_or_confirmation(harness, monkeypatch):
    adapter, context, target, _, goal = harness
    goal.supported = False
    monkeypatch.setattr(desktop.jev_client, "ask", lambda *_: pytest.fail("Unsupported task reached Jev"))
    assert (await adapter.run(context, "Unsupported task", target)).outcome == "blocked"
    assert not adapter.pipe.actions and not context.confirmations


@pytest.mark.anyio
async def test_unknown_element_is_rejected_before_admission(harness, monkeypatch):
    adapter, context, target, action, _ = harness
    action.element_id = "not-in-the-observation"
    monkeypatch.setattr(desktop.jev_client, "ask", lambda *_: pytest.fail("Ungrounded action reached Jev"))
    with pytest.raises(Fault, match="control is unavailable"):
        await adapter.run(context, "Set the draft", target)
    assert not adapter.pipe.actions


@pytest.mark.anyio
async def test_user_focus_change_during_planning_stops_before_dispatch(harness, monkeypatch):
    adapter, context, target, action, goal = harness
    target["application"] = "calculatorapp"
    monkeypatch.setenv("PANINI_APPROVALS", "risky")  # An unconfirmed action re-checks focus itself.

    async def parse(_rules, _payload, schema):
        if schema is desktop.Goal:
            return goal
        adapter.pipe.foreground = False
        return action

    monkeypatch.setattr(desktop, "parse", parse)
    result = await adapter.run(context, "Set the draft", target)
    assert result.outcome == "blocked" and "Focus changed" in result.message
    assert not adapter.pipe.actions
    assert not any(phase == "needs_focus" for phase, _ in context.events)
