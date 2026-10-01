"""Drawing on the Windows desktop: strokes, curve fitting, time budgets, review and the cursor overlay."""

import base64
import io
import math
from copy import deepcopy
from types import SimpleNamespace

import pytest
from PIL import Image
from pydantic import ValidationError

from backend.capabilities import desktop
from backend.capabilities import desktop_visual as visual
from backend.panini.contracts import Fault
from backend.tests.panini.test_desktop_adapter import Context


@pytest.fixture
def anyio_backend():
    return "asyncio"


def png(color, size=(100, 100)):
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode()


CAPTURE = {
    "width": 100,
    "height": 100,
    "source_width": 200,
    "source_height": 200,
    "source_x": 0,
    "source_y": 0,
}


def stroke(*points, **flags):
    return {"xy": [value for point in points for value in point], **flags}


def draw(*strokes, **fields):
    return visual.VisualAction(
        kind="draw", explanation="draw", strokes=[visual.Stroke(**s) for s in strokes], **fields
    )


# --- The action contract -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fields",
    [
        {"kind": "draw"},  # No strokes.
        {"kind": "click", "x": 1, "y": 1, "strokes": [{"xy": [1, 1, 2, 2]}]},  # Strokes belong to draw.
    ],
)
def test_draw_contract_rejects_missing_or_misplaced_strokes(fields):
    with pytest.raises(ValidationError):
        visual.VisualAction(explanation="test", **fields)


@pytest.mark.parametrize("xy", [[1, 2, 3], [1, 2], [1, 2, 3, 4, 5, 6, 7], [1, -2, 3, 4], [1, 2, 3, 9000]])
def test_a_stroke_needs_whole_in_range_coordinate_pairs(xy):
    with pytest.raises(ValidationError):
        visual.Stroke(xy=xy)


def test_draw_is_limited_to_a_bounded_number_of_points():
    many = [stroke(*[(i, i) for i in range(80)]) for _ in range(11)]  # 880 points.
    with pytest.raises(ValidationError, match="limited"):
        draw(*many)
    assert draw(*many[:10]).strokes  # 800 is allowed.


def test_drawing_planner_gets_a_budget_large_enough_for_hundreds_of_coordinates():
    assert visual.VisualAction.MAX_TOKENS >= 4000
    # planning.parse reads the budget off the schema, so the default for other schemas is unchanged.
    assert getattr(visual.VisualGoal, "MAX_TOKENS", 1800) == 1800


# --- Curve fitting and the wire format -----------------------------------------------------------


def test_smooth_stroke_becomes_a_dense_in_bounds_curve_through_its_waypoints():
    action = draw(stroke((10, 50), (30, 20), (60, 20), (90, 50), smooth=True))
    (path,) = visual.expand_strokes(action, CAPTURE)
    assert path[0] == (10, 50) and path[-1] == (90, 50)
    assert len(path) > 10  # Far more than the four waypoints.
    assert all(0 <= x < 100 and 0 <= y < 100 for x, y in path)
    for waypoint in [(30, 20), (60, 20)]:  # The curve passes through every waypoint.
        assert min(math.dist(waypoint, point) for point in path) <= 1.5
    steps = [math.dist(a, b) for a, b in zip(path, path[1:], strict=False)]
    assert max(steps) <= 9  # Sampled about 8 px apart.


def test_straight_stroke_keeps_its_corners_and_closed_strokes_return_to_the_start():
    square = draw(stroke((20, 20), (80, 20), (80, 80), (20, 80), smooth=False, closed=True))
    (path,) = visual.expand_strokes(square, CAPTURE)
    assert path == [(20, 20), (80, 20), (80, 80), (20, 80), (20, 20)]
    circle = draw(stroke((50, 20), (80, 50), (50, 80), (20, 50), smooth=True, closed=True))
    (loop,) = visual.expand_strokes(circle, CAPTURE)
    assert loop[0] == loop[-1] and len(loop) > 12


