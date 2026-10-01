"""Jev decides; the LLM proposes. These tests pin the decision points and the token budgets."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from backend import usage_log
from backend.extension_loop import ExtensionLoop, screenshot_detail
from backend.extension_planner import Proposal, TaskAssessment, TaskPlan, proposal_payload


@pytest.fixture(autouse=True)
def fresh_usage():
    usage_log.reset()
    yield
    usage_log.reset()


def jev(probabilities, tokens=100):
    return {
        "answers": {key: {"probability": value} for key, value in probabilities.items()},
        "usage": {"inputTokens": tokens},
    }


# -- Telemetry -----------------------------------------------------------------
def test_usage_is_recorded_per_surface_with_cached_input_priced_lower():
    fresh = usage_log.record("browser.propose", "gpt-4.1-2025-04-14", 1_000_000, 0)
    cached = usage_log.record("browser.propose", "gpt-4.1-2025-04-14", 1_000_000, 0, cached_tokens=1_000_000)
    assert fresh == pytest.approx(2.0) and cached == pytest.approx(0.5)
    assert usage_log.record_jev("browser.decide", jev({}, tokens=1_000_000)) == pytest.approx(0.042)
    summary = usage_log.summary()
    assert summary["surfaces"]["browser.propose"]["calls"] == 2
    assert summary["surfaces"]["browser.decide"]["input_tokens"] == 1_000_000
    assert summary["total"]["calls"] == 3
    # Unknown models never overstate cost, but their tokens still count.
    assert usage_log.record("x", "some-new-model", 500, 5) == 0
    assert usage_log.record_openai("y", "gpt-4.1-mini", None) == 0


def test_openai_usage_objects_are_read_including_cached_tokens():
    usage = SimpleNamespace(
        prompt_tokens=2000, completion_tokens=100, prompt_tokens_details=SimpleNamespace(cached_tokens=1024)
    )
    usage_log.record_openai("browser.plan", "gpt-4.1-mini", usage)
    entry = usage_log.summary()["recent"][-1]
    assert (entry["input_tokens"], entry["cached_tokens"], entry["output_tokens"]) == (2000, 1024, 100)


# -- Proposer payload ------------------------------------------------------------
def test_proposer_payload_drops_the_duplicate_active_reading_and_old_outcomes():
    page = {
        "tab_id": 1,
        "url": "https://a.test",
        "text": "A" * 4000,
        "actions": [],
        "visited_pages": [
            {"tab_id": 1, "url": "https://a.test", "text": "A" * 4000},
            {"tab_id": 2, "url": "https://b.test", "text": "B" * 4000},
        ],
        "screenshot": "data:image/png;base64,xx",
    }
    history = [f"step {i}" for i in range(10)]
    payload = proposal_payload("task", page, history, TaskPlan(objective="task", searches=[]), {})
    readings = payload["page"]["visited_pages"]
    assert [r["tab_id"] for r in readings] == [2]
    assert len(readings[0]["text"]) == 1500
    assert payload["previous_outcomes"] == [
        "(6 earlier actions omitted)",
        "step 6",
        "step 7",
        "step 8",
        "step 9",
    ]
    assert "screenshot" not in payload["page"]


def test_screenshot_detail_is_low_until_the_page_resists(monkeypatch):
    from backend import config

    monkeypatch.setattr(config, "SCREENSHOT_DETAIL", "auto")
    assert screenshot_detail(0) == "low"
    assert screenshot_detail(1) == "high"
    monkeypatch.setattr(config, "SCREENSHOT_DETAIL", "high")
    assert screenshot_detail(0) == "high"


# -- Jev decides "blocked" ------------------------------------------------------
class Bridge:
    connected = True
    attached_tab = 7

    def __init__(self):
        self.sent = []

    async def snapshot(self):
        return {
            "url": "https://example.test",
            "title": "Search",
            "text": f"Search {len(self.sent)}",
            "marker": "m",
            "actions": [{"id": "query", "kind": "fill", "label": "Search", "value": ""}],
        }

    async def text(self):
        return {}

    async def act(self, *args, **kwargs):
        self.sent.append((args, kwargs))
        return {"ok": "typed"}


def blocked_run(monkeypatch, blocked_probability):
    from backend import extension_loop

    monkeypatch.setattr(extension_loop, "plan_task", lambda i: TaskPlan(objective=i, searches=[]))
    bridge = Bridge()
    loop = ExtensionLoop(bridge)
    calls = []

    def proposals(instruction, page, history, plan, verified):
        status = "blocked" if not calls else "in_progress"
        calls.append(status)
        return Proposal(
            [{**page["actions"][0], "text": "AI jobs"}],
            TaskAssessment(status=status, message="Log in first", evidence=[], searches=[]),
            {"latency_ms": 1},
        )

    def score(state, options):
        if [o["key"] for o in options] == ["blocked"]:
            return jev({"blocked": blocked_probability}), {"blocked": blocked_probability}
        return jev({"opt_0": 0.9}), {"opt_0": 0.9}

    monkeypatch.setattr(extension_loop, "propose", proposals)
    monkeypatch.setattr(loop, "_score", score)

    async def run():
        return [event async for event in loop._run_events("search AI jobs", max_steps=2)]

    return asyncio.run(run()), bridge


def test_a_blocked_claim_stops_only_when_jev_confirms_it(monkeypatch):
    events, bridge = blocked_run(monkeypatch, 0.9)
    assert events[-1]["event"] == "incomplete" and events[-1]["requires_user"]
    assert not bridge.sent


def test_jev_overrules_an_unfounded_blocked_claim(monkeypatch):
    events, bridge = blocked_run(monkeypatch, 0.1)
    assert not any(e.get("requires_user") for e in events)
    assert bridge.sent  # The task continued with a real action.
    assert usage_log.summary()["surfaces"]["browser.blocked"]["calls"] == 1


# -- Jev routes plain requests without an LLM call --------------------------------
def session(**overrides):
    ready = {"state": "ready"}
    values = {
        "pending": None,
        "inventory": {"search": ready, "browser": ready, "code": ready, "agent": ready, "desktop": ready},
        "history": [],
        "selected": {},
        "results": {},
        "last_task": None,
    }
    return SimpleNamespace(**(values | overrides))


def turn(text):
    return SimpleNamespace(text=text, capability_hint=None, selected_result_id=None)


@pytest.fixture
def jev_gates(monkeypatch):
    monkeypatch.setenv("PANINI_JEV_GATES", "true")
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "test-gateway-key")


def fake_route(monkeypatch, probabilities):
    from backend import jev_client
    from backend.panini import planning

    asked = []

    def ask(state, questions, *_):
        asked.append((json.loads(state), questions))
        return jev({key: probabilities.get(key, 0.02) for key in questions})

    async def llm(*args, **kwargs):
        raise AssertionError("The LLM planner must not run")

    monkeypatch.setattr(jev_client, "ask", ask)
    monkeypatch.setattr(planning, "parse", llm)
    return planning, asked


def test_jev_routes_a_plain_request_without_the_llm_planner(monkeypatch, jev_gates):
    planning, asked = fake_route(monkeypatch, {"route_search": 0.95})
    plan = asyncio.run(planning.route(session(), turn("latest news about fusion energy")))
    assert plan["decided_by"] == "jev"
    assert plan["steps"][0]["capability"] == "search"
    assert plan["steps"][0]["instruction"] == "latest news about fusion energy"
    assert len(asked) == 1 and "route_desktop" not in asked[0][1]  # Desktop needs targets: LLM only.
    assert usage_log.summary()["surfaces"]["route"]["calls"] == 1


@pytest.mark.parametrize(
    "probabilities",
    [
        {"route_search": 0.95, "small_talk": 0.6},  # Small talk needs wording.
        {"route_code": 0.9, "missing_details": 0.9},  # A clarifying question needs wording.
        {"route_code": 0.95, "names_target": 0.7},  # Named workspace or application.
        {"route_browser": 0.9, "multi_step": 0.5},
        {"route_search": 0.9, "follow_up": 0.8},
        {"route_search": 0.7},  # Not confident enough.
        {"route_search": 0.95, "route_code": 0.9},  # Too close to call.
    ],
)
def test_anything_ambiguous_goes_to_the_llm_planner(monkeypatch, jev_gates, probabilities):
    planning, _ = fake_route(monkeypatch, probabilities)
    assert asyncio.run(planning.jev_route(session(), turn("do the thing"))) is None


def test_jev_routing_is_skipped_for_pending_clarifications_and_when_off(monkeypatch, jev_gates):
    planning, asked = fake_route(monkeypatch, {"route_search": 0.99})
    assert asyncio.run(planning.jev_route(session(pending={"instruction": "x"}), turn("fusion"))) is None
    monkeypatch.setenv("PANINI_JEV_GATES", "false")
    assert asyncio.run(planning.jev_route(session(), turn("fusion"))) is None
    assert not asked


def test_jev_failure_falls_back_to_the_llm_planner(monkeypatch, jev_gates):
    from backend import jev_client
    from backend.panini import planning

    def broken(*args):
        raise RuntimeError("sidecar down")

    monkeypatch.setattr(jev_client, "ask", broken)
    assert asyncio.run(planning.jev_route(session(), turn("fusion news"))) is None


# -- Jev confirms the agent's success claim -----------------------------------------
def agent(monkeypatch, achieved):
    from backend import jev_client
    from backend.harness.agent import Agent

    monkeypatch.setattr(jev_client, "ask", lambda state, questions: jev({"achieved": achieved}))
    a = Agent.__new__(Agent)
    a.evidence, a.outcomes, a.goal, a.plan = (
        ["ev1"],
        [{"capability": "search", "outcome": "verified_complete"}],
        "g",
        [],
    )
    a.emit = lambda *args, **kwargs: None
    return a


@pytest.mark.parametrize("achieved,expected", [(0.9, "verified_complete"), (0.2, "partial")])
def test_agent_success_needs_jev(monkeypatch, jev_gates, achieved, expected):
    a = agent(monkeypatch, achieved)
    _, result = asyncio.run(a._call("finish", {"outcome": "verified_complete", "message": "Done"}))
    assert result.outcome == expected


def test_viewport_text_is_sent_once_while_verification_keeps_the_whole_page():
    from backend.extension_planner import page_text_beyond_viewport

    viewport = "Top result: AI Manager, Dubai"
    page = {"viewport_text": viewport, "text": viewport + "\n" + viewport + " More results below"}
    assert page_text_beyond_viewport(page) == "More results below"
    assert page_text_beyond_viewport({"text": "only  text"}) == "only text"


# -- Browser: compact actions and Jev-selected off-screen text ---------------------
def test_actions_reach_the_proposer_without_executor_only_fields():
    from backend.extension_planner import action_line, clean_href, compact_action

    action = {
        "id": "e12_click",
        "kind": "click",
        "node": 12,
        "role": "link",
        "label": "Senior AI Manager",
        "context": "A job-card__link artdeco-entity-lockup__title ember-view " * 4,
        "bounds": {"x": 10, "y": 20, "width": 300, "height": 40},
        "href": "https://www.linkedin.com/jobs/view/1",
        "value": "",
    }
    assert compact_action(action) == {
        "id": "e12_click",
        "kind": "click",
        "label": "Senior AI Manager",
        "role": "link",
        "href": "https://www.linkedin.com/jobs/view/1",
    }
    assert "role" not in compact_action({"id": "b", "kind": "click", "label": "button", "role": "button"})
    assert (
        action_line(action)
        == 'e12_click | click | Senior AI Manager | role="link" | href="https://www.linkedin.com/jobs/view/1"'
    )
    assert (
        clean_href(
            "https://x.test/jobs/view/1/?refId=a%3D&trackingId=b&keywords=AI%20manager&utm_source=x#top"
        )
        == "https://x.test/jobs/view/1/?keywords=AI%20manager"
    )


def links_page(count=60):
    actions = [{"id": "q", "kind": "fill", "label": "Search jobs", "value": ""}]
    actions += [{"id": f"nav{i}", "kind": "click", "label": f"Menu {i}"} for i in range(count - 5)]
    actions += [{"id": f"job{i}", "kind": "click", "label": f"Senior AI Manager {i}"} for i in range(5)]
    actions += [{"id": "scroll_down", "kind": "scroll", "label": "Scroll down"}]
    return {"text": "short", "actions": actions}


def test_jev_picks_the_links_the_proposer_sees_and_keeps_every_field(monkeypatch, jev_gates):
    from backend import extension_planner

    def ask(state, questions, *_):
        links = json.loads(state)["links"]
        return jev({k: (0.9 if "AI Manager" in links[k] else 0.05) for k in questions})

    monkeypatch.setattr(extension_planner.jev_client, "ask", ask)
    plan = TaskPlan(objective="AI jobs", searches=[])
    selection = extension_planner.select_page("find AI manager jobs", plan, links_page())
    assert {f"job{i}" for i in range(5)} | {"q", "scroll_down"} <= selection.action_ids
    assert len(selection.action_ids) == extension_planner.MIN_LINKS + 2  # Top links, the field and scroll.
    payload = proposal_payload(
        "t", links_page(), [], plan, {}, (selection.text, selection.how), selection.action_ids
    )
    assert len(payload["page"]["actions"]) == len(selection.action_ids) and "actions_note" in payload["page"]
    # Few links: nothing to decide, no Jev call.
    monkeypatch.setattr(extension_planner.jev_client, "ask", lambda *a: pytest.fail("Jev not needed"))
    assert extension_planner.select_page("t", plan, links_page(20)).action_ids is None


def long_page():
    viewport = "Jobs you may like"
    lines = [f"Footer link {i}" for i in range(200)] + [
        "Senior AI Manager, Dubai. 1,203 results for AI manager"
    ]
    return {"viewport_text": viewport, "text": viewport + "\n" + "\n".join(lines)}


def test_short_offscreen_text_is_sent_whole_without_asking_jev(monkeypatch, jev_gates):
    from backend import extension_planner

    monkeypatch.setattr(extension_planner.jev_client, "ask", lambda *a: pytest.fail("Jev not needed"))
    plan = TaskPlan(objective="AI jobs", searches=[])
    assert extension_planner.relevant_text("t", plan, {"text": "short"}) == ("short", "whole")


def test_jev_picks_the_passages_the_proposer_reads(monkeypatch, jev_gates):
    from backend import extension_planner

    def ask(state, questions, *_):
        passages = json.loads(state)["passages"]
        return jev({k: (0.9 if "results for AI manager" in passages[k] else 0.05) for k in questions}, 2000)

    monkeypatch.setattr(extension_planner.jev_client, "ask", ask)
    plan = TaskPlan(objective="AI jobs", searches=[])
    text, how = extension_planner.relevant_text("search AI manager jobs", plan, long_page())
    assert how == "jev_selected" and "1,203 results for AI manager" in text
    assert len(text) <= extension_planner.SELECTED_TEXT_CHARS and "Footer link 3 " not in text
    payload = proposal_payload("t", long_page(), [], plan, {}, (text, how))
    assert "text_note" in payload["page"]
    assert usage_log.summary()["surfaces"]["browser.page_select"]["calls"] == 1


def test_offscreen_text_falls_back_to_whole_when_jev_fails(monkeypatch, jev_gates):
    from backend import extension_planner

    def broken(*args):
        raise RuntimeError("down")

    monkeypatch.setattr(extension_planner.jev_client, "ask", broken)
    plan = TaskPlan(objective="AI jobs", searches=[])
    text, how = extension_planner.relevant_text("t", plan, long_page())
    assert how == "whole" and "results for AI manager" in text


def test_text_chunks_keep_lines_whole_and_bounded():
    from backend.extension_planner import text_chunks

    chunks = text_chunks("a b\n" + "x" * 1500 + "\n" + "c  d", size=600)
    assert chunks[0] == "a b" and all(len(c) <= 600 for c in chunks) and chunks[-1].endswith("c d")


# -- Desktop: compact UIA elements -------------------------------------------------
def test_desktop_elements_are_compacted_for_the_models():
    from backend.capabilities.desktop import compact_element

    element = {
        "element_id": "42.1.99.3",
        "name": "Equals",
        "automation_id": "equalButton",
        "type": "ControlType.Button",
        "enabled": True,
        "value": None,
        "bounds": {"x": 1, "y": 2, "width": 3, "height": 4},
        "patterns": ["InvokePatternIdentifiers.Pattern", "LegacyIAccessiblePatternIdentifiers.Pattern"],
    }
    assert compact_element(element, "equalButton") == {
        "element_id": "equalButton",
        "name": "Equals",
        "type": "Button",
        "automation_id": "equalButton",
        "patterns": ["Invoke", "LegacyIAccessible"],
        "bounds": {"x": 1, "y": 2, "width": 3, "height": 4},
    }
    assert (
        compact_element({**element, "enabled": False, "automation_id": ""}, "control_1")["enabled"] is False
    )


# -- Harness: Jev chooses the task's context and can finish it ---------------------
def harness_with(tmp_path, steps, skills=()):
    from panini_cli.agent.model import ScriptedModel

    from backend.harness.runtime import Harness
    from backend.harness.skills import Skills
    from backend.harness.store import Store

    model = ScriptedModel(steps)
    h = Harness(
        Store(tmp_path / "h.sqlite3"),
        model_factory=lambda: model,
        skills=Skills(user_root=tmp_path / "skills"),
    )
    for name, description in skills:
        h.skills.create(name, description, "Do it well.")
    return h, model


def test_jev_leaves_unneeded_skills_memories_and_tools_out_of_the_prompt(tmp_path, monkeypatch, jev_gates):
    from backend import jev_client

    h, model = harness_with(
        tmp_path,
        [{"text": "Here is the brief."}],
        skills=[("ev-brief", "Write EV market briefs"), ("tax-filing", "Prepare quarterly tax filings")],
    )
    h.memory.add("Prefers concise briefs", kind="preference", source="user")

    def ask(state, questions, *_):
        chosen = {"skill_0": 0.9, "memory_0": 0.8}
        return jev({k: chosen.get(k, 0.05) for k in questions})

    monkeypatch.setattr(jev_client, "ask", ask)
    tools_seen = []

    async def delegate(*_):
        return {"outcome": "failed", "message": ""}

    agent = h.agent(delegate=delegate, capabilities=("search",))
    original = agent.tools
    agent.tools = lambda: tools_seen.append([t["name"] for t in original()]) or original()
    asyncio.run(agent.run("Brief me on EV sales"))
    prompt = model.requests[0]["instructions"]
    assert "ev-brief" in prompt and "tax-filing" not in prompt
    assert "Prefers concise briefs" in prompt
    names = tools_seen[-1]
    assert {"use_capability", "ask_user", "finish", "load_skill"} <= set(names)
    assert not {"recall", "remember", "schedule_task", "watch", "create_skill"} & set(names)
    assert "use recall" not in prompt.lower() and "schedule_task" not in prompt


def test_without_jev_the_agent_sees_everything_as_before(tmp_path):
    h, model = harness_with(tmp_path, [{"text": "ok"}], skills=[("tax-filing", "Prepare tax filings")])

    async def delegate(*_):
        return {}

    asyncio.run(h.agent(delegate=delegate, capabilities=("search",)).run("hello"))
    assert "tax-filing" in model.requests[0]["instructions"]


@pytest.mark.parametrize("done,model_turns", [(0.95, 1), (0.3, 2)])
def test_jev_finishes_after_a_verified_step_without_another_model_turn(
    tmp_path, monkeypatch, jev_gates, done, model_turns
):
    from backend import jev_client

    h, model = harness_with(
        tmp_path,
        [
            {
                "calls": [
                    {
                        "name": "use_capability",
                        "arguments": {"capability": "search", "instruction": "EV sales"},
                    }
                ]
            },
            {
                "calls": [
                    {"name": "finish", "arguments": {"outcome": "verified_complete", "message": "Brief"}}
                ]
            },
        ],
    )
    monkeypatch.setattr(
        jev_client,
        "ask",
        lambda state, questions: jev(
            {k: {"done": done, "remaining": 1 - done}.get(k, 0.9) for k in questions}
        ),
    )

    async def delegate(capability, instruction):
        return {"outcome": "verified_complete", "message": "EV sales rose 20%", "evidence_ids": ["e1"]}

    result = asyncio.run(h.agent(delegate=delegate, capabilities=("search",)).run("EV sales this year"))
    assert result.outcome == "verified_complete"
    assert model.count == model_turns
    if model_turns == 1:
        assert result.message == "EV sales rose 20%"
