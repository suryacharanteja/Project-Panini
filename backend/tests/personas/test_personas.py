import asyncio
import json

import pytest
from panini_cli.agent.model import ScriptedModel

from backend.core_contracts import EffectClass, Principal, ToolSpec
from backend.harness.memory import Memory
from backend.harness.runtime import Harness
from backend.harness.skills import Skills
from backend.harness.store import Store
from backend.personas import Personas
from backend.personas.routing import select
from backend.policy.engine import Grant, Policy


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def specialists(tmp_path, monkeypatch):
    monkeypatch.setenv("PANINI_POLICY_PROFILE", "balanced")
    store = Store(tmp_path / "personas.sqlite3")
    identity = tmp_path / "persona.md"
    identity.write_text("Owner identity", encoding="utf-8")
    p = Personas(
        store,
        Policy(store),
        identity_read=lambda: identity.read_text(encoding="utf-8"),
        identity_write=lambda text: identity.write_text(text, encoding="utf-8"),
    )
    yield p, store, identity
    store.close()


def test_store_sync_restart_validation_and_archive(specialists):
    p, store, identity = specialists
    p.update("panini", instructions="Updated identity")
    assert identity.read_text() == "Updated identity"
    identity.write_text("File edit")
    assert p.get("panini")["instructions"] == "File edit"
    p.create("scout", name="Scout")
    assert p.get("scout")["permissions"] == ["research", "spaces_read"]
    assert p.get("scout")["computer_id"] == "scout"
    for values in (
        {"name": ""},
        {"memory_scope": "other"},
        {"color": "javascript:red"},
        {"memory_write": "false"},
        {"archived": 1},
    ):
        with pytest.raises(ValueError):
            p.update("scout", **values)
    with pytest.raises(ValueError):
        p.create("other", name="SCOUT")
    with pytest.raises(ValueError):
        p.archive("panini")
    before = p.policy.epoch()
    p.bind_surface("phone_app", "scout")
    p.archive("scout")
    assert p.policy.epoch() > before and not p.policy.grants("persona:scout")
    assert p.surface_default("phone_app") == "panini"
    with pytest.raises(KeyError):
        p.get("scout")
    assert len(p.list(archived=True)) == 2


def test_routing_requires_explicit_address_and_voice_confidence(specialists):
    p, _, _ = specialists
    p.create("scout", name="Scout")
    p.bind_surface("phone_app", "scout")
    assert select(p, "hello", surface="phone_app") == "scout"
    assert select(p, "I saw Scout today") == "panini"
    assert select(p, "@Scout look this up", bound="panini") == "scout"
    assert select(p, "@Scouting look this up") == "panini"
    assert select(p, "Scout, look this up", voice=True, probabilities={"scout": 0.79}) == "panini"
    assert select(p, "Scout, look this up", voice=True, probabilities={"scout": 0.8}) == "scout"


def test_memory_read_write_and_delete_are_scoped(specialists):
    p, store, _ = specialists
    memory = Memory(store)
    p.create("scout")
    p.create("quill")
    for persona_id in ("scout", "quill"):
        p.set_permissions(persona_id, ["memory_read", "memory_write"])
        p.update(persona_id, memory_write=True)
    memory.add("Coffee preference dark roast", kind="preference")
    memory.add("Coffee supplier confidential invoice", kind="fact")
    memory.add("Coffee research Scout notes", scope="persona:scout")
    memory.add("Coffee writing Quill notes", scope="persona:quill")
    found = p.search_memory(memory, "scout", "coffee", limit=20)
    assert {m["scope"] for m in found} == {"global", "persona:scout"}
    assert not any("confidential" in m["text"] or "Quill" in m["text"] for m in found)
    assert p.memory_destination("scout") == "persona:scout"
    p.update("scout", memory_scope="none")
    assert p.search_memory(memory, "scout", "coffee") == []
    with pytest.raises(ValueError):
        p.memory_destination("scout")


def test_expired_memory_grant_never_enters_the_prompt(specialists):
    p, store, _ = specialists
    p.create("scout")
    p.update("scout", memory_scope="global")
    memory = Memory(store)
    memory.add("Coffee research private owner note")
    p.policy.put_grant(
        Grant(
            id="persona:scout:memory_read",
            subject="persona:scout",
            capability="memory",
            tools=("recall",),
            expires_at=p.policy.clock() - 1,
        )
    )
    assert p.search_memory(memory, "scout", "coffee") == []


