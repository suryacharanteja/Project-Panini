import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend import interrupts, ledger
from backend.core_contracts import Principal, ToolResult
from backend.hands.browser_v2 import BrowserHand
from backend.harness.skills import Skills
from backend.harness.store import Store
from backend.interrupts import Interrupts, decided_by
from backend.ledger.artifacts import Artifacts
from backend.ledger.hook import LedgerHook
from backend.ledger.receipts import Ledger
from backend.policy.engine import Policy
from backend.policy.hook import PolicyHook
from backend.registry.context import ToolContext, ToolView
from backend.registry.registry import Registry
from backend.teach.replay import nodes, resolve
from backend.teach.schema import Target
from backend.teach.service import Teach

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def anyio_backend():
    return "asyncio"


class Hand:
    def __init__(self):
        self.label = "Save report"
        self.calls = []
        self.failure = None
        self.gate = None
        self.snapshot = None

    async def call(self, ctx, spec, args):
        self.calls.append((spec.name, args))
        if spec.name == "browser_snapshot":
            return ToolResult(
                content=self.snapshot or f'- textbox "Query" [ref=e1]\n- button "{self.label}" [ref=e2]'
            )
        if spec.name == "browser_click" and self.gate:
            await self.gate.wait()
        if spec.name == "browser_click" and self.failure:
            return self.failure
        if spec.name.startswith("browser_verify_"):
            return ToolResult(evidence=[{"kind": "assertion", "summary": "Report ready"}])
        return ToolResult(content="Dispatched")


@pytest.fixture
def world(tmp_path, monkeypatch):
    for key in ("PANINI_TEACH", "PANINI_V2_LEDGER", "PANINI_V2_INTERRUPTS"):
        monkeypatch.setenv(key, "true")
    monkeypatch.setenv("PANINI_V2_POLICY", "shadow")
    monkeypatch.setenv("PANINI_POLICY_PROFILE", "balanced")
    store = Store(tmp_path / "state.sqlite3")
    h = SimpleNamespace(store=store, skills=Skills(user_root=tmp_path / "skills"), policy=Policy(store))
    h.interrupts = interrupts.install(Interrupts(store))
    h.artifacts = Artifacts(store, tmp_path / "artifacts")
    h.ledger = ledger.install(Ledger(store), h.artifacts)
    h.registry = Registry(
        {"harness": h},
        sources=[
            (ROOT / "backend/registry/manifests", False),
            (ROOT / "panini-core/capabilities/browser/manifests", False),
        ],
    )
    hand = Hand()
    for name in list(h.registry.hands):
        if name.startswith("browser_"):
            h.registry.hands[name] = hand
    h.registry.hooks = [LedgerHook(), PolicyHook(mode="shadow", policy=h.policy)]
    h.model_requests = []

    class Model:
        async def respond(self, instructions, items, tools, **kw):
            h.model_requests.append((instructions, items, tools, kw))
            return SimpleNamespace(calls=[], text=json.dumps({"ref": "e2"}))

    h.model_factory = Model
    h.teach = Teach(h)
    asked = []

    async def confirm(title, detail):
        asked.append((title, detail))
        return True

    ctx = ToolContext(
        run_id="teach-test", view=ToolView(teach=True, managed_browser=True, ledger=True), confirm=confirm
    )
    yield h, hand, ctx, asked
    store.close()


def document():
    return {
        "version": 1,
        "kind": "browser",
        "steps": [
            {
                "id": "s1",
                "tool": "browser_type",
                "target": {"role": "textbox", "name": "Query"},
                "args": {"text": "{{query}}"},
            },
            {
                "id": "s2",
                "tool": "browser_click",
                "target": {"role": "button", "name": "Save report"},
                "args": {},
            },
        ],
        "expect": [{"id": "s3", "tool": "browser_verify_text_visible", "args": {"text": "Report ready"}}],
        "inputs": {"query": {"default": "apples", "kind": "text"}},
    }


def approve(h, review, yes=True):
    h.interrupts.resolve(
        review["id"],
        nonce=review["nonce"],
        action_digest=review["action_digest"],
        status="resolved",
        payload={"approved": yes},
        decided_by=decided_by("avatar", "local"),
    )


def seed(h):
    proposal = h.teach.bundles.propose("report-demo", "Create a report", document())
    approve(h, proposal["review"])
    assert h.teach.bundles.current("report-demo")[0]["version"] == 1
    return proposal


def test_review_is_required_and_plain_edits_cannot_change_replay(world):
    h, *_ = world
    proposal = h.teach.bundles.propose("report-demo", "Create report", document())
    with pytest.raises(ValueError, match="No approved"):
        h.teach.bundles.current("report-demo")
    approve(h, proposal["review"])
    folder = h.teach.bundles.root / "report-demo"
    assert (folder / "CHANGELOG.md").exists()
    with pytest.raises(ValueError, match="reviewed demonstration"):
        h.skills.create("report-demo", "Changed", "Hidden changed instructions")
    (folder / "replay.json").write_text('{"steps": []}', encoding="utf-8")
    assert len(h.teach.bundles.current("report-demo")[1].steps) == 2


