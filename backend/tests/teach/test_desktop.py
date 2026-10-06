import asyncio
import json

import pytest

from backend import interrupts
from backend.core_contracts import Principal
from backend.harness.skills import Skills
from backend.interrupts import Interrupts, decided_by
from backend.panini.contracts import Fault
from backend.registry.context import ToolView
from backend.teach.desktop_events import normalize, snapshot, target
from backend.teach.replay import nodes, resolve
from backend.teach.schema import Replay, Target
from backend.teach.service import Teach
from backend.tests.hands.test_desktop_fine import world as fine_world  # noqa: F401


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def world(request, tmp_path, monkeypatch):
    h = request.getfixturevalue("fine_world")
    monkeypatch.setenv("PANINI_TEACH", "true")
    monkeypatch.setenv("PANINI_V2_INTERRUPTS", "true")
    h.interrupts = interrupts.install(Interrupts(h.store))
    h.skills = Skills(user_root=tmp_path / "skills")
    h.teach = Teach(h)
    h.ctx.view = ToolView(teach=True, desktop_fine=True, ledger=True)
    h.model_requests = []

    class Model:
        async def respond(self, *args, **kwargs):
            h.model_requests.append((args, kwargs))
            raise AssertionError("This test must not ask the healer")

    h.model_factory = Model
    original = h.pipe.call
    h.record_events = [{"kind": "type", "target": {"type": "Edit", "name": "Draft text"}, "value": "after"}]

    async def transport(payload):
        if payload["op"] == "record_start":
            h.pipe.calls.append(payload)
            return {"recording_id": "native", "before": await original({"op": "snapshot"})}
        if payload["op"] == "record_stop":
            h.pipe.calls.append(payload)
            return {
                "state": "stopped",
                "events": h.record_events,
                "after": await original({"op": "snapshot"}),
            }
        return await original(payload)

    h.pipe.call = transport
    return h


def document():
    return {
        "kind": "desktop",
        "desktop_application": "fixture",
        "steps": [
            {
                "id": "s1",
                "tool": "desktop_type",
                "target": {"role": "textbox", "name": "Draft text"},
                "args": {"text": "{{draft}}"},
            }
        ],
        "expect": [
            {
                "id": "s2",
                "tool": "desktop_verify_value",
                "target": {"role": "textbox", "name": "Draft text"},
                "args": {"value": "{{draft}}"},
            }
        ],
        "inputs": {"draft": {"default": "after"}},
    }


def seed(h, payload=None):
    review = h.teach.bundles.propose("desktop-demo", "Taught Windows task", payload or document())["review"]
    h.interrupts.resolve(
        review["id"],
        nonce=review["nonce"],
        action_digest=review["action_digest"],
        status="resolved",
        payload={"approved": True},
        decided_by=decided_by("avatar", "local"),
    )


@pytest.mark.parametrize(
    "value",
    [
        None,
        {},
        {"type": "Edit", "name": "Draft", "ancestors": None},
        {"type": "Edit", "name": "Draft", "ancestors": [{}]},
        {"type": {}, "name": "Draft"},
        {"type": "Edit", "name": "Draft", "element_id": "native"},
        {"type": "Edit", "name": "Draft", "ancestors": [{"type": "Group", "name": "Card number"}]},
    ],
)
def test_malformed_or_private_selectors_cannot_be_replayed(value):
    assert target(value) is None


@pytest.mark.parametrize(
    "event",
    [
        {"kind": "type", "target": {"type": "Edit", "name": "Password"}, "value": "private value"},
        {"kind": "type", "target": {"type": "Edit", "name": "Draft"}, "value": "sk-proj-" + "a" * 40},
        {"kind": "key", "target": {"type": "Edit", "name": "Draft"}, "value": "Ctrl+V; injected"},
        {"kind": "drag", "target": {"type": "Button", "name": "Go"}},
        {"kind": "type", "target": {"type": "Edit", "name": "Draft"}, "value": "x" * 2001},
    ],
)
def test_unreplayable_events_are_manual_without_values(event):
    replay = normalize([event], "fixture")
    assert replay["steps"][0]["tool"] == "manual"
    assert "value" not in replay["steps"][0] and not replay["inputs"]
    assert "sk-proj" not in json.dumps(replay)