def test_curve_overshoot_is_clamped_inside_the_window():
    # A sharp spike makes a Catmull-Rom curve overshoot; nothing may leave the screenshot.
    action = draw(stroke((0, 0), (99, 0), (0, 99), (99, 99), (0, 0), smooth=True))
    (path,) = visual.expand_strokes(action, CAPTURE)
    assert all(0 <= x <= 99 and 0 <= y <= 99 for x, y in path)


def test_a_single_spot_still_touches_the_canvas():
    (path,) = visual.expand_strokes(draw(stroke((40, 40), (40, 40), smooth=False)), CAPTURE)
    assert len(path) == 2 and path[0] == (40, 40)


def test_huge_drawings_are_coarsened_then_straightened_to_fit_the_workers_limits():
    wide = {"width": 1280, "height": 720, "source_width": 1920, "source_height": 1080}
    zigzag = [
        stroke(*[(20 + (i * 15) % 1200, 20 + (i % 2) * 600) for i in range(80)], smooth=True)
        for _ in range(10)
    ]
    paths = visual.expand_strokes(draw(*zigzag), wide)
    assert sum(len(p) for p in paths) <= visual.MAX_TOTAL_POINTS
    assert all(len(p) <= visual.MAX_STROKE_POINTS for p in paths)


def test_time_estimate_grows_with_length_and_speed_and_reflects_display_scale():
    path = [[(0, 0), (90, 0)]]
    fast, slow = visual.estimate_ms(path, CAPTURE, 3), visual.estimate_ms(path, CAPTURE, 10)
    assert 0 < fast < slow
    same_pixels_larger_window = {**CAPTURE, "source_width": 400}
    assert visual.estimate_ms(path, same_pixels_larger_window, 6) > visual.estimate_ms(path, CAPTURE, 6)


# --- What people review, and what later prompts carry -------------------------------------------


def test_review_and_history_use_a_compact_summary_even_for_the_biggest_drawing():
    biggest = [stroke(*[(i % 90, (i * 7) % 90) for i in range(20)]) for _ in range(40)]  # 800 points.
    proposal = {
        "kind": "draw",
        "explanation": "Draw an elephant",
        "strokes": biggest,
    }
    summary = visual.summarize_action(proposal)
    assert summary["strokes"] == 40 and summary["points"] == 800
    assert set(summary["bounds"]) == {"x0", "y0", "x1", "y1"}
    assert len(str(summary)) < 400  # The 3,900-character confirmation card limit is never near.


def test_drag_summary_keeps_endpoints_not_every_point():
    summary = visual.summarize_action(
        {"kind": "drag", "explanation": "d", "path": [{"x": 1, "y": 2}, {"x": 3, "y": 4}, {"x": 5, "y": 6}]}
    )
    assert (
        summary["from"] == {"x": 1, "y": 2} and summary["to"] == {"x": 5, "y": 6} and summary["points"] == 3
    )


# --- The reference grid ---------------------------------------------------------------------------


def test_grid_annotates_only_the_planning_copy_and_can_be_turned_off(monkeypatch):
    plain = png("white", (400, 300))
    gridded = visual.grid_image(plain)
    with Image.open(io.BytesIO(base64.b64decode(gridded))) as image:
        assert image.size == (400, 300)
        assert image.getpixel((100, 150)) != (255, 255, 255)  # A grid line crosses here.
        assert image.getpixel((150, 150)) == (255, 255, 255)  # Between lines nothing changes.
    monkeypatch.setenv("PANINI_DESKTOP_GRID", "0")
    assert visual.grid_image(plain) == plain
    monkeypatch.delenv("PANINI_DESKTOP_GRID")
    assert visual.grid_image("not an image") == "not an image"  # Never breaks planning.


# --- End to end through the loop -----------------------------------------------------------------


class DrawPipe:
    """A fake broker: a white canvas that turns dark where strokes were drawn."""

    def __init__(self):
        self.calls, self.canvas = [], png("white")
        self.token = 0

    def snapshot(self):
        self.token += 1
        return {
            "snapshot_id": str(self.token),
            "observation": {"observation_id": str(self.token), "foreground": True, "elements": []},
            "capture": {**CAPTURE, "image_base64": self.canvas},
        }

    async def call(self, request):
        self.calls.append(deepcopy(request))
        if request["op"] == "visual_state":
            return self.snapshot()
        self.canvas = png("black")
        return {"dispatched": True, "verified": False, "after": self.snapshot()}