def test_competing_versions_cannot_overwrite_approved_base(world):
    h, *_ = world
    seed(h)
    a = h.teach.bundles.propose("report-demo", "Update A", document(), base_version=1)
    b = h.teach.bundles.propose("report-demo", "Update B", document(), base_version=1)
    approve(h, b["review"])
    approve(h, a["review"])
    assert h.teach.bundles.current("report-demo")[0]["version"] == 3
    assert h.store.one("SELECT state FROM teach_versions WHERE version=2")["state"] == "rejected"


@pytest.mark.anyio
async def test_every_step_has_separate_receipt_new_inputs_and_assertions(world):
    h, hand, ctx, asked = world
    seed(h)
    result = await h.registry.call(ctx, "use_skill", {"name": "report-demo", "inputs": {"query": "oranges"}})
    assert result.outcome == "ok" and result.data["state"] == "passed"
    assert any(a.get("text") == "oranges" for n, a in hand.calls if n == "browser_type")
    rows = h.store.all("SELECT tool,outcome FROM receipts ORDER BY started_at")
    assert {r["tool"] for r in rows} >= {
        "use_skill",
        "browser_snapshot",
        "browser_type",
        "browser_click",
        "browser_verify_text_visible",
    }
    assert all(r["outcome"] == "ok" for r in rows)
    assert result.evidence[-1]["kind"] == "assertion"


@pytest.mark.anyio
async def test_healed_label_waits_for_review_and_has_no_tools_or_memory(world):
    h, hand, ctx, _ = world
    seed(h)
    hand.label = "Generate report"
    result = await h.teach.replayer.run(ctx, "report-demo", {"query": "pears"})
    assert result.data["state"] == "passed" and result.data["heals"] == 1
    assert h.teach.bundles.current("report-demo")[0]["version"] == 1
    reviews = h.interrupts.list()
    assert len(reviews) == 1
    assert reviews[0]["preview"]["replay"]["steps"][1]["target"]["name"] == "Generate report"
    assert h.model_requests[0][2] == []
    assert "memory" not in h.model_requests[0][1][0]["content"]
    approve(h, reviews[0])
    assert h.teach.bundles.current("report-demo")[0]["version"] == 2


@pytest.mark.anyio
async def test_unknown_effect_is_never_healed_or_retried(world):
    h, hand, ctx, _ = world
    seed(h)
    hand.failure = ToolResult("unknown_effect", "Driver stopped", dispatched=True)
    result = await h.teach.replayer.run(ctx, "report-demo", {})
    assert result.data["state"] == "unknown_effect"
    assert len([n for n, _ in hand.calls if n == "browser_click"]) == 1
    assert not h.model_requests
    assert not h.interrupts.list()


@pytest.mark.anyio
async def test_pause_stops_effect_even_when_global_policy_is_shadow(world):
    h, hand, ctx, _ = world
    seed(h)
    hand.gate = asyncio.Event()
    task = asyncio.create_task(h.teach.replayer.run(ctx, "report-demo", {}))
    while not any(n == "browser_click" for n, _ in hand.calls):
        await asyncio.sleep(0)
    h.policy.pause(True)
    result = await asyncio.wait_for(task, 2)
    assert result.data["state"] != "passed"
    assert not any(n.startswith("browser_verify") for n, _ in hand.calls)


@pytest.mark.anyio
async def test_invalid_input_does_not_leave_stop_state_active(world):
    h, _, ctx, _ = world
    seed(h)
    with pytest.raises(ValueError):
        await h.teach.replayer.run(ctx, "report-demo", {"bad": "x"})
    assert h.teach.replayer.active is None


def test_refs_and_literal_prefix_resolve_uniquely():
    snap = '- button "Save" [ref=e1]\n- button "Save" [ref=e2]\n- link "Report 2026" [ref=e3]\n- textbox "Passcode" [ref=e4]'
    candidates = nodes(snap)
    assert len(candidates) == 3
    assert resolve(Target(role="button", name="Save"), candidates) is None
    assert resolve(Target(role="link", name_regex="^Report"), candidates)["ref"] == "e3"
    with pytest.raises(ValueError, match="named iframe"):
        Target(name="Save", frame="iframe")


