"""V2-06 one brain: model tiers, budgets, run records, threads, reflexes and the evidence rule."""

import asyncio
from types import SimpleNamespace

import pytest
from panini_cli.agent.model import ModelTurn, ScriptedModel

from backend.brain import Budget, Meter, ModelRouter, Runs, Threads, choose_tier
from backend.brain.budget import turn_cost
from backend.harness.runtime import Harness
from backend.harness.sessions import Sessions
from backend.harness.skills import Skills
from backend.harness.store import Store


@pytest.fixture
def one_brain(monkeypatch):
    monkeypatch.setenv("PANINI_V2_ONE_BRAIN", "true")
    for key in (
        "PANINI_FAST_MODEL",
        "PANINI_AGENT_MODEL",
        "PANINI_DEEP_MODEL",
        "PANINI_RUN_MAX_USD",
        "PANINI_DAILY_MAX_USD",
        "PANINI_RUN_MAX_STEPS",
        "PANINI_AVATAR_THREAD",
    ):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "b.sqlite3")


def make_harness(tmp_path, model):
    return Harness(
        Store(tmp_path / "h.sqlite3"),
        model_factory=lambda: model,
        skills=Skills(user_root=tmp_path / "skills"),
    )


class Switchable:
    """A model double that records model switches and returns a fixed usage per turn."""

    def __init__(self, steps, usage=None, model="gpt-4.1"):
        self.inner, self.model, self.usage, self.switches = ScriptedModel(steps), model, usage, []

    def switch_model(self, name):
        self.switches.append(name)
        self.model = name

    async def respond(self, *args, **kwargs):
        turn = await self.inner.respond(*args, **kwargs)
        return ModelTurn(turn.items, turn.text, turn.calls, self.usage)


# Model tiers ----------------------------------------------------------------
def test_tier_choice_from_jev_probabilities(one_brain, monkeypatch):
    assert choose_tier(None) == "agent"  # Without Jev every run gets the agent model.
    assert choose_tier({"tier_simple": 0.9, "tier_deep": 0.1}) == "fast"
    assert choose_tier({"tier_simple": 0.9, "tier_deep": 0.5}) == "agent"
    assert choose_tier({"tier_simple": 0.1, "tier_deep": 0.9}) == "agent"  # No deep model configured.
    monkeypatch.setenv("PANINI_DEEP_MODEL", "gpt-deep")
    assert choose_tier({"tier_simple": 0.1, "tier_deep": 0.9}) == "deep"


def test_router_escalates_after_two_tool_errors_and_on_a_long_plan(one_brain):
    model = Switchable([])
    router = ModelRouter(model, "fast")
    assert model.model == "gpt-4.1-mini" and router.tier == "fast"
    router.note_tool({"error": "nope"})
    router.note_tool({"ok": True})  # A success resets the count.
    router.note_tool({"error": "nope"})
    assert router.tier == "fast"
    router.note_tool({"error": "again"})
    assert router.tier == "agent" and model.model == "gpt-4.1"  # Back to the factory's model.
    assert router.escalations[0]["reason"] == "two tool calls failed in a row"
    assert not router.escalate("more")  # No deep model configured: agent is the ceiling.
    plan = ModelRouter(Switchable([]), "fast")
    plan.note_plan(3)
    assert plan.tier == "agent"


def test_router_leaves_models_without_switching_alone(one_brain):
    model = ScriptedModel([])
    router = ModelRouter(model, "fast")
    assert router.tier == "fast" and not hasattr(model, "model")


# Budgets ----------------------------------------------------------------------
def test_cost_comes_from_usage_and_missing_usage_fails_closed():
    usage = {"input_tokens": 1_000_000, "output_tokens": 0, "input_tokens_details": {"cached_tokens": 0}}
    assert turn_cost("gpt-4.1", usage) == pytest.approx(2.0)
    assert turn_cost("gpt-4.1", None) > 0.05  # Counted at a fixed upper estimate.
    assert turn_cost("unknown-model", usage) == 0