def test_parameterized_desktop_text_and_key_chord_remain_declarative():
    replay = normalize(
        [
            {"kind": "type", "target": {"type": "Edit", "name": "Draft"}, "value": "café 漢字"},
            {"kind": "key", "target": {"type": "Edit", "name": "Draft"}, "value": "Ctrl+Shift+S"},
        ],
        "fixture",
    )
    parsed = Replay.model_validate(replay)
    assert parsed.steps[0].args["text"] == "{{draft}}"
    assert parsed.inputs["draft"].default == "café 漢字"
    assert parsed.steps[1].args == {"key": "Ctrl+Shift+S"}


def test_snapshots_mask_sensitive_ancestors_and_descendants():
    result = snapshot(
        {
            "nodes": [
                {"ref": "w1", "type": "Window", "name": "App", "depth": 0},
                {"ref": "w2", "type": "Group", "name": "Card number", "depth": 1},
                {"ref": "w3", "type": "Edit", "name": "Entry", "value": "private value", "depth": 2},
                {"ref": "w4", "type": "Edit", "name": "Draft", "value": "allowed", "depth": 1},
            ]
        }
    )
    assert "private value" not in result and "Entry" not in result and "allowed" in result
    assert {n["ref"] for n in nodes(result)} == {"w1", "w4"}


@pytest.mark.parametrize(
    "row",
    [
        {"ref": "w1", "element_id": "native"},
        {"ref": "w1", "automation_id": "private"},
        {"ref": "raw", "depth": 0},
        {"ref": "w1", "depth": 4},
        {"ref": "w1", "states": "password"},
        None,
    ],
)
def test_snapshot_rejects_raw_identity_or_invalid_hierarchy(row):
    with pytest.raises(ValueError):
        snapshot({"nodes": [row]})


def test_identical_names_require_unique_reviewed_ancestry():
    candidates = nodes(
        '- window "App" [ref=w1]\n  - group "First" [ref=w2]\n    - button "Save" [ref=w3]\n  - group "Second" [ref=w4]\n    - button "Save" [ref=w5]'
    )
    assert resolve(Target(role="button", name="Save"), candidates) is None
    assert (
        resolve(
            Target(role="button", name="Save", ancestors=[{"role": "group", "name": "Second"}]), candidates
        )["ref"]
        == "w5"
    )


@pytest.mark.parametrize("change", ["raw_ref", "wrong_kind", "no_app", "locator", "private_ancestor"])
def test_desktop_schema_prevents_selector_and_vocabulary_bypass(change):
    data = document()
    if change == "raw_ref":
        data["steps"][0]["args"]["ref"] = "w1"
    elif change == "wrong_kind":
        data["steps"][0]["tool"] = "browser_type"
    elif change == "no_app":
        data["desktop_application"] = ""
    elif change == "locator":
        data["steps"][0]["target"]["locator"] = "#draft"
    else:
        data["steps"][0]["target"]["ancestors"] = [{"role": "group", "name": "Password"}]
    with pytest.raises(ValueError):
        Replay.model_validate(data)


@pytest.mark.parametrize("change", ["later", "duplicate", "target", "extra_arg", "invalid_id"])
def test_desktop_launch_must_be_one_reviewed_first_step(change):
    data = document()
    launch = {"id": "s3", "tool": "desktop_open_application", "args": {"application_id": "fixture-app"}}
    data["steps"].insert(0, launch)
    if change == "later":
        data["steps"].reverse()
    elif change == "duplicate":
        data["steps"].append({**launch, "id": "s4"})
    elif change == "target":
        launch["target"] = {"role": "button", "name": "Launch"}
    elif change == "extra_arg":
        launch["args"]["command"] = "arbitrary.exe"
    else:
        launch["args"]["application_id"] = "../arbitrary"
    with pytest.raises(ValueError):
        Replay.model_validate(data)


@pytest.mark.anyio
async def test_open_and_record_prepends_reviewed_launch_once(world):
    h = world
    reviews = []

    async def approve(title, detail, decision):
        reviews.append((title, detail, decision.effect))
        return True

    h.ctx.confirm_decision = approve
    await h.teach.recorder.start_desktop(h.ctx, "", application_id="fixture-app")
    assert len(reviews) == 1
    assert [p["op"] for p in h.pipe.calls].count("launch") == 1
    result = await h.teach.recorder.stop()
    replay = Replay.model_validate(result["replay"])
    assert [s.tool for s in replay.steps] == ["desktop_open_application", "desktop_type"]
    assert replay.steps[0].args == {"application_id": "fixture-app"}
    assert [s.id for s in replay.steps] == ["s1", "s2"]
    assert not h.desktop_fine.sessions