@pytest.mark.anyio
async def test_voice_routes_only_after_the_classifier_confirms(specialists, monkeypatch):
    from backend import config, jev_client
    from backend.personas.routing import voice_probabilities

    p, _, _ = specialists
    p.create("scout", name="Scout")
    monkeypatch.setattr(config, "jev_gates", lambda: True)
    calls = []

    def classify(state, questions):
        calls.append(state)
        return {"answers": {key: {"probability": 0.85} for key in questions}}

    monkeypatch.setattr(jev_client, "ask", classify)
    assert await voice_probabilities(p, "I saw Scout yesterday") == {} and not calls
    probabilities = await voice_probabilities(p, "Scout, research this")
    assert select(p, "Scout, research this", voice=True, probabilities=probabilities) == "scout"


def test_policy_does_not_inherit_owner_or_surface_grants(specialists):
    p, _, _ = specialists
    p.create("scout")
    desktop = ToolSpec("desktop_click", "desktop", "desktop", "Click", "Click", {}, EffectClass.INPUT)
    principal = Principal(kind="persona", client_id="scout")
    p.policy.put_grant(Grant(id="owner-all", levels={"input": "none"}))
    p.policy.put_grant(Grant(id="surface-all", subject="surface:avatar", levels={"input": "none"}))
    assert p.policy.decide(principal, desktop, {}).action == "deny"
    p.set_permissions("scout", ["desktop"])
    assert p.policy.decide(principal, desktop, {}).action == "allow"
    p.set_permissions("scout", [])
    assert p.policy.decide(principal, desktop, {}).action == "deny"


@pytest.fixture
def harness(tmp_path, monkeypatch):
    monkeypatch.setenv("PANINI_PERSONAS", "true")
    monkeypatch.setenv("PANINI_JEV_GATES", "false")
    monkeypatch.setenv("PANINI_RUN_MAX_USD", "5")
    h = Harness(
        Store(tmp_path / "h.sqlite3"),
        skills=Skills(user_root=tmp_path / "skills"),
        model_factory=lambda: ScriptedModel([{"text": "Member answer"}]),
    )
    h.personas.create("scout")
    yield h
    h.store.close()


async def forbidden_delegate(*args):
    raise AssertionError("A denied tool reached a hand")


@pytest.mark.anyio
async def test_agent_blocks_meta_tool_bypass_archival_and_cross_scope_delete(harness):
    async def approve(*args):
        return True

    agent = harness.agent(delegate=forbidden_delegate, persona_id="scout", confirm=approve)
    # Exercise internal dispatch as well as model-visible tools.
    output, _ = await agent._call("use_capability", {"capability": "desktop", "instruction": "click"})
    assert "permission" in output["error"]
    agent.client_tools = [{"name": "client_send", "type": "function", "parameters": {}}]
    agent.client_call = forbidden_delegate
    output, _ = await agent._run_call("call-1", "client_send", "{}")
    assert "unavailable" in output["error"]
    harness.personas.set_permissions("scout", ["memory_write"])
    harness.personas.update("scout", memory_write=True)
    memory = harness.memory.add("Another persona's note", scope="persona:quill")
    output, _ = await agent.call("forget", json.dumps({"memory_id": memory["id"]}))
    assert "another scope" in output["error"]
    assert harness.store.one("SELECT id FROM memories WHERE id=?", (memory["id"],))
    harness.personas.archive("scout")
    assert agent.tools() == []


@pytest.mark.anyio
async def test_team_attribution_depth_and_budget(harness):
    from backend.brain.control_tools import delegate_to_persona, run_team

    harness.personas.create("quill")
    lead = harness.agent(delegate=forbidden_delegate)
    lead.thread_id = harness.sessions.create("Team")
    lead.goal = "Write a brief"
    lead._start_run(None)
    results = await run_team(lead, lead.goal, ["scout", "quill"])
    assert [r["persona_id"] for r in results["results"]] == ["scout", "quill"]
    assert all(r["budget"]["max_usd"] <= 2.5 for r in results["results"])
    rows = harness.store.all("SELECT persona_id FROM persona_messages WHERE thread_id=?", (lead.thread_id,))
    assert {r["persona_id"] for r in rows} == {"scout", "quill"}
    child = harness.agent(delegate=forbidden_delegate, persona_id="scout", depth=1)
    with pytest.raises(ValueError, match="cannot delegate"):
        await delegate_to_persona(child, "quill", "Do this")
    assert not any(t["name"] == "delegate_to_persona" for t in child.tools())