def test_meter_reports_the_crossed_limit(monkeypatch):
    monkeypatch.setenv("PANINI_RUN_MAX_USD", "0.10")
    budget = Budget.for_mode(background=False)
    assert budget.max_steps == 24 and Budget.for_mode(background=True).max_steps == 16
    meter = Meter(budget, spent_today=lambda: 4.99)
    assert meter.exceeded() is None
    meter.add_turn("gpt-4.1", {"input_tokens": 100_000, "output_tokens": 0})
    assert meter.exceeded() == "cost" and meter.over_daily()
    meter.daily_approved = True
    assert not meter.over_daily()


# Runs ---------------------------------------------------------------------------
def test_runs_record_checkpoints_and_spend(store):
    runs = Runs(store)
    run_id = runs.start(mode="background", principal={"kind": "system"}, task_id="t1", tier="fast")
    big = "x" * 50_000
    items = [{"role": "user", "content": "go"}] + [
        {"type": "function_call_output", "call_id": str(i), "output": big} for i in range(5)
    ]
    runs.checkpoint(run_id, {"items": items, "step": 3, "plan": [{"step": "a", "status": "done"}]})
    loaded = runs.load_checkpoint(run_id)
    assert loaded["step"] == 3 and len(loaded["items"][1]["output"]) < 2100
    runs.update(run_id, usage={"usd": 0.25})
    runs.finish(run_id, "parked", {"error": "waiting"}, usage={"usd": 0.25})
    assert runs.get(run_id)["state"] == "parked" and runs.get(run_id)["has_checkpoint"]
    assert runs.resumable("t1")["id"] == run_id
    assert runs.spent_today() == pytest.approx(0.25)
    assert runs.close_parked("t1", "resumed") == 1 and runs.resumable("t1") is None
    other = runs.start(mode="foreground", principal={}, task_id="t2")
    assert runs.recover() == 1 and runs.get(other)["state"] == "interrupted"
    assert [r["id"] for r in runs.list(task_id="t1")] == [run_id]
    with pytest.raises(ValueError):
        runs.finish(run_id, "nonsense", {})


def test_checkpoints_stay_under_the_size_limit(store, monkeypatch):
    from backend.brain import runs as runs_module

    monkeypatch.setattr(runs_module, "MAX_CHECKPOINT", 4000)
    runs = Runs(store)
    run_id = runs.start(mode="foreground", principal={})
    import secrets

    items = [{"role": "user", "content": secrets.token_hex(400)} for _ in range(40)]
    size = runs.checkpoint(run_id, {"items": items, "step": 1})
    loaded = runs.load_checkpoint(run_id)
    assert size <= 4000 and loaded["dropped"] and len(loaded["items"]) < 40


# Threads --------------------------------------------------------------------------
def test_threads_bind_surfaces_durably_and_the_avatar_continues(store, one_brain, monkeypatch):
    sessions = Sessions(store)
    now = [1000.0]
    threads = Threads(store, sessions, clock=lambda: now[0])
    phone = threads.bind("phone_app", "conv-1", title="hello")
    assert threads.bind("phone_app", "conv-1") == phone
    assert store.one("SELECT origin FROM sessions WHERE id=?", (phone,))["origin"] == "phone_app"
    avatar = threads.bind("avatar", "shell-session-1")
    sessions.save(avatar, [{"role": "user", "content": "hi"}])
    # After a restart the shell has a new session id; the recent avatar thread continues.
    assert threads.bind("avatar", "shell-session-2") == avatar
    monkeypatch.setenv("PANINI_AVATAR_THREAD", "new")
    assert threads.bind("avatar", "shell-session-3") != avatar
    assert {b["external_id"] for b in threads.bindings(avatar)} == {"shell-session-1", "shell-session-2"}
    assert threads.unbind("phone_app", "conv-1") and threads.bind("phone_app", "conv-1") != phone


