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
    from panini_cli.agent import local_quality

    monkeypatch.setattr(local_quality, "qualified", lambda task_class: (True, "qualified"))
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
async def test_offline_fallback_uses_local_admission_and_semantic_desktop(harness, monkeypatch):
    from backend.brain import local_gate

    adapter, context, target, *_ = harness
    monkeypatch.setenv("PANINI_LOCAL_MODE", "fallback")
    monkeypatch.setenv("PANINI_LOCAL_MODEL", "test-local")
    monkeypatch.setenv("PANINI_LOCAL_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "")
    monkeypatch.setenv("PANINI_DESKTOP_VISUAL", "1")
    monkeypatch.setattr(desktop.jev_client, "ask", lambda *_: pytest.fail("Jev called offline"))
    seen = []

    async def admit(state, questions, *, timeout, allow_fallback):
        seen.append((state, questions, timeout, allow_fallback))
        return {"answers": {"advance": {"probability": 1.0}}}

    monkeypatch.setattr(local_gate, "ask", admit)
    result = await adapter.run(context, "Set the draft", target)
    assert result.outcome == "verified_complete"
    assert len(adapter.pipe.actions) == 1 and len(seen) == 1
    assert seen[0][0]["selected_control"]["element_id"] == "draft"
    assert seen[0][3] is True


@pytest.mark.anyio
async def test_offline_fallback_fails_closed_when_local_admission_is_unavailable(harness, monkeypatch):
    from backend.brain import local_gate

    adapter, context, target, *_ = harness
    monkeypatch.setenv("PANINI_LOCAL_MODE", "fallback")
    monkeypatch.setenv("PANINI_LOCAL_MODEL", "test-local")
    monkeypatch.setenv("PANINI_LOCAL_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "")

    async def unavailable(*args, **kwargs):
        raise RuntimeError("model offline")

    monkeypatch.setattr(local_gate, "ask", unavailable)
    result = await adapter.run(context, "Set the draft", target)
    assert result.outcome == "blocked" and "model offline" in result.message
    assert not adapter.pipe.actions and not context.confirmations


@pytest.mark.anyio
async def test_gateway_outage_switches_desktop_admission_to_local(harness, monkeypatch):
    from backend.brain import local_gate

    adapter, context, target, *_ = harness
    monkeypatch.setenv("PANINI_LOCAL_MODE", "fallback")
    monkeypatch.setenv("PANINI_LOCAL_MODEL", "test-local")
    monkeypatch.setenv("PANINI_LOCAL_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "test-gateway")
    monkeypatch.setattr(
        desktop.jev_client, "ask", lambda *_: (_ for _ in ()).throw(RuntimeError("gateway down"))
    )
    called = []

    async def admit(*args, **kwargs):
        called.append(True)
        return {"answers": {"advance": {"probability": 1.0}}}

    monkeypatch.setattr(local_gate, "ask", admit)
    result = await adapter.run(context, "Set the draft", target)
    assert result.outcome == "verified_complete" and called == [True]


@pytest.mark.anyio
async def test_open_application_verifies_only_a_new_window(monkeypatch):
    app = {"application_id": "notepad", "label": "Notepad", "application": "notepad"}
    existing = {"hwnd": 10, "pid": 100, "created": "old", "application": "notepad"}
    launched = {"hwnd": 20, "pid": 200, "created": "new", "application": "notepad"}

    class LaunchPipe:
        def __init__(self, create_window):
            self.create_window = create_window
            self.launched = False

        async def call(self, request):
            if request["op"] == "applications":
                return {"applications": [app]}
            if request["op"] == "targets":
                return {"targets": [existing] + ([launched] if self.launched and self.create_window else [])}
            if request["op"] == "launch":
                self.launched = True
                return {"launch_pid": 200}
            if request["op"] == "window":
                assert request["target"] == launched
                return {"verified": True}
            raise AssertionError(request)

    async def no_wait(_):
        return None

    monkeypatch.setattr(desktop.asyncio, "sleep", no_wait)
    adapter = desktop.DesktopAdapter(True)
    adapter.pipe = LaunchPipe(True)
    context = Context()
    result = await adapter.open_application(context, "notepad")
    assert result.outcome == "verified_complete"
    assert context.dispatched == 1 and context.evidence[0][2]["window"]["verified"]

    adapter.pipe = LaunchPipe(False)
    result = await adapter.open_application(Context(), "notepad")
    assert result.outcome == "unknown_effect"


@pytest.mark.anyio
async def test_window_identity_uses_native_target_without_uia_and_rejects_rebinding():
    target = {"hwnd": 20, "pid": 200, "created": "new", "application": "notepad"}

    class WindowPipe:
        changed = False
        calls = []

        async def call(self, request):
            self.calls.append(request)
            assert request["op"] == "window" and request["task_id"] == "run"
            return {**target, "pid": 201 if self.changed else 200, "title": "New document"}

    adapter = desktop.DesktopAdapter(True)
    adapter.pipe = WindowPipe()
    adapter.choices = {"new": target}
    observed, identity = await adapter.window_identity("new", "run")
    assert observed == target and identity["title"] == "New document"
    assert [call["op"] for call in adapter.pipe.calls] == ["window"]
    adapter.pipe.changed = True
    with pytest.raises(Fault, match="identity changed"):
        await adapter.window_identity("new", "run")
    with pytest.raises(Fault, match="Refresh and select"):
        await adapter.window_identity("missing", "run")


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


def test_pipe_waits_for_a_starting_or_busy_broker_before_reporting_offline(monkeypatch):
    import ctypes

    from backend.capabilities import desktop

    answers = iter([False, False, True])
    calls = []

    def wait(name, timeout):
        calls.append(timeout)
        return next(answers)

    monkeypatch.setattr(ctypes.windll.kernel32, "WaitNamedPipeW", wait, raising=False)
    monkeypatch.setattr(desktop.time, "sleep", lambda seconds: None)

    class Connected(Exception):
        pass

    def connected(*args, **kwargs):
        raise Connected

    monkeypatch.setattr("builtins.open", connected)
    pipe = desktop.DesktopPipe()
    pipe.name, pipe.secret = "panini-test", "s" * 32
    with pytest.raises(Connected):
        pipe._call({"op": "targets"})
    assert len(calls) == 3  # Two failed waits (starting, then busy) before the third finds the broker.
