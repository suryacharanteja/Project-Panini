import asyncio

import pytest

from backend.extension_loop import ExtensionLoop
from backend.extension_planner import (
    Candidate,
    CandidateSet,
    Evidence,
    Proposal,
    SearchGoal,
    SearchProof,
    TaskAssessment,
    TaskPlan,
    completion_evidence,
    ground_candidates,
    plan_task,
    propose,
    search_verified,
)


def assessment(status="in_progress", evidence=None, searches=None):
    return TaskAssessment(
        status=status,
        message="Check the requested task",
        evidence=[Evidence(source="text", quote=quote) for quote in evidence or []],
        searches=searches or [],
    )


@pytest.fixture(autouse=True)
def stub_task_planning(monkeypatch):
    monkeypatch.setattr(
        "backend.extension_loop.plan_task", lambda instruction: TaskPlan(objective=instruction, searches=[])
    )


def candidate(action_id, text="", url=""):
    return Candidate(action_id=action_id, text=text, url=url, rationale="Next action")


def test_grounding_sees_late_fields_and_rejects_invented_or_empty_actions():
    page = {
        "actions": [
            *[{"id": f"link{i}", "kind": "click", "label": f"Link {i}"} for i in range(20)],
            {"id": "query", "kind": "fill", "label": "Search", "value": ""},
        ]
    }
    actions = ground_candidates(
        [
            candidate("missing", "bad"),
            candidate("query"),
            candidate("query", "AI jobs"),
            candidate("query", "AI jobs"),
            candidate("navigate", url="javascript:alert(1)"),
            candidate("done"),
        ],
        page,
    )
    assert len(actions) == 1
    assert actions[0]["id"] == "query" and actions[0]["text"] == "AI jobs"
    page["actions"][-1]["value"] = "AI jobs"
    assert ground_candidates([candidate("query", "AI jobs")], page) == []


def test_proposal_uses_shared_provider_and_original_natural_instruction(monkeypatch):
    from types import SimpleNamespace

    from backend import extension_planner

    calls = []

    def parse(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        parsed=CandidateSet(
                            assessment=assessment(), candidates=[candidate("query", "AI jobs")]
                        )
                    )
                )
            ],
            usage=SimpleNamespace(prompt_tokens=40, completion_tokens=12),
        )

    monkeypatch.setattr(
        extension_planner,
        "get_openai_client",
        lambda: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(parse=parse))),
    )
    result = propose(
        "open AI jobs search",
        {"actions": [{"id": "query", "kind": "fill", "value": ""}]},
        [],
        TaskPlan(objective="Search AI jobs", searches=[]),
        {},
    )
    assert result.actions[0]["text"] == "AI jobs"
    assert "open AI jobs search" in calls[0]["messages"][1]["content"]
    assert calls[0]["response_format"] is CandidateSet
    assert result.usage["output_tokens"] == 12
    assert calls[0]["model"] == "gpt-4.1-2025-04-14"

    propose(
        "find the search icon",
        {"screenshot": "data:image/png;base64,dGVzdA==", "actions": []},
        [],
        TaskPlan(objective="Search", searches=[]),
        {},
    )
    content = calls[-1]["messages"][1]["content"]
    assert content[0]["type"] == "text"
    assert content[1] == {
        "type": "image_url",
        "image_url": {"url": "data:image/png;base64,dGVzdA==", "detail": "high"},
    }


class PageBridge:
    connected = True
    attached_tab = 7

    def __init__(self, error=None):
        self.error = error
        self.sent = []

    async def snapshot(self):
        return {
            "url": "https://example.test",
            "title": "Search",
            "text": "Search",
            "marker": "m",
            "actions": [{"id": "query", "kind": "fill", "label": "Search", "value": ""}],
        }

    async def text(self):
        return {}

    async def act(self, *args, **kwargs):
        self.sent.append((args, kwargs))
        return {"error": self.error} if self.error else {"ok": "typed"}


