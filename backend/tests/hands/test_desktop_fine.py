import asyncio
import copy
import hashlib
import json
from types import SimpleNamespace

import pytest

from backend import ledger
from backend.core_contracts import EffectClass, Principal
from backend.hands.desktop_fine import DesktopFine, tree
from backend.harness.store import Store
from backend.ledger.artifacts import Artifacts
from backend.ledger.hook import LedgerHook
from backend.ledger.receipts import Ledger
from backend.panini.contracts import Fault
from backend.policy.engine import Policy
from backend.policy.hook import PolicyHook
from backend.registry.context import ToolContext, ToolView
from backend.registry.manifest import BUILTIN
from backend.registry.registry import Registry


@pytest.fixture
def anyio_backend():
    return "asyncio"


class Pipe:
    def __init__(self):
        self.calls = []
        self.value = "before"
        self.label = "Apply draft"
        self.generation = 0
        self.password_focus = False
        self.foreground = True
        self.failure = None
        self.gate = None
        self.changed_anchor = False
        self.title = "Fixture"
        self.document = False
        self.selected_tab = None
        self.launched = False
        self.launch_visible = True

    async def call(self, payload):
        self.calls.append(payload)
        if payload["op"] == "stop":
            return {"stopped": True}
        if payload["op"] == "launch":
            assert payload["application_id"] == "fixture-app"
            self.launched = True
            return {"application_id": "fixture-app", "application": "fixture"}
        if payload["op"] == "window":
            target = payload["target"]
            return {
                "hwnd": target["hwnd"],
                "pid": target["pid"],
                "created": target["created"],
                "application": target["application"],
                "title": "New Fixture",
            }
        if payload["op"] == "observe":
            return {"observed": True}
        if payload["op"] == "fine_action":
            if self.gate:
                await self.gate.wait()
            if self.failure:
                raise self.failure
            if "text" in payload["action"]:
                self.value = payload["action"]["text"]
            if payload["action"]["kind"] == "activate":
                self.foreground = True
            return {
                "dispatched": True,
                "verified": "text" in payload["action"] or payload["action"]["kind"] == "activate",
            }
        if payload["op"] != "snapshot":
            raise ValueError("No screenshot transport in this fixture")
        self.generation += 1
        rows = []
        controls = [
            ("Fixture", "Window", None, [], False, []),
            (
                "Draft text",
                "Document" if self.document else "Edit",
                self.value,
                [],
                not self.password_focus,
                [] if self.document else ["ValuePatternIdentifiers.Pattern"],
            ),
            (self.label, "Button", None, [], False, ["InvokePatternIdentifiers.Pattern"]),
            ("Password field", "Edit", None, ["password"], self.password_focus, []),
        ]
        if self.selected_tab is not None:
            controls.append(("Untitled", "TabItem", None, ["selected"], False, []))
        for i, (name, kind, value, states, focused, patterns) in enumerate(controls):
            row = {
                "ref": f"w{100 * self.generation + i}",
                "name": name,
                "type": "ControlType." + kind,
                "value": value,
                "states": states,
                "depth": 0 if i == 0 else 1,
                "focused": focused,
                "patterns": patterns,
                "bounds": {"x": i * 100, "y": 50, "width": 80, "height": 30},
            }
            row["anchor"] = hashlib.sha256(f"{i}:{name}:{self.changed_anchor}".encode()).hexdigest().upper()
            if kind == "TabItem":
                row["entity"] = hashlib.sha256(f"tab:{self.selected_tab}".encode()).hexdigest().upper()
            row["identity"] = (
                hashlib.sha256(json.dumps({k: v for k, v in row.items() if k != "ref"}).encode())
                .hexdigest()
                .upper()
            )
            rows.append(row)
        return {
            "nodes": rows,
            "snapshot_id": str(self.generation),
            "foreground": self.foreground,
            "title": self.title,
        }


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("PANINI_DESKTOP_TOOLS", "fine")
    monkeypatch.setenv("PANINI_V2_LEDGER", "true")
    monkeypatch.setenv("PANINI_POLICY_PROFILE", "balanced")
    monkeypatch.setenv("PANINI_V2_POLICY", "shadow")
    pipe = Pipe()

    class Adapter:
        enabled = True

        def __init__(self):
            self.pipe = pipe

        def permits(self, application):
            return application == "fixture"

        async def targets(self):
            rows = [{"choice_id": "fixture", "label": "Fixture", "application": "fixture"}]
            if pipe.launched and pipe.launch_visible:
                rows.append({"choice_id": "new-fixture", "label": "New Fixture", "application": "fixture"})
            return rows

        async def applications(self):
            return [{"application_id": "fixture-app", "label": "Fixture", "application": "fixture"}]

        async def select(self, choice):
            if choice == "new-fixture" and pipe.launched and pipe.launch_visible:
                return {
                    "hwnd": 2,
                    "pid": 11,
                    "created": "2",
                    "label": "New Fixture",
                    "application": "fixture",
                }
            if choice != "fixture":
                raise ValueError("Unknown window")
            return {"hwnd": 1, "pid": 10, "created": "1", "label": "Fixture", "application": "fixture"}

        async def window_identity(self, choice, task_id):
            target = await self.select(choice)
            identity = await self.pipe.call({"op": "window", "task_id": task_id, "target": target})
            return target, identity

    store = Store(tmp_path / "state.sqlite3")
    h = SimpleNamespace(store=store, adapters={"desktop": Adapter()}, policy=Policy(store))
    h.artifacts = Artifacts(store, tmp_path / "artifacts")
    h.ledger = ledger.install(Ledger(store), h.artifacts)
    h.desktop_fine = DesktopFine(h)
    h.registry = Registry({"harness": h}, sources=[(BUILTIN, False)])
    assert not h.registry.errors
    h.registry.hooks = [LedgerHook(), PolicyHook(mode="shadow", policy=h.policy)]
    h.ctx = ToolContext(view=ToolView(desktop_fine=True, ledger=True), run_id="fine-run")
    h.pipe = pipe
    yield h
    ledger._ledger = ledger._artifacts = None
    store.close()