def test_named_frame_resolves_only_its_own_descendants():
    candidates = nodes(
        '- button "Save" [ref=f1e1]\n'
        '- iframe "Report" [ref=f1e2]:\n'
        '  - button "Save" [ref=f2e1]\n'
        '- iframe "Other" [ref=f1e3]:\n'
        '  - button "Save" [ref=f3e1]'
    )
    assert resolve(Target(role="button", name="Save"), candidates)["ref"] == "f1e1"
    target = Target(role="button", name="Save", frame='iframe[name="Report"]')
    assert resolve(target, candidates)["ref"] == "f2e1"
    with pytest.raises(ValueError, match="missing or ambiguous"):
        resolve(Target(role="button", name="Save", frame='iframe[title="Missing"]'), candidates)
    with pytest.raises(ValueError, match="missing or ambiguous"):
        resolve(target, candidates + [dict(candidates[1], ref="f1e4")])


@pytest.mark.anyio
async def test_frame_dom_probe_requires_one_current_named_iframe():
    snapshot = '- iframe [ref=f1e1]:\n  - button "Save" [ref=f2e1]'

    class Session:
        def __init__(self, total, matching):
            self.total, self.matching = total, matching

        async def call(self, name, args, timeout_s):
            assert name == "browser_evaluate" and "Report" in args["function"]
            return {
                "content": [
                    {"type": "text", "text": json.dumps({"total": self.total, "matching": self.matching})}
                ]
            }

    probe = BrowserHand.verified_frame
    assert (
        await probe(None, None, "managed", 'iframe[name="Report"]', snapshot, session=Session(1, 1)) == "f1e1"
    )
    for total, matching in ((2, 1), (1, 0), (1, 2)):
        with pytest.raises(ValueError, match="missing, hidden or ambiguous"):
            await probe(
                None, None, "managed", 'iframe[name="Report"]', snapshot, session=Session(total, matching)
            )
    with pytest.raises(ValueError, match="exactly one visible iframe"):
        await probe(
            None,
            None,
            "managed",
            'iframe[name="Report"]',
            snapshot + "\n- iframe [ref=f1e2]",
            session=Session(2, 1),
        )


@pytest.mark.anyio
async def test_reviewed_named_frame_replays_through_registry(world):
    h, hand, ctx, _ = world
    hand.snapshot = (
        '- iframe "Report" [ref=f1e1]:\n'
        '  - textbox "Query" [ref=f2e1]\n'
        '  - button "Save report" [ref=f2e2]\n'
        '- iframe "Other" [ref=f1e2]:\n'
        '  - button "Save report" [ref=f3e1]'
    )
    payload = document()
    for step in payload["steps"]:
        step["target"]["frame"] = 'iframe[name="Report"]'
    proposal = h.teach.bundles.propose("frame-demo", "Create a framed report", payload)
    approve(h, proposal["review"])
    result = await h.registry.call(ctx, "use_skill", {"name": "frame-demo", "inputs": {}})
    assert result.outcome == "ok" and result.data["state"] == "passed"
    assert any(name == "browser_click" and args["target"] == "f2e2" for name, args in hand.calls)


def test_main_frame_prefix_is_not_an_iframe():
    candidates = nodes(
        '- generic [ref=f1e1]:\n  - button "Save" [ref=f1e2]\n  - iframe [ref=f1e3]:\n    - button "Save" [ref=f2e1]'
    )
    assert resolve(Target(role="button", name="Save"), candidates)["ref"] == "f1e2"


@pytest.mark.anyio
async def test_recording_blocks_replay(world):
    h, _, ctx, _ = world
    seed(h)
    h.teach.recorder.current = {"id": "active-recording"}
    result = await h.teach.replayer.run(ctx, "report-demo", {})
    h.teach.recorder.current = None
    assert result.outcome == "blocked"
    assert not h.store.all("SELECT * FROM skill_runs")


@pytest.mark.anyio
async def test_stop_cancels_driver_start_before_recording(world):
    h, hand, ctx, _ = world
    entered = asyncio.Event()
    closed = []

    class Session:
        preset = SimpleNamespace(flags=[])

        async def call(self, *args):
            entered.set()
            await asyncio.Event().wait()

        async def close(self):
            closed.append(True)

    async def session(*args):
        return Session()

    hand.session = session
    starting = asyncio.create_task(h.teach.recorder.start(ctx, "managed"))
    await asyncio.wait_for(entered.wait(), 2)
    assert h.teach.recorder.status()["state"] == "starting"
    result = await asyncio.wait_for(h.teach.recorder.stop(), 2)
    assert result["state"] == "idle" and closed
    assert starting.cancelled()
    assert h.teach.recorder.starting is None