@pytest.mark.parametrize("error,expected,sent", [("Control is covered", "error", 2), (None, "incomplete", 3)])
def test_failures_and_unchanged_pages_stop_without_ten_retries(monkeypatch, error, expected, sent):
    from backend import extension_loop

    bridge = PageBridge(error)
    loop = ExtensionLoop(bridge)
    observed_history = []

    def proposals(instruction, page, history, plan, verified):
        observed_history.append(list(history))
        return Proposal([{**page["actions"][0], "text": "AI jobs"}], assessment(), {"latency_ms": 1})

    monkeypatch.setattr(extension_loop, "propose", proposals)
    monkeypatch.setattr(loop, "_score", lambda *args: ({}, {"done": 0.01, "opt_0": 0.9}))

    async def run():
        return [event async for event in loop._run_events("open AI jobs search")]

    events = asyncio.run(run())
    assert events[-1]["event"] == expected
    assert len(bridge.sent) == sent
    assert bridge.sent[0][1]["text"] == "AI jobs"
    if error:
        assert "FAILED" in observed_history[1][0]
        assert error in events[-1]["error"]


def test_submit_executes_against_exact_observed_field():
    bridge = PageBridge()
    loop = ExtensionLoop(bridge)
    asyncio.run(loop._execute({"id": "query-submit", "kind": "submit"}, "m"))
    assert bridge.sent == [(("query-submit", "SUBMIT"), {"marker": "m"})]


def test_human_verification_stops_before_planning_or_any_action(monkeypatch):
    from backend import extension_loop

    bridge = PageBridge()

    async def challenge():
        return {
            "url": "https://www.google.com/sorry/",
            "blocked_reason": "Complete human verification in the attached tab.",
        }

    bridge.snapshot = challenge
    monkeypatch.setattr(
        extension_loop, "propose", lambda *args: pytest.fail("Must not plan around verification")
    )
    loop = ExtensionLoop(bridge)

    async def run():
        return [e async for e in loop._run_events("open AI jobs search")]

    events = asyncio.run(run())
    assert events[-1]["event"] == "incomplete" and events[-1]["requires_user"]
    assert not bridge.sent
    assert loop.instruction_history[-1]["outcome"] == "requires_user"


def test_different_fields_keep_their_own_proposed_values():
    page = {
        "actions": [{"id": "name", "kind": "fill", "value": ""}, {"id": "city", "kind": "fill", "value": ""}]
    }
    actions = ground_candidates([candidate("name", "Alice"), candidate("city", "Paris")], page)
    assert [(a["id"], a["text"]) for a in actions] == [("name", "Alice"), ("city", "Paris")]


@pytest.mark.parametrize("limit,expected_actions,terminal", [(None, 55, "complete"), (10, 10, "incomplete")])
def test_continuous_preserves_progress_beyond_ten_steps_and_bounds_context(
    monkeypatch, limit, expected_actions, terminal
):
    from backend import extension_loop
    from backend.extension_bridge import ExtensionBridge

    class ProgressBridge(ExtensionBridge):
        progress = 0

        async def text(self):
            return {}

        async def snapshot(self):
            return {
                "url": "https://example.test/wizard",
                "title": "Wizard",
                "text": str(self.progress),
                "marker": "fresh",
                "actions": [{"id": "next", "kind": "click", "label": "Next stage"}],
            }

        async def act(self, *args, **kwargs):
            self.progress += 1
            return {"ok": "advanced"}

    bridge = ProgressBridge()
    loop = ExtensionLoop(bridge)
    seen_context_sizes = []

    def proposals(instruction, page, history, plan, verified):
        seen_context_sizes.append(len(history))
        return Proposal(
            page["actions"],
            assessment("complete" if bridge.progress == 55 else "in_progress", ["55"]),
            {"latency_ms": 1},
        )

    monkeypatch.setattr(extension_loop, "propose", proposals)
    monkeypatch.setattr(
        loop, "_score", lambda *args: ({}, {"done": 0.99 if bridge.progress == 55 else 0.01, "opt_0": 0.9})
    )

    async def run():
        await bridge.attach(object())
        bridge.on_message(
            {"type": "hello", "tab": 7, "protocol": 4, "version": "3.0.0", "attachment_id": "test"}
        )
        return [
            e async for e in loop.run_instruction("Complete 55 stages", max_steps=limit, timeout_seconds=None)
        ]

    events = asyncio.run(run())
    assert bridge.progress == expected_actions
    assert events[-1]["event"] == terminal
    assert len([e for e in events if e["event"] == "step"]) == expected_actions
    assert max(seen_context_sizes) <= 50
    assert not loop.loop_active