async def snapshot(h, **args):
    result = await h.registry.call(h.ctx, "desktop_snapshot", {"window": "fixture", **args})
    assert result.outcome == "ok", result.content
    return h.desktop_fine.sessions[h.ctx.run_id]


def ref(session, name):
    return next(n.ref for n, _ in session["nodes"].values() if n.name == name)


@pytest.mark.anyio
async def test_catalog_launch_requires_concrete_review_and_verified_new_window(world):
    h = world
    listed = await h.registry.call(h.ctx, "desktop_applications", {})
    assert listed.outcome == "ok" and listed.data["applications"][0]["application_id"] == "fixture-app"
    refused = await h.registry.call(h.ctx, "desktop_open_application", {"application_id": "unknown"})
    assert refused.outcome == "blocked" and not h.pipe.launched
    reviews = []

    async def decline(title, detail, decision):
        reviews.append((json.loads(detail), decision.effect))
        return False

    h.ctx.confirm_decision = decline
    denied = await h.registry.call(h.ctx, "desktop_open_application", {"application_id": "fixture-app"})
    assert denied.outcome == "blocked" and not h.pipe.launched
    assert reviews[0][0]["application_id"] == "fixture-app"
    assert reviews[0][1] == EffectClass.LOCAL_EXEC

    async def approve(*_):
        return True

    h.ctx.confirm_decision = approve
    opened = await h.registry.call(h.ctx, "desktop_open_application", {"application_id": "fixture-app"})
    assert opened.outcome == "ok" and opened.dispatched
    assert opened.data["verified"] and opened.data["window"] == "new-fixture"
    assert [call["op"] for call in h.pipe.calls].count("launch") == 1
    assert [call["op"] for call in h.pipe.calls].count("window") == 1