class Overlay:
    def __init__(self):
        self.events = []

    async def __call__(self, phase, message=""):
        self.events.append((phase, message))


@pytest.fixture
def scene(monkeypatch):
    monkeypatch.setenv("PANINI_DESKTOP_GRID", "0")
    pipe, context, overlay = DrawPipe(), Context(), Overlay()
    goal = visual.VisualGoal(
        supported=True, explanation="", field="visual", expected="An elephant is drawn", automation_id=""
    )
    plan = {
        "action": draw(
            stroke((20, 60), (40, 30), (70, 30), (85, 60), smooth=True, closed=True),
            stroke((30, 70), (30, 90), smooth=False),
        )
    }
    seen = SimpleNamespace(confirmations=[])

    async def parse(rules, payload, schema, *, image=None, surface=None):
        if schema is visual.VisualGoal:
            return goal
        if schema is visual.VisualAssessment:
            drawn = pipe.canvas == png("black")
            return visual.VisualAssessment(matched=drawn, evidence="A dark elephant outline fills the canvas")
        return plan["action"]

    async def confirm(action, label, explanation, review):
        seen.confirmations.append((action, label, review))

    context.confirm = confirm
    monkeypatch.setattr(visual, "parse", parse)
    monkeypatch.setattr(
        visual.jev_client,
        "ask",
        lambda *_: {"answers": {"advance": {"probability": 0.99}, "satisfied": {"probability": 0.99}}},
    )
    adapter = SimpleNamespace(pipe=pipe, overlay=overlay)
    return adapter, context, {"label": "Untitled - Paint"}, pipe, overlay, plan, seen


@pytest.mark.anyio
async def test_draw_is_sent_as_dense_paced_strokes_with_a_gesture_budget(scene):
    adapter, context, target, pipe, overlay, _plan, _seen = scene
    result = await visual.run_visual(adapter, context, "draw an elephant", target)
    assert result.outcome == "partial"  # Visual results are assessed, never claimed as exact.
    (sent,) = [c for c in pipe.calls if c["op"] == "visual_action"]
    assert sent["timeout_ms"] == 58000 and sent["snapshot_id"] == "2"
    action = sent["action"]
    assert action["kind"] == "draw" and action["step_ms"] == visual.STEP_MS["normal"]
    assert len(action["strokes"]) == 2
    first = [(p["x"], p["y"]) for p in action["strokes"][0]["path"]]
    assert len(first) > 12 and first[0] == first[-1]  # A smoothed, closed outline.
    assert all(set(p) == {"x", "y"} for p in action["strokes"][1]["path"])
    assert context.dispatched == 1


@pytest.mark.anyio
async def test_the_person_reviews_a_summary_not_hundreds_of_coordinates(scene, monkeypatch):
    adapter, context, target, _pipe, _overlay, _plan, seen = scene
    monkeypatch.setenv("PANINI_APPROVALS", "all")
    await visual.run_visual(adapter, context, "draw an elephant", target)
    ((action, label, review),) = seen.confirmations
    assert action["kind"] == "draw" and action["strokes"] == 2 and "xy" not in review
    assert label == "Untitled - Paint" and len(review) < 3900


@pytest.mark.anyio
async def test_the_cursor_overlay_narrates_thinking_waiting_and_done(scene, monkeypatch):
    adapter, context, target, _pipe, overlay, _plan, _seen = scene
    monkeypatch.setenv("PANINI_APPROVALS", "all")
    await visual.run_visual(adapter, context, "draw an elephant", target)
    phases = [phase for phase, _ in overlay.events]
    assert phases[0] == "thinking" and "waiting" in phases and phases[-1] == "done"
    assert phases.index("waiting") < len(phases) - 1


@pytest.mark.anyio
async def test_a_failed_run_tells_the_overlay_it_stopped(scene):
    adapter, context, target, pipe, overlay, _plan, _seen = scene
    context.cancelled = True
    with pytest.raises(BaseException):  # noqa: B017 -- CancelledError is a BaseException.
        await visual.run_visual(adapter, context, "draw", target)
    assert overlay.events[-1][0] == "stopped"