# One-brain routing ------------------------------------------------------------------
def _route_session(agent_ready=True):
    return SimpleNamespace(
        inventory={"agent": {"state": "ready" if agent_ready else "unavailable", "reason": "No key."}},
        pending=None,
    )


def test_one_brain_routes_every_turn_to_the_agent_with_a_reflex(one_brain, monkeypatch):
    from backend.panini import planning

    turn = SimpleNamespace(text="latest fusion news", capability_hint=None, selected_result_id=None)
    decided = {"steps": [{"capability": "search", "instruction": "latest fusion news"}], "decided_by": "jev"}
    plan = planning.one_brain_route(_route_session(), turn, decided)
    assert plan["steps"][0]["capability"] == "agent" and plan["decided_by"] == "agent+jev_reflex"
    assert plan["reflex"] == {"capability": "search", "instruction": "latest fusion news"}
    assert "reflex" not in planning.one_brain_route(_route_session(), turn, None)
    monkeypatch.setenv("PANINI_REFLEXES", "false")
    assert "reflex" not in planning.one_brain_route(_route_session(), turn, decided)
    # Without the agent: Jev's sure route still runs directly, and nothing else reaches the LLM planner.
    assert planning.one_brain_route(_route_session(False), turn, decided) == decided
    reply = planning.one_brain_route(_route_session(False), turn, None)
    assert reply["steps"] == [] and reply["reply"].startswith("No key.")


def test_route_never_calls_the_llm_planner_with_one_brain(one_brain, monkeypatch):
    from backend.panini import planning

    async def planner(*args, **kwargs):
        raise AssertionError("The LLM planner must not run.")

    monkeypatch.setattr(planning, "parse", planner)
    turn = SimpleNamespace(text="do a thing", capability_hint=None, selected_result_id=None)
    plan = asyncio.run(planning.route(_route_session(False), turn))
    assert plan["steps"] == [] and plan["reply"]


# The loop ---------------------------------------------------------------------------
def test_a_run_records_tier_usage_checkpoint_and_result(tmp_path, one_brain):
    model = Switchable(
        [
            {
                "calls": [
                    {"name": "update_plan", "arguments": {"steps": [{"step": "look", "status": "pending"}]}}
                ]
            },
            {"text": "Hello there."},
        ],
        usage={"input_tokens": 1000, "output_tokens": 100},
    )
    h = make_harness(tmp_path, model)

    async def delegate(capability, instruction):
        raise AssertionError("not used")

    agent = h.agent(delegate=delegate, capabilities=("search",))
    result = asyncio.run(agent.run("say hi"))
    assert result.outcome == "reply"
    (run,) = h.runs.list()
    assert run["state"] == "completed" and run["tier"] == "agent" and run["mode"] == "foreground"
    assert run["usage"]["steps"] == 2 and run["usage"]["usd"] > 0
    assert run["has_checkpoint"] and h.runs.load_checkpoint(run["id"])["step"] == 1
    assert run["result"]["outcome"] == "reply"


def test_the_v1_loop_is_unchanged_without_the_flag(tmp_path, monkeypatch):
    monkeypatch.delenv("PANINI_V2_ONE_BRAIN", raising=False)
    h = make_harness(tmp_path, ScriptedModel([{"text": "Hi."}]))
    agent = h.agent(delegate=None, capabilities=())
    agent.reflex = {"capability": "search", "instruction": "x"}
    assert asyncio.run(agent.run("hi")).outcome == "reply" and h.runs.list() == []


def test_a_reflex_runs_before_the_model_and_is_part_of_the_conversation(tmp_path, one_brain):
    model = ScriptedModel([{"text": "Fusion: ITER reached first plasma (source: iter.org)."}])
    h = make_harness(tmp_path, model)
    asked = []

    async def delegate(capability, instruction):
        asked.append((capability, instruction))
        return {"outcome": "verified_complete", "message": "ITER news", "results": [], "evidence_ids": ["e1"]}

    agent = h.agent(delegate=delegate, capabilities=("search",))
    agent.reflex = {"capability": "search", "instruction": "latest fusion news"}
    result = asyncio.run(agent.run("latest fusion news"))
    assert asked == [("search", "latest fusion news")]
    assert result.outcome == "verified_complete" and result.evidence == ["e1"]
    first = model.requests[0]["items"]
    assert [i.get("type") for i in first[-2:]] == ["function_call", "function_call_output"]
    assert first[-2]["call_id"] == first[-1]["call_id"]


