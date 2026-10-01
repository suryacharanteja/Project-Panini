import asyncio

import pytest

from backend.extension_bridge import ExtensionBridge, ExtensionUnavailable
from backend.extension_loop import ExtensionLoop
from backend.extension_planner import (
    Candidate,
    Evidence,
    Proposal,
    TaskAssessment,
    TaskPlan,
    completion_evidence,
    ground_candidates,
)
from backend.voice_service import workspace_evidence


def hello(tab=7, revision="r1", allow=True):
    return {
        "type": "hello",
        "protocol": 4,
        "version": "3.0.0",
        "tab": tab,
        "attachment_id": f"grant-{tab}",
        "workspace": {
            "id": "w1",
            "revision": revision,
            "selected_tab": tab,
            "allow_new_tabs": allow,
            "tabs": [
                {"id": 7, "title": "First", "url": "https://example.com/first"},
                {"id": 8, "title": "Second", "url": "https://example.com/second"},
            ],
        },
    }


def test_workspace_switch_preserves_bound_task_and_updates_per_tab_grant():
    async def run():
        bridge = ExtensionBridge()
        messages = []

        class Socket:
            async def send_json(self, msg):
                messages.append(msg)
                if msg["action"]["kind"] == "select_tab":
                    bridge.on_message(hello(8))
                bridge.on_message(
                    {
                        "type": "result",
                        "id": msg["id"],
                        "ok": True,
                        "result": {"actions": [], "url": "https://example.com/second"},
                    }
                )

        await bridge.attach(Socket())
        bridge.on_message(hello())
        target = bridge.target_id
        assert bridge.compatible
        with bridge.bind_target(target):
            await bridge.select_tab(8)
            assert bridge.target_id == target
            page = await bridge.snapshot()
        assert messages[0]["action"]["expected_tab"] == 7
        assert messages[1]["action"]["expected_tab"] == 8
        assert messages[1]["action"]["expected_attachment"] == "grant-8"
        assert messages[1]["action"]["expected_workspace"] == "w1"
        assert {a["id"] for a in page["actions"]} == {"workspace_tab_7", "workspace_open_tab"}

    asyncio.run(run())


def test_manual_workspace_change_invalidates_pending_and_future_bound_actions():
    async def run():
        bridge = ExtensionBridge()
        sent = asyncio.Event()

        class Socket:
            async def send_json(self, msg):
                sent.set()

        await bridge.attach(Socket())
        bridge.on_message(hello())
        with bridge.bind_target(bridge.target_id):
            pending = asyncio.create_task(bridge.text())
            await sent.wait()
            bridge.on_message(hello(8, revision="r2"))
            with pytest.raises(ExtensionUnavailable, match="changed"):
                await pending
            with pytest.raises(ExtensionUnavailable, match="changed"):
                await bridge.select_tab(7)

    asyncio.run(run())


def test_stop_sends_cancellation_to_extension_worker():
    async def run():
        bridge = ExtensionBridge()
        sent = asyncio.Event()
        messages = []

        class Socket:
            async def send_json(self, msg):
                messages.append(msg)
                sent.set()

        await bridge.attach(Socket())
        bridge.on_message(hello())
        with bridge.bind_target(bridge.target_id):
            pending = asyncio.create_task(bridge.navigate("https://example.com"))
            await sent.wait()
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
        assert messages[1] == {"type": "cancel", "id": messages[0]["id"]}
        assert not bridge._pending

    asyncio.run(run())


def test_workspace_actions_are_grounded_in_authorized_capabilities():
    candidates = [
        Candidate(action_id="workspace_open_tab", url="https://example.com", text="", rationale="Research"),
        Candidate(action_id="workspace_tab_8", url="", text="", rationale="Compare"),
        Candidate(action_id="workspace_tab_99", url="", text="", rationale="Invented"),
    ]
    assert ground_candidates(candidates, {"actions": []}) == []
    actions = [
        {"id": "workspace_open_tab", "kind": "open_tab"},
        {"id": "workspace_tab_8", "kind": "select_tab", "tab_id": 8},
    ]
    grounded = ground_candidates(candidates, {"actions": actions})
    assert len(grounded) == 2
    assert grounded[0]["url"] == "https://example.com"
    assert grounded[1]["tab_id"] == 8
    candidates[0].url = "javascript:alert(1)"
    assert len(ground_candidates(candidates, {"actions": actions})) == 1


def test_malformed_workspace_never_falls_back_to_single_tab_authority():
    bridge = ExtensionBridge()
    for value in ({}, {**hello()["workspace"], "tabs": []}, {**hello()["workspace"], "selected_tab": 999}):
        bridge.on_message({**hello(), "workspace": value})
        assert bridge.attached_tab is None
        assert not bridge.target_id


def test_workspace_permission_is_not_inferred_from_protocol_or_page_content():
    async def run():
        bridge = ExtensionBridge()

        class Socket:
            async def send_json(self, msg):
                bridge.on_message({"type": "result", "id": msg["id"], "ok": True, "result": {"actions": []}})

        await bridge.attach(Socket())
        bridge.on_message(hello(allow=False))
        with bridge.bind_target(bridge.target_id):
            page = await bridge.snapshot()
        assert [a["kind"] for a in page["actions"]] == ["select_tab"]
        bridge.on_message({**hello(), "protocol": 2, "workspace": None})
        with bridge.bind_target(bridge.target_id):
            assert (await bridge.snapshot())["actions"] == []

    asyncio.run(run())


def test_workspace_loop_dispatches_grounded_actions_without_dom_clicks():
    async def run():
        class Bridge:
            async def select_tab(self, tab_id):
                return {"selected": tab_id}

            async def open_tab(self, url):
                return {"opened": url}

        loop = ExtensionLoop(Bridge())
        assert await loop._execute({"kind": "select_tab", "tab_id": 8}, "") == {"selected": 8}
        assert await loop._execute({"kind": "open_tab", "url": "https://example.com"}, "") == {
            "opened": "https://example.com"
        }

    asyncio.run(run())


def test_completion_accepts_observed_workspace_evidence_without_repeating_finished_subtasks():
    page = {
        "workspace": hello()["workspace"],
        "tab_id": 7,
        "url": "https://example.com/first",
        "text": "Alpha finished.",
        "actions": [],
        "visited_pages": [
            {"tab_id": 8, "url": "https://example.com/second", "text": "Beta has been reviewed."}
        ],
    }
    proposal = Proposal(
        [],
        TaskAssessment(
            status="complete",
            message="Both finished",
            searches=[],
            evidence=[
                Evidence(source="text", quote="Alpha finished."),
                Evidence(source="text", quote="Beta has been reviewed."),
            ],
        ),
        {},
    )
    plan = TaskPlan(objective="Review both", searches=[])
    assert completion_evidence(plan, proposal, page, {})
    assert "Beta has been reviewed." in workspace_evidence(page)["text"]
    assert "https://example.com/second" in [a["href"] for a in workspace_evidence(page)["actions"]]
    # A tab never observed, or removed from the grant, cannot establish completion.
    page["visited_pages"][0]["tab_id"] = 99
    assert not completion_evidence(plan, proposal, page, {})
    assert "Beta has been reviewed." not in workspace_evidence(page)["text"]
    page["workspace"] = None
    assert not completion_evidence(plan, proposal, page, {})