@pytest.mark.anyio
async def test_denied_open_does_not_start_recording(world):
    h = world

    async def decline(*_):
        return False

    h.ctx.confirm_decision = decline
    with pytest.raises(ValueError):
        await h.teach.recorder.start_desktop(h.ctx, "", application_id="fixture-app")
    assert not any(p["op"] in {"launch", "record_start"} for p in h.pipe.calls)
    assert h.teach.recorder.status()["state"] == "idle"
    assert not h.desktop_fine.sessions


@pytest.mark.anyio
async def test_record_finish_saves_sanitized_evidence_and_releases_context(world):
    h = world
    started = await h.teach.recorder.start_desktop(h.ctx, "fixture")
    assert h.teach.recorder.status()["kind"] == "desktop" and not h.desktop_fine.sessions
    result = await h.teach.recorder.stop()
    assert result["state"] == "draft" and result["replay"]["desktop_application"] == "fixture"
    assert set(result["replay"]["recording_artifacts"]) == {"start", "end"}
    assert h.store.one("SELECT state FROM teach_recordings WHERE id=?", (started["id"],))["state"] == "draft"
    assert h.teach.recorder.status()["state"] == "idle"


@pytest.mark.anyio
@pytest.mark.parametrize("change", ["disabled", "paused", "external", "wrong_window"])
async def test_recording_rejects_invalid_owner_scope_before_hooks(world, monkeypatch, change):
    h = world
    if change == "disabled":
        monkeypatch.setenv("PANINI_DESKTOP_TOOLS", "coarse")
    elif change == "paused":
        h.policy.pause(True)
    elif change == "external":
        h.ctx.principal = Principal(kind="mcp_client")
    with pytest.raises(ValueError):
        await h.teach.recorder.start_desktop(h.ctx, "bad" if change == "wrong_window" else "fixture")
    assert not any(p["op"] == "record_start" for p in h.pipe.calls)
    assert not h.desktop_fine.sessions


@pytest.mark.anyio
async def test_stop_cancels_native_start_and_fences_task(world):
    h = world
    entered, calls = asyncio.Event(), []

    async def transport(payload):
        calls.append(payload)
        if payload["op"] == "record_start":
            entered.set()
            await asyncio.Event().wait()
        return {"stopped": True}

    h.pipe.call = transport
    starting = asyncio.create_task(h.teach.recorder.start_desktop(h.ctx, "fixture"))
    await asyncio.wait_for(entered.wait(), 2)
    assert (await asyncio.wait_for(h.teach.recorder.stop(), 2))["state"] == "idle"
    assert starting.cancelled() and not h.desktop_fine.sessions
    assert calls[-1]["op"] == "stop" and calls[0]["task_id"] == calls[-1]["task_id"]


@pytest.mark.anyio
async def test_database_failure_cleans_up_native_recording(world, monkeypatch):
    h = world
    original = h.store.execute

    def failing(sql, *args):
        if sql.startswith("INSERT INTO teach_recordings"):
            raise RuntimeError("database unavailable")
        return original(sql, *args)

    monkeypatch.setattr(h.store, "execute", failing)
    with pytest.raises(RuntimeError):
        await h.teach.recorder.start_desktop(h.ctx, "fixture")
    assert h.teach.recorder.current is None and h.teach.recorder.starting is None
    assert h.pipe.calls[-1]["op"] == "stop" and not h.desktop_fine.sessions


@pytest.mark.anyio
async def test_stop_after_disable_remains_available(world, monkeypatch):
    h = world
    await h.teach.recorder.start_desktop(h.ctx, "fixture")
    monkeypatch.setenv("PANINI_TEACH", "false")
    result = await h.teach.recorder.stop(discard=True)
    assert result["state"] == "discarded" and h.pipe.calls[-1]["op"] == "stop"


@pytest.mark.anyio
async def test_failed_stop_never_saves_partial_skill(world):
    h = world
    await h.teach.recorder.start_desktop(h.ctx, "fixture")
    original = h.pipe.call

    async def failing(payload):
        if payload["op"] == "record_stop":
            raise Fault("RECORDING_INTERRUPTED", "Provider lost")
        return await original(payload)

    h.pipe.call = failing
    result = await h.teach.recorder.stop()
    assert result["state"] == "interrupted" and result["replay"] == {}
    assert h.pipe.calls[-1]["op"] == "stop"