@pytest.mark.anyio
async def test_launched_application_without_new_window_is_unknown_not_retried(world):
    h = world
    h.pipe.launch_visible = False

    async def approve(*_):
        return True

    h.ctx.confirm_decision = approve
    result = await h.registry.call(h.ctx, "desktop_open_application", {"application_id": "fixture-app"})
    assert result.outcome == "unknown_effect" and result.dispatched
    assert [call["op"] for call in h.pipe.calls].count("launch") == 1


@pytest.mark.anyio
@pytest.mark.parametrize("change", ["pause", "disable", "catalog"])
async def test_launch_rechecks_authority_after_owner_review(world, monkeypatch, change):
    h = world
    original = h.adapters["desktop"].applications

    async def approve(*_):
        if change == "pause":
            h.policy.pause(True)
        elif change == "disable":
            monkeypatch.setenv("PANINI_DESKTOP_TOOLS", "coarse")
        else:

            async def changed():
                return []

            h.adapters["desktop"].applications = changed
        return True

    h.ctx.confirm_decision = approve
    result = await h.registry.call(h.ctx, "desktop_open_application", {"application_id": "fixture-app"})
    assert result.outcome == "blocked" and not h.pipe.launched
    h.adapters["desktop"].applications = original


@pytest.mark.anyio
async def test_shadow_mode_grounds_effect_and_obtains_concrete_review(world):
    h = world
    session = await snapshot(h)
    reviews = []

    async def confirm(title, detail, decision):
        reviews.append((json.loads(detail), decision.effect))
        return True

    h.ctx.confirm_decision = confirm
    result = await h.registry.call(h.ctx, "desktop_click", {"ref": ref(session, "Apply draft")})
    assert result.outcome == "ok" and result.dispatched
    assert reviews[0][1] == EffectClass.COMMUNICATE
    assert reviews[0][0]["application"] == "fixture"
    assert h.ctx.decision.effect == EffectClass.COMMUNICATE
    assert len([c for c in h.pipe.calls if c["op"] == "fine_action"]) == 1
    assert not session["nodes"]


@pytest.mark.anyio
@pytest.mark.parametrize("change", ["decline", "label", "value", "focus", "title", "pause", "disable"])
async def test_review_changes_or_decline_prevent_native_dispatch(world, monkeypatch, change):
    h = world
    session = await snapshot(h)

    async def confirm(*_):
        if change == "label":
            h.pipe.label = "Send a different message"
        elif change == "value":
            h.pipe.value = "changed"
            h.pipe.label = "Apply draft changed"
        elif change == "focus":
            h.pipe.foreground = False
        elif change == "title":
            h.pipe.title = "A different document in the same app"
        elif change == "pause":
            h.policy.pause(True)
        elif change == "disable":
            monkeypatch.setenv("PANINI_DESKTOP_TOOLS", "coarse")
        return change != "decline"

    h.ctx.confirm = confirm
    result = await h.registry.call(h.ctx, "desktop_click", {"ref": ref(session, "Apply draft")})
    assert result.outcome in {"blocked", "cancelled"}
    assert not any(c["op"] == "fine_action" for c in h.pipe.calls)


@pytest.mark.anyio
async def test_type_and_assertions_are_separate_receipted_operations(world):
    h = world
    session = await snapshot(h)
    old_ref = ref(session, "Draft text")
    typed = await h.registry.call(h.ctx, "desktop_type", {"ref": old_ref, "text": "after café 漢字"})
    assert typed.outcome == "ok" and typed.data["verified"]
    assert (
        await h.registry.call(h.ctx, "desktop_type", {"ref": old_ref, "text": "again"})
    ).outcome == "blocked"
    session = await snapshot(h)
    verified = await h.registry.call(
        h.ctx, "desktop_verify_value", {"ref": ref(session, "Draft text"), "value": "after café 漢字"}
    )
    assert verified.outcome == "ok" and verified.evidence[0]["passed"]
    wrong = await h.registry.call(h.ctx, "desktop_verify_text", {"text": "absent result"})
    assert wrong.outcome == "failed" and wrong.evidence[0]["passed"] is False
    receipts = h.store.all("SELECT tool FROM receipts")
    assert {r["tool"] for r in receipts} >= {"desktop_type", "desktop_verify_value", "desktop_verify_text"}