@pytest.mark.anyio
async def test_an_overlay_that_breaks_never_breaks_the_task(scene):
    adapter, context, target, *_ = scene

    async def broken(phase, message=""):
        raise RuntimeError("overlay exploded")

    # The real adapter swallows overlay failures; run_visual itself must also tolerate a missing hook.
    adapter.overlay = None
    result = await visual.run_visual(adapter, context, "draw an elephant", target)
    assert result.outcome == "partial"


@pytest.mark.anyio
async def test_a_drawing_too_long_for_one_gesture_is_replanned_smaller(scene, monkeypatch):
    adapter, context, target, pipe, _overlay, plan, _seen = scene
    calls = []
    real = visual.estimate_ms

    def estimate(paths, capture, step_ms):
        calls.append(step_ms)
        return visual.MAX_GESTURE_MS + 1 if len(calls) <= 2 else real(paths, capture, step_ms)

    monkeypatch.setattr(visual, "estimate_ms", estimate)
    result = await visual.run_visual(adapter, context, "draw an elephant", target)
    assert result.outcome == "partial"  # The same plan then fits (the estimate relaxes) on the next step.
    assert calls[:2] == [visual.STEP_MS["normal"], visual.STEP_MS["fast"]]


@pytest.mark.anyio
async def test_persistently_oversized_drawings_stop_rather_than_loop(scene, monkeypatch):
    adapter, context, target, pipe, _overlay, _plan, _seen = scene
    monkeypatch.setattr(visual, "estimate_ms", lambda *_: visual.MAX_GESTURE_MS * 2)
    result = await visual.run_visual(adapter, context, "draw an elephant", target)
    assert result.outcome == "blocked" and "too large" in result.message
    assert not [c for c in pipe.calls if c["op"] == "visual_action"]


# --- Full-access defaults ------------------------------------------------------------------------


def test_every_application_is_a_valid_target_by_default(monkeypatch):
    monkeypatch.delenv("PANINI_DESKTOP_APPS", raising=False)
    adapter = desktop.DesktopAdapter(True)
    for name in ("mspaint", "windowsterminal", "cmd", "panini-cli", "chrome", "SomethingNew"):
        assert adapter.permits(name)


def test_an_explicit_application_list_still_restricts(monkeypatch):
    monkeypatch.setenv("PANINI_DESKTOP_APPS", "notepad, mspaint")
    adapter = desktop.DesktopAdapter(True)
    assert adapter.permits("MSPaint") and adapter.permits("notepad") and not adapter.permits("cmd")


def test_broker_time_window_is_short_by_default_and_capped_for_gestures():
    assert desktop.request_window_ms({"op": "state"}) == 8000
    assert desktop.request_window_ms({"timeout_ms": 58000}) == 58000
    assert desktop.request_window_ms({"timeout_ms": 10**9}) == 60000
    assert desktop.request_window_ms({"timeout_ms": 1}) == 1000


@pytest.mark.anyio
async def test_overlay_updates_are_cosmetic_and_never_raise(monkeypatch):
    adapter = desktop.DesktopAdapter(True)
    sent = []

    async def call(payload):
        sent.append(payload)
        raise Fault("UNSUPPORTED_OPERATION", "An older broker does not know overlay.")

    adapter.pipe = SimpleNamespace(call=call)
    await adapter.overlay("thinking", "x" * 300)
    assert sent[0]["op"] == "overlay" and len(sent[0]["message"]) == 80
    monkeypatch.setenv("PANINI_DESKTOP_OVERLAY", "0")
    sent.clear()
    await adapter.overlay("thinking", "hi")
    assert not sent
    assert not await desktop.DesktopAdapter(False).overlay("thinking")


# --- Finding the canvas -----------------------------------------------------------------------------


def window_with_canvas(canvas=(200, 120, 700, 460), size=(800, 500)):
    from PIL import ImageDraw

    image = Image.new("RGB", size, (32, 32, 32))  # A dark app window.
    draw_ = ImageDraw.Draw(image)
    draw_.rectangle(canvas, fill="white")
    draw_.rectangle((20, 20, 44, 44), fill="white")  # A small white swatch that is not the canvas.
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode()


