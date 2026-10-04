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

    async def call(self, payload):
        self.calls.append(payload)
        if payload["op"] == "stop":
            return {"stopped": True}
        if payload["op"] == "fine_action":
            if self.gate:
                await self.gate.wait()
            if self.failure:
                raise self.failure
            if "text" in payload["action"]:
                self.value = payload["action"]["text"]
            return {"dispatched": True, "verified": "text" in payload["action"]}
        if payload["op"] != "snapshot":
            raise ValueError("No screenshot transport in this fixture")
        self.generation += 1
        rows = []
        for i, (name, kind, value, states, focused, patterns) in enumerate(
            [
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
        ):
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
            return [{"choice_id": "fixture", "label": "Fixture", "application": "fixture"}]

        async def select(self, choice):
            if choice != "fixture":
                raise ValueError("Unknown window")
            return {"hwnd": 1, "pid": 10, "created": "1", "label": "Fixture", "application": "fixture"}

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
@pytest.mark.parametrize("level", ["verify", "local"])
async def test_legacy_review_cannot_authorize_stronger_desktop_action(world, level):
    from backend.policy.engine import new_grant

    h = world
    h.policy.put_grant(new_grant(tools=("desktop_click",), levels={"communicate": level}))
    session = await snapshot(h)
    result = await h.registry.call(h.ctx, "desktop_click", {"ref": ref(session, "Apply draft")})
    assert result.outcome == "blocked" and "stronger approval" in result.content
    assert not any(c["op"] == "fine_action" for c in h.pipe.calls)