@pytest.mark.anyio
async def test_value_assertion_does_not_match_replacement_control(world):
    h = world
    session = await snapshot(h)
    old_ref = ref(session, "Draft text")
    h.pipe.changed_anchor = True
    result = await h.registry.call(h.ctx, "desktop_verify_value", {"ref": old_ref, "value": "before"})
    assert result.outcome == "failed"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "name,args",
    [("desktop_type", {"text": "secret"}), ("desktop_click", {}), ("desktop_press", {"key": "A"})],
)
async def test_password_ref_or_focus_is_never_acted_on(world, name, args):
    h = world
    h.pipe.password_focus = True
    session = await snapshot(h)
    if name != "desktop_press":
        args = {**args, "ref": ref(session, "Password field")}
    result = await h.registry.call(h.ctx, name, args)
    assert result.outcome == "blocked"
    assert not any(c["op"] == "fine_action" for c in h.pipe.calls)


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["external_agent", "mcp_client", "channel_user"])
async def test_external_context_cannot_use_owner_desktop(world, kind):
    h = world
    h.ctx.principal = Principal(kind=kind)
    result = await h.registry.call(h.ctx, "desktop_windows", {})
    assert result.outcome == "blocked"
    assert not h.pipe.calls


@pytest.mark.anyio
async def test_unverified_owner_cannot_read_the_desktop(world):
    h = world
    h.ctx.principal = Principal(verified=False)
    result = await h.registry.call(h.ctx, "desktop_windows", {})
    assert result.outcome == "blocked"
    assert not h.pipe.calls


@pytest.mark.anyio
async def test_unknown_dispatch_is_not_retried(world):
    h = world
    session = await snapshot(h)
    h.pipe.failure = Fault("UNKNOWN_EFFECT", "Disconnected")
    result = await h.registry.call(h.ctx, "desktop_type", {"ref": ref(session, "Draft text"), "text": "new"})
    assert result.outcome == "unknown_effect" and result.dispatched
    assert len([c for c in h.pipe.calls if c["op"] == "fine_action"]) == 1


@pytest.mark.anyio
async def test_pause_cancels_native_work_and_sends_stop(world):
    h = world
    session = await snapshot(h)
    h.pipe.gate = asyncio.Event()
    pending = asyncio.create_task(
        h.registry.call(h.ctx, "desktop_type", {"ref": ref(session, "Draft text"), "text": "new"})
    )
    for _ in range(50):
        if any(c["op"] == "fine_action" for c in h.pipe.calls):
            break
        await asyncio.sleep(0.01)
    assert any(c["op"] == "fine_action" for c in h.pipe.calls)
    h.policy.pause(True)
    result = await asyncio.wait_for(pending, 2)
    assert result.outcome == "cancelled"
    for _ in range(20):
        if any(c["op"] == "stop" for c in h.pipe.calls):
            break
        await asyncio.sleep(0.01)
    assert any(c["op"] == "stop" for c in h.pipe.calls)


@pytest.mark.anyio
async def test_tree_refuses_unbounded_or_native_id_payloads(world):
    page = await world.pipe.call({"op": "snapshot"})
    unsafe = copy.deepcopy(page)
    unsafe["nodes"][0]["element_id"] = "native-ui-id"
    with pytest.raises(ValueError, match="opaque-ref"):
        tree(unsafe)
    with pytest.raises(ValueError, match="invalid tree"):
        tree({"nodes": page["nodes"] * 151})


@pytest.mark.anyio
async def test_stronger_local_approval_is_preserved(world):
    from backend.core_contracts import ApprovalLevel
    from backend.policy.engine import new_grant

    h = world
    h.policy.put_grant(new_grant(tools=("desktop_click",), levels={"communicate": "local"}))
    session = await snapshot(h)
    decisions = []

    async def confirm(title, detail, decision):
        decisions.append(decision)
        return False

    h.ctx.confirm_decision = confirm
    result = await h.registry.call(h.ctx, "desktop_click", {"ref": ref(session, "Apply draft")})
    assert result.outcome == "blocked"
    assert decisions[0].level == ApprovalLevel.LOCAL
    assert not any(c["op"] == "fine_action" for c in h.pipe.calls)