def test_the_canvas_is_the_largest_blank_region_inset_from_its_edges():
    area = visual.blank_area(window_with_canvas())
    assert area is not None
    # Within one detection cell of the true canvas, and inset so strokes stay inside it.
    assert 200 <= area["x0"] <= 216 and 120 <= area["y0"] <= 136
    assert 684 <= area["x1"] <= 700 and 444 <= area["y1"] <= 460


def test_the_canvas_is_still_found_after_something_is_drawn_on_it():
    from PIL import ImageDraw

    with Image.open(io.BytesIO(base64.b64decode(window_with_canvas()))) as image:
        ImageDraw.Draw(image).ellipse((300, 180, 600, 400), outline="black", width=4)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
    area = visual.blank_area(base64.b64encode(buffer.getvalue()).decode())
    assert area and area["x1"] - area["x0"] > 400 and area["y1"] - area["y0"] > 280


def test_no_canvas_hint_when_nothing_large_is_blank_or_the_image_is_unreadable():
    dark = Image.new("RGB", (400, 300), (30, 30, 30))
    buffer = io.BytesIO()
    dark.save(buffer, format="PNG")
    assert visual.blank_area(base64.b64encode(buffer.getvalue()).decode()) is None
    assert visual.blank_area("not an image") is None


def test_the_planner_writes_its_layout_before_the_strokes():
    fields = list(visual.VisualAction.model_json_schema()["properties"])
    assert fields.index("layout") < fields.index("strokes")
    action = draw(stroke((10, 10), (50, 50)), layout="canvas 0,0,100,100; body centre 50,50")
    assert action.layout.startswith("canvas")
    assert visual.VisualAction(kind="click", x=1, y=1, explanation="x").layout == ""


# --- A window that moves on while the model is thinking ------------------------------------------------


def flicker(pipe, flickers):
    """Make the canvas change on its own between the planning view and the view taken just before acting."""
    original, seen = pipe.call, {"states": 0}

    async def call(request):
        if request["op"] == "visual_state":
            seen["states"] += 1
            if seen["states"] in range(2, 2 + flickers):
                pipe.canvas = png("gray") if pipe.canvas == png("white") else png("white")
        return await original(request)

    pipe.call = call


@pytest.mark.anyio
async def test_a_window_that_changed_while_planning_is_observed_again_and_the_task_continues(
    scene, monkeypatch
):
    adapter, context, target, pipe, _overlay, _plan, _seen = scene
    monkeypatch.setenv("PANINI_APPROVALS", "risky")  # The default: nothing was approved on the old view.
    flicker(pipe, 1)
    result = await visual.run_visual(adapter, context, "draw an elephant", target)
    assert result.outcome == "partial", result.message
    assert len([c for c in pipe.calls if c["op"] == "visual_action"]) == 1 and context.dispatched == 1


@pytest.mark.anyio
async def test_a_window_that_never_settles_stops_after_a_bounded_number_of_looks(scene, monkeypatch):
    adapter, context, target, pipe, _overlay, _plan, _seen = scene
    monkeypatch.setenv("PANINI_APPROVALS", "risky")
    flicker(pipe, 50)
    result = await visual.run_visual(adapter, context, "draw an elephant", target)
    assert result.outcome == "blocked" and "changed" in result.message
    assert not [c for c in pipe.calls if c["op"] == "visual_action"]


@pytest.mark.anyio
async def test_a_change_during_the_persons_confirmation_still_stops_the_task(scene, monkeypatch):
    adapter, context, target, pipe, _overlay, _plan, _seen = scene
    monkeypatch.setenv("PANINI_APPROVALS", "all")

    async def confirm(*_):
        pipe.canvas = png("red")  # The screen the person approved is no longer the screen.

    context.confirm = confirm
    result = await visual.run_visual(adapter, context, "draw an elephant", target)
    assert result.outcome == "blocked" and not [c for c in pipe.calls if c["op"] == "visual_action"]
