import asyncio
import base64
import io
from copy import deepcopy
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw
from pydantic import ValidationError

from backend.capabilities import desktop_visual as visual
from backend.panini.contracts import Fault
from backend.tests.panini.test_desktop_adapter import Context


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.parametrize(
    "fields",
    [
        {"kind": "drag", "path": [{"x": 1, "y": 2}]},
        {"kind": "keypress", "keys": []},
        {"kind": "type", "text": ""},
        {"kind": "scroll"},
        {"kind": "click", "x": -1},
        {"kind": "wait", "milliseconds": 2001},
    ],
)
def test_rejects_invalid_graphical_contract(fields):
    with pytest.raises(ValidationError):
        visual.VisualAction(explanation="test", **fields)


def test_preserves_typed_whitespace_and_checks_drag_bounds():
    action = visual.VisualAction(kind="type", explanation="test", text="  a\n b  ")
    assert action.text == "  a\n b  "
    goal = visual.VisualGoal(
        supported=True, explanation="", field="value", expected="  a\n b  ", automation_id="draft"
    )
    assert goal.expected == action.text
    action = visual.VisualAction(kind="drag", explanation="test", path=[{"x": 0, "y": 0}, {"x": 100, "y": 1}])
    with pytest.raises(Fault, match="outside"):
        visual.validate_points(action, {"width": 100, "height": 100})


class Pipe:
    def __init__(self):
        self.actions = []
        self.image, self.value, self.token = png("black"), "", 0
        self.readback = True

    def snapshot(self):
        self.token += 1
        return {
            "snapshot_id": str(self.token),
            "observation": {
                "observation_id": str(self.token),
                "foreground": True,
                "focused_element_id": "edit",
                "elements": [{"automation_id": "draft", "value": self.value, "name": "Draft"}],
            },
            "capture": {
                "width": 100,
                "height": 100,
                "source_x": -100,
                "source_y": 0,
                "source_width": 200,
                "source_height": 200,
                "image_base64": self.image,
            },
        }

    async def call(self, request):
        if request["op"] == "visual_state":
            return self.snapshot()
        assert request["op"] == "visual_action"
        self.actions.append(deepcopy(request))
        if self.readback:
            self.value = "exact draft"
            self.image = png("white")
        return {"dispatched": True, "verified": False, "after": self.snapshot()}


@pytest.fixture
def harness(monkeypatch):
    pipe, context = Pipe(), Context()
    images = []
    goal = visual.VisualGoal(
        supported=True, explanation="", field="value", expected="exact draft", automation_id="draft"
    )
    action = visual.VisualAction(kind="type", text="exact draft", explanation="Fill draft")

    async def parse(rules, payload, schema, *, image=None):
        images.append(image)
        assert "image_base64" not in str(payload), "Image must be attached as vision input, not JSON text"
        return goal if schema is visual.VisualGoal else action

    monkeypatch.setattr(visual, "parse", parse)
    monkeypatch.setattr(visual.jev_client, "ask", lambda *_: {"answers": {"advance": {"probability": 0.99}}})
    return SimpleNamespace(pipe=pipe), context, {"label": "Fixture"}, images


def png(color):
    buffer = io.BytesIO()
    Image.new("RGB", (100, 100), color).save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode()


def test_frame_comparison_allows_only_caret_in_unchanged_focused_edit():
    before = Pipe().snapshot()
    before["observation"]["focused_element_id"] = "edit"
    before["observation"]["elements"] = [
        {
            "element_id": "edit",
            "type": "ControlType.Edit",
            "value": "unchanged",
            "bounds": {"x": -100, "y": 0, "width": 200, "height": 200},
        }
    ]
    after = deepcopy(before)
    pixels = Image.new("RGB", (100, 100), "black")
    ImageDraw.Draw(pixels).rectangle((10, 10, 11, 30), fill="white")
    buffer = io.BytesIO()
    pixels.save(buffer, format="PNG")
    after["capture"]["image_base64"] = base64.b64encode(buffer.getvalue()).decode()
    assert visual.same_frame(before, after)
    after["observation"]["elements"][0]["value"] = "changed"
    assert not visual.same_frame(before, after)
    after = deepcopy(before)
    after["capture"]["image_base64"] = png("red")
    assert not visual.same_frame(before, after)
    after = deepcopy(before)
    after["capture"]["source_x"] += 1
    assert not visual.same_frame(before, after)


@pytest.mark.anyio
async def test_real_image_planner_contract_confirmation_and_exact_readback(harness):
    adapter, context, target, images = harness
    result = await visual.run_visual(adapter, context, "Set draft", target)
    assert result.outcome == "verified_complete"
    assert images == [png("black"), png("black")]
    assert adapter.pipe.actions[0]["snapshot_id"] == "2"
    assert context.confirmations == context.dispatched == 1
    assert context.evidence[-1][0] == "uia_postcondition"


@pytest.mark.anyio
async def test_invalid_model_action_replans_without_relaxing_validation(monkeypatch):
    calls = []

    async def planner(rules, payload, schema, *, image):
        calls.append(payload)
        if len(calls) == 1:
            return visual.VisualAction(
                kind="keypress", keys=["CTRL", "A"], element_id="edit", explanation="select"
            )
        return visual.VisualAction(kind="keypress", keys=["CTRL", "A"], explanation="select")

    monkeypatch.setattr(visual, "parse", planner)
    result = await visual.parse_action("rules", {}, visual.VisualAction, image="fixture")
    assert result.element_id == "" and len(calls) == 2
    assert calls[1]["validation_feedback"] and "input" not in calls[1]["validation_feedback"][0]


@pytest.mark.anyio
async def test_invalid_model_actions_stop_after_bounded_retries(monkeypatch):
    calls = []

    async def planner(*args, **kwargs):
        calls.append(1)
        return visual.VisualAction(kind="keypress", keys=[], explanation="invalid")

    monkeypatch.setattr(visual, "parse", planner)
    with pytest.raises(Fault) as error:
        await visual.parse_action("rules", {}, visual.VisualAction, image="fixture")
    assert error.value.code == "INVALID_DESKTOP_PLAN" and len(calls) == 3


@pytest.mark.anyio
async def test_changed_image_during_confirmation_prevents_dispatch(harness):
    adapter, context, target, _ = harness

    async def confirm(*_):
        adapter.pipe.image = png("red")

    context.confirm = confirm
    result = await visual.run_visual(adapter, context, "Set draft", target)
    assert result.outcome == "blocked"
    assert not adapter.pipe.actions


@pytest.mark.anyio
async def test_input_ack_is_not_success_and_identical_actions_not_repeated(harness):
    adapter, context, target, _ = harness
    adapter.pipe.readback = False
    result = await visual.run_visual(adapter, context, "Set draft", target)
    assert result.outcome == "unknown_effect"
    assert len(adapter.pipe.actions) == 1


@pytest.mark.anyio
async def test_cancellation_while_confirming_never_dispatches(harness):
    adapter, context, target, _ = harness

    async def confirm(*_):
        context.cancelled = True

    context.confirm = confirm
    with pytest.raises(asyncio.CancelledError):
        await visual.run_visual(adapter, context, "Set draft", target)
    assert not adapter.pipe.actions


@pytest.mark.anyio
async def test_jev_rejection_never_sends_input(harness, monkeypatch):
    adapter, context, target, _ = harness
    monkeypatch.setattr(visual.jev_client, "ask", lambda *_: {"answers": {"advance": {"probability": 0.1}}})
    result = await visual.run_visual(adapter, context, "Set draft", target)
    assert result.outcome == "blocked"
    assert not context.confirmations and not adapter.pipe.actions