@pytest.mark.anyio
@pytest.mark.parametrize("character", ["\t", "\n", "\r", "\x85"])
async def test_document_navigation_characters_do_not_move_focus_and_continue_typing(world, character):
    h = world
    h.pipe.document = True
    session = await snapshot(h)
    result = await h.registry.call(
        h.ctx, "desktop_type", {"ref": ref(session, "Draft text"), "text": "before" + character + "after"}
    )
    assert result.outcome == "blocked" and not result.dispatched
    assert not any(c["op"] == "fine_action" for c in h.pipe.calls)


@pytest.mark.anyio
async def test_document_replacement_is_explicit_and_reviewed(world):
    h = world
    h.pipe.document = True
    session = await snapshot(h)
    result = await h.registry.call(
        h.ctx, "desktop_type", {"ref": ref(session, "Draft text"), "text": "new", "replace": True}
    )
    assert result.outcome == "ok"
    action = next(c for c in reversed(h.pipe.calls) if c["op"] == "fine_action")
    assert action["action"] == {"kind": "fine_type", "text": "new", "replace": True}
    assert h.ctx.desktop_policy[1]["replace"] is True


@pytest.mark.anyio
async def test_observation_never_activates_window_and_explicit_activation_invalidates_refs(world):
    h = world
    h.pipe.foreground = False
    session = await snapshot(h)
    old_ref = ref(session, "Draft text")
    assert not any(c["op"] == "fine_action" for c in h.pipe.calls)
    result = await h.registry.call(h.ctx, "desktop_activate", {"window": "fixture"})
    assert result.outcome == "ok" and result.data["verified"]
    action = next(c for c in reversed(h.pipe.calls) if c["op"] == "fine_action")
    assert action["action"] == {"kind": "activate"}
    assert h.pipe.foreground
    result = await h.registry.call(h.ctx, "desktop_type", {"ref": old_ref, "text": "never"})
    assert result.outcome == "blocked" and not result.dispatched


@pytest.mark.anyio
async def test_activation_rechecks_window_after_review(world):
    from backend.policy.engine import new_grant

    h = world
    h.policy.put_grant(new_grant(tools=("desktop_activate",), levels={"navigate": "tap"}))

    async def confirm(*_):
        h.pipe.title = "Replaced document"
        return True

    h.ctx.confirm_decision = confirm
    result = await h.registry.call(h.ctx, "desktop_activate", {"window": "fixture"})
    assert result.outcome == "blocked" and not result.dispatched
    assert not any(c["op"] == "fine_action" for c in h.pipe.calls)


@pytest.mark.anyio
@pytest.mark.parametrize("level", ["verify", "local"])
async def test_legacy_review_cannot_authorize_stronger_desktop_action(world, level):
    from backend.policy.engine import new_grant

    h = world
    h.policy.put_grant(new_grant(tools=("desktop_click",), levels={"communicate": level}))
    session = await snapshot(h)
    result = await h.registry.call(h.ctx, "desktop_click", {"ref": ref(session, "Apply draft")})
    assert result.outcome == "blocked" and "stronger approval" in result.content
    assert not any(c["op"] == "fine_action" for c in h.pipe.calls)


@pytest.mark.anyio
async def test_type_and_submit_is_one_submit_effect_then_enter_in_the_same_field(world):
    h = world
    session = await snapshot(h)
    result = await h.registry.call(
        h.ctx, "desktop_type", {"ref": ref(session, "Draft text"), "text": "weather today", "submit": True}
    )
    assert result.outcome == "ok", result.content
    assert result.data["submitted"] is True
    assert (
        h.ctx.decision.effect == EffectClass.SUBMIT
    )  # Classified (and reviewed, where policy asks) as a submit.
    actions = [c for c in h.pipe.calls if c["op"] == "fine_action"]
    assert actions[-1]["action"] == {"kind": "keypress", "key": "Enter"}
    assert actions[-2]["action"]["text"] == "weather today"
    assert actions[-1]["ref"] == actions[-2]["ref"] or actions[-1]["target"] == actions[-2]["target"]


