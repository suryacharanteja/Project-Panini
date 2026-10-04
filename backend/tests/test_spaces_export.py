import asyncio
import json

import pytest

from backend.harness.runtime import Harness
from backend.harness.skills import Skills
from backend.harness.store import Store
from backend.spaces import SpacesError
from backend.spaces.export import pdf, save_source


@pytest.fixture
def notebook(tmp_path):
    h = Harness(Store(tmp_path / "h.db"), skills=Skills(user_root=tmp_path / "skills"))
    space = h.spaces.create_space("Exports")
    yield h, space["id"]
    h.store.close()


def completed(h, text="Durable result", state="completed"):
    thread = h.sessions.create()
    h.sessions.save(
        thread, [{"role": "user", "content": "Question"}, {"type": "message", "content": [{"text": text}]}]
    )
    run = h.runs.start(mode="foreground", principal={}, thread_id=thread)
    h.runs.finish(run, state, {"outcome": "verified_complete", "message": text})
    return run


def test_result_retries_and_deleted_destination_keep_original(notebook):
    h, space = notebook
    run = completed(h)
    page = save_source(h, space_id=space, kind="result", source_id=run)
    assert "Durable result" in page["content"]
    h.spaces.edit(page["id"], 1, content="Owner edits")
    replay = save_source(h, space_id=space, kind="result", source_id=run)
    assert replay["id"] == page["id"] and replay["content"] == "Owner edits"
    h.spaces.edit(page["id"], 2, trash=True)
    assert save_source(h, space_id=space, kind="result", source_id=run)["deleted_at"]
    assert h.store.one("SELECT count(*) AS n FROM pages")["n"] == 1


def test_failed_malformed_oversized_history_creates_nothing(notebook):
    h, space = notebook
    for state in ("running", "failed", "partial", "blocked"):
        run = completed(h, state=state)
        with pytest.raises(SpacesError):
            save_source(h, space_id=space, kind="history", source_id=run)
    run = completed(h)
    h.store.execute("UPDATE runs SET result=? WHERE id=?", ("{invalid", run))
    with pytest.raises(SpacesError):
        save_source(h, space_id=space, kind="result", source_id=run)
    run = completed(h)
    thread = h.runs.get(run)["thread_id"]
    h.sessions.save(thread, [{"role": "user", "content": "x" * 200001}])
    with pytest.raises(SpacesError):
        save_source(h, space_id=space, kind="history", source_id=run)
    assert h.spaces.pages(space) == []
    assert h.store.one("SELECT count(*) AS n FROM page_sources")["n"] == 0


def test_receipt_preserves_failure_and_redacted_arguments(notebook):
    h, space = notebook
    receipt = h.ledger.begin(
        run_id="r",
        tool="example",
        capability="test",
        hand="test",
        args={"password": "secret"},
        effect="observe",
        principal={},
    )
    with pytest.raises(SpacesError):
        save_source(h, space_id=space, kind="receipt", source_id=receipt["id"])
    h.ledger.end(receipt["id"], outcome="failed", content="Provider refused")
    page = save_source(h, space_id=space, kind="receipt", source_id=receipt["id"])
    assert "Outcome: failed" in page["content"] and "Provider refused" in page["content"]
    assert '"password": "secret"' not in page["content"]


def test_revision_restore_is_cas_and_retains_history(notebook):
    h, space = notebook
    page = h.spaces.create(space, "Old", "Original")
    h.spaces.edit(page["id"], 1, title="New", content="Changed")
    with pytest.raises(SpacesError):
        h.spaces.restore_revision(page["id"], 1, 1)
    restored = h.spaces.restore_revision(page["id"], 2, 1)
    assert restored["revision"] == 3 and restored["content"] == "Original"
    assert len(h.spaces.revisions(page["id"])) == 3


def test_default_destination_validation_and_revoke(notebook):
    h, space = notebook
    h.personas.create("researcher")
    with pytest.raises(ValueError):
        h.personas.update("researcher", default_space_id=space)
    with pytest.raises(ValueError):
        h.personas.update("panini", default_space_id="missing")
    h.spaces.grant(space, "researcher", True)
    h.personas.update("researcher", default_space_id=space)
    h.spaces.grant(space, "researcher", False)
    assert h.personas.get("researcher")["default_space_id"] is None


def test_pdf_real_renderer_escapes_active_document():
    document = {
        "title": "<script>alert(1)</script>",
        "revision": 2,
        "content": "# Qualified PDF\n\n| A | B |\n|---|---|\n| One | Two |\n\n<script>fetch('https://example.com')</script>\n\n![remote](https://example.com/image.png)\n"
        + "\n\nParagraph for pagination." * 160,
    }
    output = asyncio.run(pdf(document))
    assert output.startswith(b"%PDF-") and len(output) > 5000
    assert b"/URI" not in output and output.count(b"/Type /Page\n") > 1
    # The source object must stay unchanged.
    assert json.loads(json.dumps(document))["revision"] == 2