def test_related_linkedin_results_cannot_prove_the_requested_query():
    goal = SearchGoal(query="AI manager", domain="linkedin.com")
    proof = SearchProof(goal_index=0, query_evidence="", results_evidence="AI Manager at Example")
    page = {
        "url": "https://www.linkedin.com/jobs/search-results/?keywords=Senior%20Manager%20(Artificial%20Intelligence)",
        "text": "63 results\nAI Manager at Example",
        "actions": [{"kind": "fill", "value": "AI manager"}],
    }
    assert not search_verified(goal, proof, page)
    page["url"] = "https://www.linkedin.com/jobs/search/?keywords=AI%20manager"
    assert search_verified(goal, proof, page)
    page["url"] = "https://evil.example/jobs?keywords=AI%20manager"
    assert not search_verified(goal, proof, page)


def test_typed_query_and_a_related_title_are_not_submitted_results():
    goal = SearchGoal(query="AI manager", domain="")
    page = {
        "url": "https://example.test/jobs",
        "text": "AI Manager at Example",
        "actions": [{"value": "AI manager"}],
    }
    proof = SearchProof(
        goal_index=0, query_evidence="AI Manager at Example", results_evidence="AI Manager at Example"
    )
    assert not search_verified(goal, proof, page)
    page["text"] = "Results for AI manager\nNo matching jobs were found"
    proof = SearchProof(
        goal_index=0, query_evidence="Results for AI manager", results_evidence="No matching jobs were found"
    )
    assert search_verified(goal, proof, page)


def test_completion_requires_all_goals_and_real_observed_evidence():
    plan = TaskPlan(
        objective="Search two sites",
        searches=[
            SearchGoal(query="AI manager", domain="linkedin.com"),
            SearchGoal(query="engineer", domain="example.test"),
        ],
    )
    page = {"url": "https://linkedin.com/jobs?keywords=AI%20manager", "text": "AI Manager at Example"}
    proof = SearchProof(goal_index=0, query_evidence="", results_evidence=page["text"])
    result = Proposal([], assessment("complete", [page["text"]], [proof]), {})
    verified = {}
    assert not completion_evidence(plan, result, page, verified)
    assert verified == {0: page["text"]}
    page = {"url": "https://example.test/?q=engineer", "text": "Engineer at Example"}
    result.assessment = assessment(
        "complete",
        [page["text"]],
        [SearchProof(goal_index=1, query_evidence="", results_evidence=page["text"])],
    )
    assert completion_evidence(plan, result, page, verified)
    result.assessment.evidence = [Evidence(source="text", quote="invented success message")]
    assert not completion_evidence(plan, result, page, verified)


def test_a_high_jev_done_score_cannot_override_unverified_openai_progress(monkeypatch):
    from backend import extension_loop

    bridge = PageBridge()
    loop = ExtensionLoop(bridge)
    monkeypatch.setattr(
        extension_loop,
        "propose",
        lambda *args: Proposal(
            [{**args[1]["actions"][0], "text": "AI manager"}], assessment(), {"latency_ms": 1}
        ),
    )
    offered = []

    def score(state, options):
        offered.extend(o["key"] for o in options)
        return {}, {"done": 0.999, "opt_0": 0.8}

    monkeypatch.setattr(loop, "_score", score)

    async def run():
        return [e async for e in loop._run_events("Search AI manager", max_steps=1)]

    events = asyncio.run(run())
    assert "done" not in offered
    assert bridge.sent[0][1]["text"] == "AI manager"
    assert events[-1]["event"] == "incomplete"


def test_no_candidates_is_incomplete_instead_of_success(monkeypatch):
    from backend import extension_loop

    monkeypatch.setattr(extension_loop, "propose", lambda *args: Proposal([], assessment(), {}))
    loop = ExtensionLoop(PageBridge())
    monkeypatch.setattr(loop, "_score", lambda *args: pytest.fail("No verified options to score"))

    async def run():
        return [e async for e in loop._run_events("Search AI manager")]

    events = asyncio.run(run())
    assert events[-1]["event"] == "incomplete"
    assert "Could not verify" in events[-1]["message"]


def test_formless_search_enter_uses_the_observed_control():
    bridge = PageBridge()
    asyncio.run(ExtensionLoop(bridge)._execute({"id": "search-enter", "kind": "press_enter"}, "fresh"))
    assert bridge.sent == [(("search-enter", "PRESS_ENTER"), {"marker": "fresh"})]