@pytest.mark.anyio
async def test_declined_type_and_submit_sends_nothing(world, monkeypatch):
    h = world
    session = await snapshot(h)
    monkeypatch.setenv(
        "PANINI_POLICY_PROFILE", "cautious"
    )  # Ask before every action, so the owner can decline.

    async def confirm(title, detail, decision):
        return False

    h.ctx.confirm_decision = confirm
    result = await h.registry.call(
        h.ctx, "desktop_type", {"ref": ref(session, "Draft text"), "text": "x", "submit": True}
    )
    assert result.outcome == "blocked", result.content
    assert not any(c["op"] == "fine_action" for c in h.pipe.calls)


@pytest.mark.anyio
async def test_draw_macro_hands_the_subject_to_the_visual_planner_for_the_selected_window(world):
    h = world
    asked = []

    async def delegate(capability, instruction):
        asked.append((capability, instruction))
        return {"outcome": "partial", "message": "Drew an elephant.", "results": [], "evidence_ids": []}

    h.ctx.delegate = delegate
    blocked = await h.registry.call(h.ctx, "desktop_draw", {"subject": "a simple elephant"})
    assert blocked.outcome == "blocked" and not asked  # No window selected yet.
    session = await snapshot(h)
    drawn = await h.registry.call(h.ctx, "desktop_draw", {"subject": "a simple elephant"})
    assert drawn.outcome == "ok" and drawn.data["outcome"] == "partial"
    assert asked == [("desktop", f"In {session['target']['label']}, draw a simple elephant.")]
    assert not session["nodes"]  # Canvas refs are stale after drawing.


@pytest.mark.anyio
async def test_drag_goes_from_one_reviewed_control_to_another(world):
    h = world
    session = await snapshot(h)
    source, destination = ref(session, "Draft text"), ref(session, "Apply draft")
    same = await h.registry.call(h.ctx, "desktop_drag", {"ref": source, "to_ref": source})
    assert same.outcome == "blocked"
    reviews = []

    async def confirm(title, detail, decision):
        reviews.append(json.loads(detail[detail.index("{") :])["drop_element"])
        return True

    h.ctx.confirm_decision = confirm
    session = await snapshot(h)
    source, destination = ref(session, "Draft text"), ref(session, "Apply draft")
    result = await h.registry.call(h.ctx, "desktop_drag", {"ref": source, "to_ref": destination})
    assert result.outcome == "ok", result.content
    action = next(c for c in reversed(h.pipe.calls) if c["op"] == "fine_action")["action"]
    assert action["kind"] == "drag" and {"from_x", "from_y", "to_x", "to_y"} <= set(action)
    assert (action["from_x"], action["from_y"]) != (action["to_x"], action["to_y"])
    assert h.ctx.desktop_policy[1]["drop_element"] == "Apply draft"
    assert reviews == ["Apply draft"]  # The review names where the drag ends.


@pytest.mark.anyio
async def test_typing_in_an_owned_dialog_sends_real_keystrokes_replacing_the_field(world):
    # Save As / Open dialogs ignore ValuePattern writes: the typed name must arrive as keystrokes.
    h = world
    adapter = h.adapters["desktop"]
    plain = adapter.select

    async def dialog(choice):
        return {**await plain(choice), "owner_hwnd": 99}

    adapter.select = dialog
    session = await snapshot(h)
    typed = await h.registry.call(
        h.ctx, "desktop_type", {"ref": ref(session, "Draft text"), "text": "report.txt"}
    )
    assert typed.outcome == "ok"
    sent = [p["action"] for p in h.pipe.calls if p["op"] == "fine_action" and "text" in p["action"]]
    assert (
        sent[-1]["kind"] == "fine_type" and sent[-1]["replace"] is True and sent[-1]["text"] == "report.txt"
    )