@pytest.mark.anyio
async def test_desktop_replay_uses_new_inputs_fresh_refs_and_separate_assertions(world):
    h = world
    seed(h)
    result = await h.teach.replayer.run(h.ctx, "desktop-demo", {"draft": "new café 漢字"}, window="fixture")
    assert result.data["state"] == "passed" and h.pipe.value == "new café 漢字"
    assert not h.desktop_fine.sessions and not h.model_requests
    receipts = h.store.all("SELECT tool,outcome FROM receipts")
    assert {r["tool"] for r in receipts} >= {"desktop_type", "desktop_verify_value", "desktop_snapshot"}
    assert all(r["outcome"] == "ok" for r in receipts)


@pytest.mark.anyio
async def test_replay_launches_once_then_binds_new_window(world):
    h = world
    data = document()
    data["steps"].insert(
        0, {"id": "s3", "tool": "desktop_open_application", "args": {"application_id": "fixture-app"}}
    )
    seed(h, data)

    async def approve(*_):
        return True

    h.ctx.confirm_decision = approve
    result = await h.teach.replayer.run(h.ctx, "desktop-demo", {"draft": "launched"})
    assert result.data["state"] == "passed" and h.pipe.value == "launched"
    assert [p["op"] for p in h.pipe.calls].count("launch") == 1
    assert (
        h.store.one("SELECT outcome FROM receipts WHERE tool='desktop_open_application'")["outcome"] == "ok"
    )
    assert not h.desktop_fine.sessions and not h.model_requests


@pytest.mark.anyio
@pytest.mark.parametrize("change", ["title", "same_title_tab"])
async def test_switching_documents_between_steps_stops_before_more_input(world, change):
    h = world
    data = document()
    data["steps"].append({**data["steps"][0], "id": "s3"})
    seed(h, data)
    if change == "same_title_tab":
        h.pipe.selected_tab = "first"
    original = h.pipe.call

    async def changed(payload):
        result = await original(payload)
        if payload["op"] == "fine_action" and payload["action"]["kind"] == "set_value":
            if change == "title":
                h.pipe.title = "Another document in the same window"
            else:
                h.pipe.selected_tab = "second"
        return result

    h.pipe.call = changed
    result = await h.teach.replayer.run(h.ctx, "desktop-demo", {}, window="fixture")
    assert result.data["state"] == "failed"
    assert "changed documents" in result.content
    assert sum(p["op"] == "fine_action" and "text" in p["action"] for p in h.pipe.calls) == 1
    assert not h.model_requests and not h.desktop_fine.sessions


@pytest.mark.anyio
async def test_ambiguous_application_window_never_dispatches(world):
    h = world
    seed(h)

    async def choices():
        return [{"choice_id": w, "application": "fixture", "label": w} for w in ("first", "second")]

    h.adapters["desktop"].targets = choices
    result = await h.teach.replayer.run(h.ctx, "desktop-demo", {})
    assert result.data["state"] == "failed"
    assert not any(p["op"] == "fine_action" for p in h.pipe.calls)
    assert not h.desktop_fine.sessions


@pytest.mark.anyio
async def test_unknown_desktop_effect_is_not_retried_or_healed(world):
    h = world
    seed(h)
    h.pipe.failure = Fault("UNKNOWN_EFFECT", "Worker interrupted")
    result = await h.teach.replayer.run(h.ctx, "desktop-demo", {})
    assert result.data["state"] == "unknown_effect"
    assert len([p for p in h.pipe.calls if p["op"] == "fine_action"]) == 1
    assert not h.model_requests and not h.interrupts.list()


@pytest.mark.anyio
async def test_cleanup_failure_is_not_reported_as_passed(world):
    h = world
    seed(h)
    original = h.pipe.call

    async def failing(payload):
        if payload["op"] == "stop":
            raise Fault("WORKER_FAILED", "Stop failed")
        return await original(payload)

    h.pipe.call = failing
    result = await h.teach.replayer.run(h.ctx, "desktop-demo", {})
    assert result.data["state"] == "failed" and "cleanup" in result.data["failure"]
    assert h.store.one("SELECT state FROM skill_runs")["state"] == "failed"
    assert not h.teach.replayer.active and not h.desktop_fine.sessions