def test_strict_provider_schema_optional_null_and_unknown_arguments(notebook, monkeypatch):
    from panini_cli.agent.model import ScriptedModel

    from backend.harness.agent import Agent

    h, space = notebook
    monkeypatch.setenv("PANINI_SPACES", "true")
    agent = Agent(h, ScriptedModel([]), delegate=None, capabilities=())
    agent.registry = h.registry
    offered = agent.tools()
    assert "use_capability" not in {tool["name"] for tool in offered}
    for tool in offered:
        if tool["name"].startswith("pages_") or tool["name"] == "spaces_list":
            schema = tool["parameters"]
            assert set(schema["required"]) == set(schema["properties"])
    h.spaces.create(space, "Durable", "Qualified search")
    result = asyncio.run(
        h.registry.call(agent.tool_context(), "pages_search", {"query": "Qualified", "space_id": None})
    )
    assert result.outcome == "ok" and len(result.data["pages"]) == 1
    refused = asyncio.run(
        h.registry.call(agent.tool_context(), "pages_search", {"query": "Qualified", "unknown": None})
    )
    assert refused.outcome == "blocked"


def test_hashed_fallback_respects_revocation_and_current_revision(notebook):
    h, space = notebook
    h.personas.create("researcher")
    h.spaces.grant(space, "researcher", True)
    page = h.spaces.create(space, "Nebula", "Nebula nebula nebula")
    assert h.spaces.search("nebulla", "researcher")[0]["id"] == page["id"]
    h.spaces.edit(page["id"], 1, title="Unrelated", content="Totally different text")
    assert h.spaces.search("nebulla", "researcher") == []
    h.spaces.grant(space, "researcher", False)
    assert h.spaces.search("Unrelated", "researcher") == []


def test_large_result_is_valid_json_and_oversized_result_is_rejected(notebook):
    h, space = notebook
    text = '"Unicode ✓"' * 8000
    run = completed(h, text)
    assert h.runs.get(run)["result"]["message"] == text
    page = save_source(h, space_id=space, kind="result", source_id=run)
    assert page["content"].startswith(text)
    huge = completed(h, "x" * 1_000_001)
    assert h.runs.get(huge)["result"]["result_truncated"] is True
    with pytest.raises(SpacesError, match="storage limit"):
        save_source(h, space_id=space, kind="result", source_id=huge)
    assert len(h.spaces.pages(space)) == 1


def test_conversation_snapshot_retries_and_new_completed_turn(notebook):
    h, space = notebook
    thread = h.threads.bind("agui", "conversation-test", continue_recent=False)
    h.sessions.save(
        thread,
        [
            {"role": "user", "content": "First question"},
            {"type": "message", "content": [{"text": "First answer"}]},
        ],
    )
    run = h.runs.start(mode="foreground", principal={}, thread_id=thread)
    h.runs.finish(run, "completed", {"outcome": "reply", "message": "First answer"})
    page = save_source(h, space_id=space, kind="conversation", source_id="conversation-test")
    assert (
        save_source(h, space_id=space, kind="conversation", source_id="conversation-test")["id"] == page["id"]
    )
    session = h.sessions.get(thread)
    h.sessions.save(thread, [*session["items"], {"role": "user", "content": "Second question"}])
    newer = h.runs.start(mode="foreground", principal={}, thread_id=thread)
    with pytest.raises(SpacesError):
        save_source(h, space_id=space, kind="conversation", source_id="conversation-test")
    h.runs.finish(newer, "completed", {"outcome": "reply", "message": "Second answer"})
    snapshot = save_source(h, space_id=space, kind="conversation", source_id="conversation-test")
    assert (
        snapshot["id"] != page["id"]
        and "Second question" in snapshot["content"]
        and "Second answer" in snapshot["content"]
    )


def test_real_agent_retains_full_result_for_saving(notebook, monkeypatch):
    from panini_cli.agent.model import ScriptedModel

    from backend.harness.agent import Agent

    h, space = notebook
    monkeypatch.setenv("PANINI_JEV_GATES", "false")
    text = "A long researched result. " * 400
    agent = Agent(h, ScriptedModel([{"text": text}]), delegate=None, capabilities=())
    agent.durable = True
    result = asyncio.run(agent.run("Return the research result."))
    run = h.runs.list(limit=1)[0]
    assert run["result"]["message"] == result.message == text
    # A plain answer carries its reply outcome; it must not be mislabeled as a verified task.
    with pytest.raises(SpacesError):
        save_source(h, space_id=space, kind="result", source_id=run["id"])