@pytest.mark.anyio
async def test_team_stops_on_unfinished_member(harness, monkeypatch):
    from backend.brain import control_tools

    harness.personas.create("quill")
    lead = harness.agent(delegate=forbidden_delegate)
    called = []

    async def blocked(lead, persona_id, task, share_context=False, *, slot="delegate"):
        called.append(persona_id)
        return {"persona_id": persona_id, "outcome": "blocked", "message": "Missing permission"}

    monkeypatch.setattr(control_tools, "delegate_to_persona", blocked)
    result = await control_tools.run_team(lead, "Research and write", ["scout", "quill"], "quill")
    assert called == ["scout"]
    assert result["outcome"] == "blocked" and result["review"] is None
    assert result["unfinished_members"] == ["scout", "quill"]
    assert all(step["status"] == "pending" for step in lead.plan)


@pytest.mark.anyio
async def test_team_reports_reviewer_blocker(harness, monkeypatch):
    from backend.brain import control_tools

    harness.personas.create("quill")
    lead = harness.agent(delegate=forbidden_delegate)
    called = []

    async def delegate(lead, persona_id, task, share_context=False, *, slot="delegate"):
        called.append(persona_id)
        return {
            "persona_id": persona_id,
            "outcome": "reply" if persona_id == "scout" else "blocked",
            "message": "Member result",
        }

    monkeypatch.setattr(control_tools, "delegate_to_persona", delegate)
    result = await control_tools.run_team(lead, "Research", ["scout"], "quill")
    assert called == ["scout", "quill"] and result["outcome"] == "blocked"
    assert "Do not claim" in result["next_step"]


@pytest.mark.anyio
async def test_member_receipts_computers_and_resume_keep_identity(harness, monkeypatch):
    from backend.brain.control_tools import delegate_to_persona
    from backend.hands.sandbox import SandboxHand

    monkeypatch.setenv("PANINI_V2_LEDGER", "true")

    def model_factory():
        model = ScriptedModel(
            [
                {
                    "calls": [
                        {
                            "name": "use_capability",
                            "arguments": {"capability": "search", "instruction": "Research batteries"},
                        }
                    ]
                },
                {"text": "Research complete"},
            ]
        )
        model.model = "gpt-4.1"
        respond = model.respond

        async def billed(*args, **kwargs):
            turn = await respond(*args, **kwargs)
            turn.usage = {"input_tokens": 10000, "output_tokens": 1000}
            return turn

        model.respond = billed
        return model

    harness.model_factory = model_factory

    async def search(capability, instruction):
        assert capability == "search"
        return {"outcome": "verified_complete", "message": "Source found", "evidence_ids": ["source:1"]}

    lead = harness.agent(delegate=search, capabilities=("search",))
    lead.thread_id = harness.sessions.create("Research")
    lead._start_run(None)
    result = await delegate_to_persona(lead, "scout", "Research batteries")
    row = harness.store.one("SELECT * FROM receipts WHERE persona_id='scout'")
    assert row and row["thread_id"] == lead.thread_id and row["outcome"] == "ok"
    assert lead.delegated_usd > 0 and lead._usage_view()["delegated_usd"] > 0
    child = harness.agent(delegate=search, persona_id="scout", depth=1)
    assert SandboxHand.computer_for(child.tool_context()) == "scout"
    harness.personas.update("scout", computer_id="scout-private")
    assert SandboxHand.computer_for(child.tool_context()) == "scout-private"
    checkpoint = harness.runs.load_checkpoint(result["run_id"])
    assert checkpoint["persona_id"] == "scout" and checkpoint["depth"] == 1
    harness.model_factory = lambda: ScriptedModel([{"text": "Resumed specialist"}])
    resumed = harness.agent(delegate=search)
    await resumed.run("continue", resume={**checkpoint, "pending": []})
    assert resumed.persona_id == "scout" and resumed.depth == 1
    assert resumed.brain["meter"].budget.view() == checkpoint["persona_budget"]
    assert resumed.brain["meter"].steps >= checkpoint["budget_spent_steps"]