def test_a_reflex_for_a_capability_the_run_lacks_is_ignored(tmp_path, one_brain):
    h = make_harness(tmp_path, ScriptedModel([{"text": "Hi."}]))
    agent = h.agent(delegate=None, capabilities=("search",))
    agent.reflex = {"capability": "desktop", "instruction": "click"}
    assert asyncio.run(agent.run("hi")).outcome == "reply"


def test_the_cost_budget_stops_a_run_plainly(tmp_path, one_brain, monkeypatch):
    monkeypatch.setenv("PANINI_RUN_MAX_USD", "0.01")
    step = {"calls": [{"name": "update_plan", "arguments": {"steps": [{"step": "a", "status": "pending"}]}}]}
    model = Switchable([step, step, step], usage={"input_tokens": 100_000, "output_tokens": 0})
    h = make_harness(tmp_path, model)
    result = asyncio.run(h.agent(delegate=None, capabilities=()).run("loop"))
    assert result.outcome == "partial" and "cost limit" in result.message and result.steps == 2
    assert h.runs.list()[0]["state"] == "partial"


def test_the_daily_cap_asks_in_the_foreground_and_stops_in_the_background(tmp_path, one_brain, monkeypatch):
    monkeypatch.setenv("PANINI_DAILY_MAX_USD", "0")
    asked = []

    async def decline(summary, detail):
        asked.append(summary)
        return False

    h = make_harness(tmp_path, ScriptedModel([{"text": "never"}]))
    result = asyncio.run(h.agent(delegate=None, capabilities=(), confirm=decline).run("hi"))
    assert result.outcome == "partial" and "spending limit" in result.message
    assert asked and asked[0].startswith("Today's PANINI spend reached USD")

    async def approve(summary, detail):
        return True

    h2 = make_harness(tmp_path / "2", ScriptedModel([{"text": "Hi."}]))
    assert asyncio.run(h2.agent(delegate=None, capabilities=(), confirm=approve).run("hi")).outcome == "reply"
    h3 = make_harness(tmp_path / "3", ScriptedModel([{"text": "never"}]))
    bg = h3.agent(delegate=None, capabilities=(), background=True, confirm=approve)
    assert "spending limit" in asyncio.run(bg.run("hi")).message


def test_two_failed_tool_calls_move_a_fast_run_to_the_agent_model(tmp_path, one_brain, monkeypatch):
    bad = {"calls": [{"name": "read_file", "arguments": {"path": str(tmp_path / "missing.txt")}}]}
    model = Switchable([bad, bad, {"text": "It is missing."}], usage={"input_tokens": 10, "output_tokens": 1})
    h = make_harness(tmp_path, model)
    agent = h.agent(delegate=None, capabilities=())

    async def select(goal, items):
        return {"groups": {"computer"}, "skills": None, "servers": set(), "memories": [], "tier": "fast"}

    agent._select = select
    asyncio.run(agent.run("read the file"))
    run = h.runs.list()[0]
    assert model.switches[0] == "gpt-4.1-mini" and model.switches[-1] == "gpt-4.1"
    assert run["result"]["escalations"][0]["to"] == "agent" and run["tier"] == "agent"