def test_offscreen_reading_keeps_interaction_evidence_separate(monkeypatch):
    from backend import extension_loop

    class OffscreenBridge(PageBridge):
        async def text(self):
            page = await self.snapshot()
            return {"url": page["url"], "text": "AI Manager at Acme"}

    loop = ExtensionLoop(OffscreenBridge())
    observations, scoring_context = [], []

    def proposal(instruction, page, *args):
        observations.append(page)
        return Proposal([], assessment("complete", ["AI Manager at Acme"]), {})

    def score(state, options):
        scoring_context.append(state)
        return {}, {"done": 0.95}

    monkeypatch.setattr(extension_loop, "propose", proposal)
    monkeypatch.setattr(loop, "_score", score)

    async def run():
        return [event async for event in loop._run_events("Read the job results")]

    assert asyncio.run(run())[-1]["event"] == "complete"
    assert "AI Manager at Acme" in observations[0]["text"]
    assert "AI Manager at Acme" not in observations[0]["viewport_text"]
    assert "Current viewport text:" in scoring_context[0]
    assert "Controls currently available" in scoring_context[0]
    assert observations[0]["actions"] == asyncio.run(OffscreenBridge().snapshot())["actions"]


def test_completion_evidence_is_repaired_before_success_is_shown(monkeypatch):
    from backend import extension_loop

    results = iter([assessment("complete", ["invented confirmation"]), assessment("complete", ["Search"])])
    monkeypatch.setattr(extension_loop, "propose", lambda *args: Proposal([], next(results), {}))
    loop = ExtensionLoop(PageBridge())
    monkeypatch.setattr(loop, "_score", lambda *args: ({}, {"done": 0.95}))

    async def run():
        return [e async for e in loop._run_events("Read the current page")]

    events = asyncio.run(run())
    assert events[-1]["event"] == "complete"
    assert events[-1]["evidence"] == [{"source": "text", "quote": "Search"}]
    assert [d["completion_verified"] for d in loop.last_run["decisions"]] == [False, True]
    assert "invented confirmation" not in str(events)


def test_task_plan_is_extracted_without_page_suggestions(monkeypatch):
    from types import SimpleNamespace

    from backend import extension_planner

    calls = []
    expected = TaskPlan(
        objective="Search LinkedIn jobs for AI manager",
        searches=[SearchGoal(query="AI manager", domain="linkedin.com")],
    )

    def parse(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(parsed=expected))])

    monkeypatch.setattr(
        extension_planner,
        "get_openai_client",
        lambda: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(parse=parse))),
    )
    assert plan_task("open linkedin and search for jobs AI manager") == expected
    assert "page" not in calls[0]["messages"][1]["content"]


def test_stale_control_then_slow_navigation_is_observed_before_failing(monkeypatch):
    from backend import extension_loop
    from backend.extension_bridge import ExtensionUnavailable

    class TransitionBridge(PageBridge):
        stage = 0

        async def snapshot(self):
            return {
                "url": f"https://example.test/{self.stage}",
                "text": "Results for AI manager" if self.stage == 2 else str(self.stage),
                "actions": [{"id": "search", "kind": "click", "label": "Search"}],
            }

        async def act(self, *args, **kwargs):
            self.stage = 1
            raise ExtensionUnavailable("Control is gone; take a fresh snapshot.")

        async def navigate(self, url):
            self.stage = 2
            raise ExtensionUnavailable("Navigation timed out. Wait for the page to load and retry.")

    bridge = TransitionBridge()
    loop = ExtensionLoop(bridge)

    def proposals(*args):
        if bridge.stage == 2:
            return Proposal([], assessment("complete", ["Results for AI manager"]), {})
        action = (
            {"id": "search", "kind": "click", "label": "Search"}
            if bridge.stage == 0
            else {"id": "navigate", "kind": "navigate", "url": "https://example.test/2"}
        )
        return Proposal([action], assessment(), {"latency_ms": 1})

    monkeypatch.setattr(extension_loop, "propose", proposals)
    monkeypatch.setattr(loop, "_score", lambda *args: ({}, {"done": 0.95, "opt_0": 0.9}))

    async def run():
        return [e async for e in loop._run_events("Search AI manager")]

    events = asyncio.run(run())
    assert events[-1]["event"] == "complete"
    assert len([e for e in events if e["event"] == "step" and not e["success"]]) == 2