@pytest.mark.anyio
async def test_stronger_level_and_stop_retract_pending_effect_review(world):
    from backend.panini.contracts import Fault
    from backend.policy.engine import new_grant
    from backend.routes.teach import context

    h, _, _, _ = world
    seed(h)
    h.policy.put_grant(new_grant(tools=("browser_click",), levels={"submit": "local"}))
    task = asyncio.create_task(h.teach.replayer.run(context(h.teach), "report-demo", {}))
    review = None
    for _ in range(1000):
        review = next((r for r in h.interrupts.list() if r["reason"] == "teach_effect"), None)
        if review:
            break
        await asyncio.sleep(0)
    assert review and review["level"] == "local", (review["title"], review["level"], h.policy.grants())
    with pytest.raises(Fault):
        h.interrupts.resolve(
            review["id"],
            nonce=review["nonce"],
            action_digest=review["action_digest"],
            status="resolved",
            payload={"approved": True},
            decided_by=decided_by("phone_app", "tap", device_id="test"),
        )
    await h.teach.replayer.stop()
    assert task.cancelled()
    assert not h.interrupts.list()


@pytest.mark.anyio
async def test_two_repairs_are_the_limit_and_failed_run_never_proposes_patch(world):
    h, hand, ctx, _ = world
    payload = document()
    payload["steps"] = [
        {
            "id": f"s{i}",
            "tool": "browser_click",
            "target": {"role": "button", "name": f"Previous {i}"},
            "args": {},
        }
        for i in range(1, 4)
    ]
    payload["expect"][0]["id"] = "s4"
    proposal = h.teach.bundles.propose("report-demo", "Demo", payload)
    approve(h, proposal["review"])
    result = await h.teach.replayer.run(ctx, "report-demo", {})
    assert result.data["state"] == "failed" and result.data["heals"] == 2
    assert len(h.model_requests) == 2
    assert len([n for n, _ in hand.calls if n == "browser_click"]) == 2
    assert not h.interrupts.list()


@pytest.mark.anyio
async def test_replay_retirement_removes_execution_permission(world):
    h, _, ctx, _ = world
    seed(h)
    h.skills.delete("report-demo")
    with pytest.raises(ValueError, match="No approved"):
        await h.teach.replayer.run(ctx, "report-demo", {})
    assert not h.teach.replayer.active
    assert h.store.one("SELECT state FROM teach_versions")["state"] == "retired"


@pytest.mark.anyio
async def test_external_client_cannot_invoke_private_skill(world):
    h, _, ctx, _ = world
    seed(h)
    ctx.principal = Principal(kind="mcp_client", verified=False)
    result = await h.registry.call(ctx, "use_skill", {"name": "report-demo", "inputs": {}})
    assert result.outcome == "blocked"
    assert not h.store.all("SELECT * FROM skill_runs")


def test_missing_expectation_and_unnamed_frame_cannot_be_saved(world):
    h, *_ = world
    payload = document()
    payload["expect"] = []
    with pytest.raises(ValueError, match="observable expectation"):
        h.teach.bundles.propose("report-demo", "Demo", payload)
    payload = document()
    payload["steps"][0]["target"]["frame"] = "iframe"
    with pytest.raises(ValueError, match="named iframe"):
        h.teach.bundles.propose("report-demo", "Demo", payload)


def test_denied_review_keeps_bundle_unsaved(world):
    h, *_ = world
    proposal = h.teach.bundles.propose("report-demo", "Demo", document())
    approve(h, proposal["review"], False)
    assert not (h.teach.bundles.root / "report-demo").exists()
    assert h.store.one("SELECT state FROM teach_versions")["state"] == "rejected"


@pytest.mark.anyio
async def test_failed_assertion_does_not_save_a_repair(world):
    h, hand, ctx, _ = world
    seed(h)
    hand.label = "Generate report"
    original = hand.call

    async def broken(ctx, spec, args):
        if spec.name.startswith("browser_verify_"):
            return ToolResult("failed", "Expected report missing")
        return await original(ctx, spec, args)

    hand.call = broken
    result = await h.teach.replayer.run(ctx, "report-demo", {})
    assert result.data["state"] == "failed" and result.data["heals"] == 1
    assert not h.interrupts.list()


@pytest.mark.anyio
async def test_owner_decline_prevents_dispatch_even_in_shadow(world):
    h, hand, ctx, _ = world
    seed(h)

    async def decline(*args):
        return False

    ctx.confirm = decline
    result = await h.teach.replayer.run(ctx, "report-demo", {})
    assert result.data["state"] == "failed"
    assert not any(n == "browser_click" for n, _ in hand.calls)


@pytest.mark.anyio
async def test_three_failures_mark_skill_needs_attention(world):
    h, hand, ctx, _ = world
    seed(h)
    hand.failure = ToolResult("failed", "Unavailable")
    for i in range(3):
        ctx.run_id = f"attempt-{i}"
        await h.teach.replayer.run(ctx, "report-demo", {})
    metrics = h.teach.bundles.list()[0]
    assert metrics["needs_attention"] and metrics["runs"] == 3 and metrics["passes"] == 0