def test_a_run_resumes_from_its_checkpoint(tmp_path, one_brain):
    model = ScriptedModel([{"text": "Finished after resuming."}])
    h = make_harness(tmp_path, model)
    checkpoint = {
        "run_id": "old",
        "items": [
            {"role": "user", "content": "long task"},
            {"role": "assistant", "content": "step one done"},
        ],
        "plan": [{"step": "one", "status": "completed"}],
        "outcomes": [],
        "evidence": [],
        "step": 4,
        "tier": "agent",
    }
    result = asyncio.run(
        h.agent(delegate=None, capabilities=(), background=True).run("long task", resume=checkpoint)
    )
    assert result.steps == 5 and result.plan == checkpoint["plan"]
    assert [i.get("content") for i in model.requests[0]["items"]] == ["long task", "step one done"]
    run = h.runs.list()[0]
    assert run["parent_run_id"] == "old" and run["mode"] == "background"


def test_success_needs_every_effect_checked_with_the_ledger_on(tmp_path, one_brain, monkeypatch):
    from backend import ledger

    finish = {"name": "finish", "arguments": {"outcome": "verified_complete", "message": "Done."}}
    model = ScriptedModel(
        [
            {
                "calls": [
                    {"name": "use_capability", "arguments": {"capability": "search", "instruction": "q"}}
                ]
            },
            {"calls": [finish]},
        ]
    )
    h = make_harness(tmp_path, model)

    async def delegate(capability, instruction):
        return {"outcome": "verified_complete", "message": "ok", "results": [], "evidence_ids": ["e1"]}

    monkeypatch.setattr(
        ledger, "active", lambda: SimpleNamespace(verified_after_last_effect=lambda run_id: False)
    )
    agent = h.agent(delegate=delegate, capabilities=("search",))
    agent.run_id = "task-1"
    assert asyncio.run(agent.run("do it")).outcome == "partial"


def test_registry_receipts_carry_v1_verification(tmp_path, monkeypatch):
    """A command's own check becomes its receipt's evidence, so I2 accepts a verified command."""
    monkeypatch.setenv("PANINI_V2_REGISTRY", "true")
    monkeypatch.setenv("PANINI_V2_LEDGER", "true")
    monkeypatch.setenv("PANINI_V2_ONE_BRAIN", "true")
    target = tmp_path / "note.txt"
    command = f"Set-Content -Path '{target}' -Value 'hello'; Get-Content '{target}'"
    finish = {"name": "finish", "arguments": {"outcome": "verified_complete", "message": "Saved."}}
    model = ScriptedModel(
        [{"calls": [{"name": "run_command", "arguments": {"command": command}}]}, {"calls": [finish]}]
    )
    h = make_harness(tmp_path, model)

    async def approve(summary, detail):
        return True

    agent = h.agent(delegate=None, capabilities=(), confirm=approve)
    agent.run_id = "task-2"
    result = asyncio.run(agent.run("save a note"))
    receipts = h.ledger.query(run_id="task-2")
    command_receipt = next(r for r in receipts if r["tool"] == "run_command")
    assert command_receipt["outcome"] == "ok"
    assert h.ledger.receipt(command_receipt["id"])["evidence"][0]["kind"] == "agent"
    assert result.outcome == "verified_complete"


def test_runs_route_lists_runs(tmp_path, one_brain):
    from fastapi.testclient import TestClient

    from backend.app import create_app

    h = make_harness(tmp_path, ScriptedModel([]))
    run_id = h.runs.start(mode="foreground", principal={"kind": "owner"}, thread_id="th")
    h.runs.checkpoint(run_id, {"items": [], "plan": [{"step": "a", "status": "pending"}], "step": 1})
    app = create_app(harness_factory=lambda: h, console_token="t" * 32)
    headers = {"x-panini-console": "t" * 32}
    with TestClient(app, base_url="http://127.0.0.1") as client:
        listed = client.get("/api/harness/runs", headers=headers).json()
        assert listed["runs"][0]["id"] == run_id and "fast" in listed["tiers"]
        one = client.get(f"/api/harness/runs/{run_id}", headers=headers).json()
        assert one["plan"][0]["step"] == "a" and one["step"] == 1
        assert client.get("/api/harness/runs/nope", headers=headers).status_code == 404