@pytest.mark.anyio
async def test_element_assertion_cannot_match_a_replacement_control(world):
    h = world
    await h.registry.call(h.ctx, "desktop_snapshot", {"window": "fixture"})
    session = h.desktop_fine.sessions[h.ctx.run_id]
    ref = next(n.ref for n, _ in session["nodes"].values() if n.name == "Draft text")
    h.pipe.changed_anchor = True
    result = await h.registry.call(
        h.ctx, "desktop_verify_element", {"ref": ref, "role": "textbox", "name": "Draft text"}
    )
    assert result.outcome == "failed" and result.evidence[0]["passed"] is False


@pytest.mark.anyio
async def test_keyboard_replay_refuses_focus_on_a_private_field(world):
    h = world
    data = document()
    data["steps"][0] = {
        "id": "s1",
        "tool": "desktop_press",
        "target": {"role": "textbox", "name": "Draft text"},
        "args": {"key": "Enter"},
    }
    seed(h, data)
    h.pipe.password_focus = True

    async def confirm(*args):
        return True

    h.ctx.confirm_decision = confirm
    result = await h.teach.replayer.run(h.ctx, "desktop-demo", {})
    assert result.data["state"] == "failed"
    assert not any(p["op"] == "fine_action" and p["action"]["kind"] == "keypress" for p in h.pipe.calls)
    assert not h.desktop_fine.sessions


@pytest.mark.anyio
async def test_replay_cleanup_preserves_invoking_agents_desktop_session(world):
    h = world
    await h.registry.call(h.ctx, "desktop_snapshot", {"window": "fixture"})
    parent = h.desktop_fine.sessions[h.ctx.run_id]
    seed(h)
    result = await h.teach.replayer.run(h.ctx, "desktop-demo", {})
    assert result.data["state"] == "passed"
    assert h.desktop_fine.sessions == {h.ctx.run_id: parent}
    stops = [p for p in h.pipe.calls if p["op"] == "stop"]
    assert stops[-1]["task_id"] == result.data["id"] != h.ctx.run_id
    assert (await h.registry.call(h.ctx, "desktop_snapshot", {})).outcome == "ok"
    assert not getattr(h.ctx, "teach_binding", None)


@pytest.mark.anyio
async def test_run_database_failure_does_not_leave_teach_locked(world, monkeypatch):
    h = world
    seed(h)
    original = h.store.execute

    def failing(sql, *args):
        if sql.startswith("INSERT INTO skill_runs"):
            raise RuntimeError("database unavailable")
        return original(sql, *args)

    monkeypatch.setattr(h.store, "execute", failing)
    result = await h.teach.replayer.run(h.ctx, "desktop-demo", {})
    assert result.outcome == "failed"
    assert not h.teach.replayer.active and not h.desktop_fine.sessions
    assert not any(p["op"] == "fine_action" for p in h.pipe.calls)
    monkeypatch.setattr(h.store, "execute", original)
    assert (await h.teach.replayer.run(h.ctx, "desktop-demo", {})).outcome == "ok"


@pytest.mark.anyio
async def test_recorded_clicks_keep_a_small_png_crop_as_an_artifact_and_drop_anything_else(world):
    import base64

    h = world
    png = base64.b64encode(
        bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c63"
            "f8cfc0f01f0005000201e5274b3f0000000049454e44ae426082"
        )  # fmt: skip
    ).decode()
    h.record_events = [
        {"kind": "click", "target": {"type": "Button", "name": "Apply draft"}, "crop": png},
        {
            "kind": "click",
            "target": {"type": "Button", "name": "Apply draft"},
            "crop": base64.b64encode(b"GIF89a").decode(),
        },
        {"kind": "type", "target": {"type": "Edit", "name": "Draft text"}, "value": "after", "crop": png},
    ]
    await h.teach.recorder.start_desktop(h.ctx, "fixture")
    result = await h.teach.recorder.stop()
    steps = result["replay"]["steps"]
    assert steps[0]["tool"] == "desktop_click" and steps[0]["crop_artifact"]
    row, data = h.artifacts.get(steps[0]["crop_artifact"])
    assert row["mime"] == "image/png" and data.startswith(bytes.fromhex("89504e470d0a1a0a"))
    assert "crop_artifact" not in steps[1]  # Not a PNG: dropped.
    assert "crop_artifact" not in steps[2]  # Only clicks carry crops.
    assert all("_crop" not in step and "crop" not in step for step in steps)  # Never inside the skill itself.
