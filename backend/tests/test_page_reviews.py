import pytest

from backend.harness.runtime import Harness
from backend.harness.skills import Skills
from backend.harness.store import Store
from backend.interrupts import Parked, decided_by
from backend.spaces import SpacesError
from backend.spaces.reviews import review


@pytest.fixture
def h(tmp_path, monkeypatch):
    monkeypatch.setenv("PANINI_SPACES", "true")
    monkeypatch.setenv("PANINI_V2_INTERRUPTS", "true")
    h = Harness(Store(tmp_path / "h.db"), skills=Skills(user_root=tmp_path / "skills"))
    yield h
    h.store.close()


def draft(h, operation, persona="panini", call="draft-1"):
    return review(h, operation, persona_id=persona, run_id="run-1", tool_call_id=call)


def decide(h, interrupt_id, approved=True, **extra):
    view = h.interrupts.get(interrupt_id, include_secret=True)
    return h.interrupts.resolve(
        interrupt_id,
        nonce=view["nonce"],
        action_digest=view["action_digest"],
        status="resolved",
        payload={"approved": approved, **extra},
        decided_by=decided_by("agui", "tap"),
    )


def test_review_replay_edited_approval_and_decline(h):
    s = h.spaces.create_space("Work")
    op = {"kind": "create", "space_id": s["id"], "title": "Draft", "content": '"' * 20000}
    with pytest.raises(Parked) as pending:
        draft(h, op)
    iid = pending.value.interrupt_id
    assert h.interrupts.get(iid)["preview"]["content"] == op["content"]
    assert not h.spaces.pages(s["id"])
    with pytest.raises(Parked) as retry:
        draft(h, op)
    assert retry.value.interrupt_id == iid
    decide(h, iid, content="Owner edited this draft")
    result = draft(h, op)
    assert result["page"]["content"] == "Owner edited this draft"
    assert draft(h, op) == result
    assert len(h.spaces.pages(s["id"])) == 1
    with pytest.raises(Parked) as declined:
        draft(h, op, call="draft-2")
    decide(h, declined.value.interrupt_id, False)
    assert draft(h, op, call="draft-2")["saved"] is False
    assert len(h.spaces.pages(s["id"])) == 1


def test_cas_and_access_rechecked_after_approval(h):
    s = h.spaces.create_space("Work")
    h.personas.create("writer")
    h.personas.set_permissions("writer", ["spaces_read", "spaces_write"])
    h.spaces.grant(s["id"], "writer", True)
    p = h.spaces.create(s["id"], "Page", "Original")
    op = {"kind": "edit", "page_id": p["id"], "expected_revision": 1, "content": "Proposed"}
    with pytest.raises(Parked) as pending:
        draft(h, op, "writer")
    decide(h, pending.value.interrupt_id)
    h.spaces.edit(p["id"], 1, content="Concurrent")
    with pytest.raises(SpacesError, match="Page changed"):
        draft(h, op, "writer")
    assert h.spaces.read(p["id"])["content"] == "Concurrent"
    h.spaces.grant(s["id"], "writer", False)
    with pytest.raises(SpacesError, match="cannot access"):
        draft(h, op, "writer")


def test_permission_revocation_and_pause_refuse_save(h):
    s = h.spaces.create_space("Work")
    h.personas.create("writer")
    h.personas.set_permissions("writer", ["spaces_write"])
    h.spaces.grant(s["id"], "writer", True)
    op = {"kind": "create", "space_id": s["id"], "title": "Draft", "content": "Proposed"}
    with pytest.raises(Parked) as pending:
        draft(h, op, "writer")
    decide(h, pending.value.interrupt_id)
    h.personas.set_permissions("writer", [])
    with pytest.raises(SpacesError, match="no longer has permission"):
        draft(h, op, "writer")
    assert not h.spaces.pages(s["id"])
    h.personas.set_permissions("writer", ["spaces_write"])
    h.policy.pause(True)
    with pytest.raises(SpacesError, match="paused"):
        draft(h, op, "writer")
    assert not h.spaces.pages(s["id"])


def test_reviewed_move_delete_are_cas_and_replay_safe(h):
    space = h.spaces.create_space("Move qualification")
    parent = h.spaces.create(space["id"], "Parent")
    page = h.spaces.create(space["id"], "Page", "Text kept")
    operation = {"kind": "move", "page_id": page["id"], "expected_revision": 1, "parent_id": parent["id"]}
    with pytest.raises(Parked) as pending:
        draft(h, operation, call="move")
    assert h.spaces.read(page["id"])["parent_id"] is None
    decide(h, pending.value.interrupt_id)
    moved = draft(h, operation, call="move")
    assert moved["page"]["parent_id"] == parent["id"]
    assert draft(h, operation, call="move") == moved
    operation = {"kind": "delete", "page_id": page["id"], "expected_revision": 2}
    with pytest.raises(Parked) as pending:
        draft(h, operation, call="trash")
    decide(h, pending.value.interrupt_id)
    deleted = draft(h, operation, call="trash")
    assert deleted["page"]["deleted_at"] and deleted["page"]["content"] == "Text kept"
    assert draft(h, operation, call="trash") == deleted


def test_exact_patch_review_and_replay_never_applies_twice(h):
    space = h.spaces.create_space("Patches")
    page = h.spaces.create(space["id"], "Plan", "Launch on Friday.")
    operation = {
        "kind": "edit",
        "page_id": page["id"],
        "expected_revision": 1,
        "patch": {"find": "Friday", "replace": "Monday"},
    }
    with pytest.raises(Parked) as pending:
        draft(h, operation)
    preview = h.interrupts.get(pending.value.interrupt_id)["preview"]
    assert preview["content"] == "Launch on Monday." and preview["previous_content"] == "Launch on Friday."
    assert "-Launch on Friday." in preview["diff"] and "+Launch on Monday." in preview["diff"]
    decide(h, pending.value.interrupt_id)
    saved = draft(h, operation)
    assert saved["page"]["content"] == "Launch on Monday."
    assert draft(h, operation) == saved
    assert h.spaces.read(page["id"])["revision"] == 2
    invalid = {**operation, "expected_revision": 2, "patch": {"find": "missing", "replace": "no"}}
    with pytest.raises(SpacesError, match="exactly once"):
        draft(h, invalid, call="invalid")
