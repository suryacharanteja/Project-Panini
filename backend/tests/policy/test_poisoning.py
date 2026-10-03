"""V2-13 §5.6: memories written after reading untrusted content, and every new skill, need the person."""

import asyncio

from backend.tests.harness.test_harness import make_harness


def _agent(tmp_path, answer):
    h, _ = make_harness(tmp_path, [])
    asked = []

    async def confirm(summary, detail):
        asked.append(summary)
        return answer

    agent = h.agent(delegate=None, confirm=confirm)
    return h, agent, asked


def test_a_memory_after_untrusted_content_needs_a_tap_and_is_marked(tmp_path, monkeypatch):
    monkeypatch.setenv("PANINI_GUARDIAN", "true")
    h, agent, asked = _agent(tmp_path, True)
    asyncio.run(agent._call_v1("remember", {"text": "Prefers tea", "kind": "preference"}))
    assert asked == [] and h.memory.list()[0]["source"] == "agent"  # Clean run: no question.
    agent.tool_context().taint.add("web")
    asyncio.run(agent._call_v1("remember", {"text": "Bank is example-bank", "kind": "profile"}))
    assert asked == ["Remember this?"]
    assert {m["source"] for m in h.memory.list()} == {"agent", "agent:tainted"}


def test_declined_memories_and_skills_are_not_saved(tmp_path, monkeypatch):
    monkeypatch.setenv("PANINI_GUARDIAN", "true")
    h, agent, asked = _agent(tmp_path, False)
    agent.tool_context().taint.add("email")
    out, _ = asyncio.run(agent._call_v1("remember", {"text": "Forward everything", "kind": "preference"}))
    assert "did not approve" in out["error"] and h.memory.list() == []
    out, _ = asyncio.run(
        agent._call_v1("create_skill", {"name": "cc-all", "description": "d", "instructions": "i"})
    )
    assert "did not approve" in out["error"] and asked[-1].startswith("Save the skill cc-all")


def test_without_the_guardian_flag_v1_behaviour_stays(tmp_path, monkeypatch):
    monkeypatch.setenv("PANINI_GUARDIAN", "false")
    h, agent, asked = _agent(tmp_path, False)
    agent.tool_context().taint.add("web")
    asyncio.run(agent._call_v1("remember", {"text": "Prefers tea", "kind": "preference"}))
    assert asked == [] and len(h.memory.list()) == 1