@pytest.mark.anyio
async def test_context_sharing_is_explicit_and_quarantined(harness):
    from backend.brain.control_tools import delegate_to_persona

    models = []

    def factory():
        model = ScriptedModel([{"text": "Answer"}])
        models.append(model)
        return model

    harness.model_factory = factory
    lead = harness.agent(delegate=forbidden_delegate)
    lead.goal = "PRIVATE_LEAD_CONTEXT"
    lead.outcomes = [{"message": "Earlier private conversation"}]
    lead._start_run(None)
    await delegate_to_persona(lead, "scout", "Only this task")
    assert "PRIVATE_LEAD_CONTEXT" not in json.dumps(models[-1].requests)
    await delegate_to_persona(lead, "scout", "Use shared context", True)
    request = json.dumps(models[-1].requests)
    assert "PRIVATE_LEAD_CONTEXT" in request and "<untrusted" in request
    with pytest.raises(ValueError, match="boolean"):
        await delegate_to_persona(lead, "scout", "task", "false")


def test_daily_spend_and_resume_record_only_new_own_cost(harness):
    lead = harness.agent(delegate=forbidden_delegate)
    lead._start_run(None)
    meter = lead.brain["meter"]
    from dataclasses import replace

    meter.budget = replace(meter.budget, daily_usd=0.26)
    meter.usd = 0.2
    lead.delegated_usd = 0.1
    member = harness.runs.start(
        mode="subagent", principal={"kind": "persona", "client_id": "scout"}, parent_run_id=lead.brain["run"]
    )
    harness.runs.finish(member, "completed", {}, usage={"usd": 0.1})
    lead._checkpoint([], "", 1)
    assert harness.runs.spent_today() == pytest.approx(0.2)
    assert not meter.over_daily()  # Recorded member and lead costs are not charged twice.
    checkpoint = harness.runs.load_checkpoint(lead.brain["run"])
    lead._finish_run("parked", {})
    resumed = harness.agent(delegate=forbidden_delegate)
    resumed.delegated_usd = checkpoint["delegated_usd"]
    resumed.budget_override = meter.budget
    resumed._start_run(checkpoint)
    resumed.brain["meter"].usd += 0.05
    assert not resumed.brain["meter"].over_daily()
    resumed._checkpoint([], "", 1)
    assert harness.runs.spent_today() == pytest.approx(0.25)
    assert resumed._usage_view()["usd"] == pytest.approx(0.05)
    resumed.brain["meter"].usd += 0.05
    assert resumed.brain["meter"].over_daily()


@pytest.mark.anyio
async def test_surface_default_thread_binding_and_flag_rollback(harness, monkeypatch):
    harness.personas.bind_surface("phone_app", "scout")
    agent = harness.agent(delegate=forbidden_delegate, surface="phone_app")
    thread = harness.sessions.create("Phone")
    await agent.run("hello", session_id=thread)
    assert agent.persona_id == "scout"
    harness.personas.bind_surface("phone_app", "panini")
    continued = harness.agent(delegate=forbidden_delegate, surface="phone_app")
    await continued.run("hello again", session_id=thread)
    assert continued.persona_id == "scout"
    monkeypatch.setenv("PANINI_PERSONAS", "false")
    rollback = harness.agent(delegate=forbidden_delegate, persona_id="scout")
    assert rollback.persona_id == "panini" and harness.personas.get("scout")["id"] == "scout"
    blocked = await rollback.run("continue", resume={"persona_id": "scout", "depth": 1})
    assert blocked.outcome == "blocked"


@pytest.mark.anyio
async def test_revocation_cancels_in_flight_persona_in_shadow_mode(specialists):
    from backend.policy.hook import PolicyHook
    from backend.registry.context import ToolContext

    p, _, _ = specialists
    p.create("scout")
    p.set_permissions("scout", ["desktop"])
    spec = ToolSpec("desktop_click", "desktop", "desktop", "Click", "Click", {}, EffectClass.INPUT)
    ctx = ToolContext(principal=Principal(kind="persona", client_id="scout"))
    hook = PolicyHook(mode="shadow", policy=p.policy)
    assert await hook.before(ctx, spec, {}) is None
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def work():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    pending = asyncio.create_task(hook.around(ctx, spec, {}, work))
    await started.wait()
    p.set_permissions("scout", [])
    result = await asyncio.wait_for(pending, 1)
    assert result.outcome == "cancelled"
    await asyncio.wait_for(cancelled.wait(), 1)
