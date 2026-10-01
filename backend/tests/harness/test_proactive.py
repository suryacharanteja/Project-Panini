import asyncio
import json
import sys
import time
from pathlib import Path

from fastapi.testclient import TestClient
from panini_cli.agent.model import ScriptedModel

from backend.capabilities.contracts import Outcome
from backend.harness import proactive
from backend.harness.connectors import Connectors
from backend.harness.runtime import Harness
from backend.harness.skills import Skills
from backend.harness.store import Store
from backend.harness.tasks import Worker

FAKE = str(Path(__file__).with_name("fake_mcp.py"))


def make(tmp_path, steps, connectors=None):
    model = ScriptedModel(steps)
    connectors = connectors or Connectors(tmp_path / "connectors.json")
    h = Harness(
        Store(tmp_path / "h.sqlite3"),
        model_factory=lambda: model,
        skills=Skills(user_root=tmp_path / "skills"),
        connectors=connectors,
    )
    return h, model


def fake_connectors(tmp_path):
    path = tmp_path / "connectors.json"
    path.write_text(json.dumps({"servers": {"notes": {"command": sys.executable, "args": [FAKE]}}}))
    return Connectors(path)


def test_approved_project_app_workflow_finishes_with_receipt_and_advances(tmp_path):
    connectors = fake_connectors(tmp_path)
    h, _ = make(
        tmp_path,
        [
            {
                "calls": [
                    {
                        "name": "propose_project_plan",
                        "arguments": {
                            "steps": [
                                {
                                    "title": "Find decisions",
                                    "goal": "Use notes.lookup to find launch decisions",
                                    "success_criteria": "Read the notes and retain a connector receipt",
                                },
                                {
                                    "title": "Find follow ups",
                                    "goal": "Use notes.lookup to find unresolved launch follow ups",
                                    "success_criteria": "Read related notes and cite receipts",
                                },
                            ]
                        },
                    }
                ]
            },
            {
                "calls": [
                    {
                        "name": "use_connector",
                        "arguments": {"server": "notes", "tool": "lookup", "arguments": '{"q":"launch"}'},
                    }
                ]
            },
            {
                "calls": [
                    {
                        "name": "finish",
                        "arguments": {"outcome": "verified_complete", "message": "Launch notes retrieved."},
                    }
                ]
            },
        ],
        connectors,
    )

    async def go():
        try:
            await connectors.status()
            connectors.set_background_grant("notes", "lookup", True)
            project = h.projects.create("Launch", "Review launch decisions")
            draft = await h.project_planner.draft(project["id"])
            h.project_planner.approve(project["id"], draft["revision"])
            claim = h.tasks.claim()
            outcome, message, evidence = await h.run_background(claim, h.tasks)
            assert outcome == "verified_complete" and evidence[0].startswith("connector:")
            h.tasks.finish(claim["id"], claim["lease_id"], outcome, message, evidence)
            h.project_planner.reconcile()
            plan = h.project_planner.get(project["id"])
            assert plan["steps"][1]["task_id"] and len(h.tasks.list()) == 2
            assert h.projects.verified_history(project["id"])[0]["evidence"] == evidence
            assert h.store.one("SELECT COUNT(*) AS n FROM connector_receipts")["n"] == 1
        finally:
            await h.close()

    asyncio.run(go())


class Search:
    async def availability(self):
        return {"state": "ready", "reason": ""}

    async def run(self, ctx, instruction, target):
        return Outcome("verified_complete", "price is 19,500", [], [ctx.record_evidence("r", "s", {})])

    async def release(self, task_id):
        pass


def finish(message):
    return [
        {
            "calls": [
                {"name": "use_capability", "arguments": {"capability": "search", "instruction": "price"}}
            ]
        },
        {"calls": [{"name": "finish", "arguments": {"outcome": "verified_complete", "message": message}}]},
    ]


def test_watch_notifies_only_when_condition_holds_and_only_once_per_finding(tmp_path):
    h, model = make(
        tmp_path,
        finish("QUIET: still 21,000")
        + finish("NOTIFY: dropped to 19,500")
        + finish("NOTIFY: dropped to 19,500"),
    )
    h.adapters = {"search": Search()}
    task = h.tasks.submit("price of the phone", every_minutes=60, condition="below 20,000")
    assert task["kind"] == "watch"

    async def cycle():
        h.store.execute("UPDATE tasks SET next_run=0 WHERE id=?", (task["id"],))
        return await Worker(h.tasks, h.run_background).run_once()

    for _ in range(3):
        assert asyncio.run(cycle())
    notices = h.notices.list()
    assert [n["body"] for n in notices] == ["dropped to 19,500"]
    assert h.tasks.get(task["id"])["status"] == "queued"  # Watches keep running.
    assert "below 20,000" in model.requests[0]["items"][0]["content"]


def test_one_off_background_task_leaves_a_result_notice(tmp_path):
    h, _ = make(tmp_path, finish("The answer is 42."))
    h.adapters = {"search": Search()}
    h.tasks.submit("find the answer")
    assert asyncio.run(Worker(h.tasks, h.run_background).run_once())
    [notice] = h.notices.list()
    assert notice["kind"] == "result" and notice["body"] == "The answer is 42."
    assert h.status()["notices"] == 1


def test_suggestions_for_repeated_goals_and_expiring_reviews(tmp_path):
    h, _ = make(tmp_path, [])
    for _ in range(3):
        h.sessions.save(h.sessions.create(), [], title="Summarise AI news for me")
    task = h.tasks.submit("x")
    h.tasks.open_review(task["id"], "question", "Which account?")
    h.store.execute("UPDATE reviews SET expires=?", (time.time() + 600,))
    created = h.suggest(force=True)
    kinds = sorted(n["action"]["type"] for n in created)
    assert kinds == ["open_review", "schedule"]
    assert h.suggest(force=True) == []  # Deduplicated.
    schedule = next(n for n in created if n["action"]["type"] == "schedule")
    accepted = h.accept(schedule["id"])
    assert accepted["task"]["every_minutes"] == 1440
    assert accepted["notice"]["status"] == "accepted"


def test_skill_suggestion_needs_three_verified_steps():
    class Notices:
        def add(self, *args, **kwargs):
            return {"args": args, **kwargs}

    steps = [{"capability": "search", "outcome": "verified_complete", "message": "ok"}] * 2
    assert proactive.suggest_skill(Notices(), "plan a trip", steps) is None
    assert proactive.suggest_skill(Notices(), "plan a trip", steps * 2)["action"]["type"] == "skill"


def test_connectors_read_only_runs_and_writes_need_confirmation(tmp_path):
    connectors = fake_connectors(tmp_path)
    h, model = make(
        tmp_path,
        [
            {
                "calls": [
                    {
                        "name": "use_connector",
                        "arguments": {"server": "notes", "tool": "lookup", "arguments": '{"q": "a"}'},
                    },
                    {
                        "name": "use_connector",
                        "arguments": {"server": "notes", "tool": "create_note", "arguments": '{"text": "b"}'},
                    },
                ]
            },
            {"text": "done"},
        ],
        connectors,
    )
    asked = []

    async def confirm(summary, detail):
        asked.append((summary, detail))
        return False

    async def delegate(*_):
        return {}

    async def go():
        try:
            return await h.agent(delegate=delegate, capabilities=(), confirm=confirm).run("notes")
        finally:
            await connectors.close()

    asyncio.run(go())
    outputs = [
        json.loads(i["output"])
        for i in model.requests[-1]["items"]
        if i.get("type") == "function_call_output"
    ]
    assert outputs[0]["result"] == 'lookup -> {"q": "a"}'
    assert "declined" in outputs[1]["error"]
    assert asked == [("notes: create_note", '{"text": "b"}')]
    assert "notes.lookup (read-only)" in model.requests[0]["instructions"]
    [receipt] = h.store.all("SELECT server, tool, read_only, ok FROM connector_receipts")
    assert receipt == {"server": "notes", "tool": "lookup", "read_only": 1, "ok": 1}


def test_background_refuses_data_changing_connector_tools(tmp_path):
    connectors = fake_connectors(tmp_path)
    h, model = make(
        tmp_path,
        [
            {
                "calls": [
                    {
                        "name": "use_connector",
                        "arguments": {"server": "notes", "tool": "create_note", "arguments": "{}"},
                    }
                ]
            },
            {"text": "ok"},
        ],
        connectors,
    )

    async def delegate(*_):
        return {}

    async def go():
        try:
            await h.agent(delegate=delegate, capabilities=(), confirm=None).run("x")
        finally:
            await connectors.close()

    asyncio.run(go())
    output = json.loads(model.requests[-1]["items"][-1]["output"])
    assert "foreground" in output["error"]


def test_background_connector_needs_explicit_read_only_grant(tmp_path):
    connectors = fake_connectors(tmp_path)
    h, _ = make(tmp_path, [], connectors)

    async def delegate(*_):
        return {}

    async def go():
        try:
            await connectors.status()
            agent = h.agent(delegate=delegate, capabilities=(), background=True)
            args = json.dumps({"server": "notes", "tool": "lookup", "arguments": "{}"})
            denied, _ = await agent.call("use_connector", args)
            assert "not granted" in denied["error"]
            connectors.servers["notes"].spec["background_read_only"] = ["lookup"]
            allowed, _ = await agent.call("use_connector", args)
            assert allowed["ok"] and agent.evidence[0].startswith("connector:")
            assert h.store.one("SELECT COUNT(*) AS n FROM connector_receipts")["n"] == 1
        finally:
            await connectors.close()

    asyncio.run(go())


def test_subagents_run_search_in_parallel_with_fresh_context(tmp_path):
    h, model = make(
        tmp_path,
        [
            {
                "calls": [
                    {
                        "name": "run_subagents",
                        "arguments": {
                            "tasks": [
                                {"task": "Research A", "capability": "search"},
                                {"task": "Research B", "capability": "search"},
                                {"task": "Click things", "capability": "browser"},
                            ]
                        },
                    }
                ]
            },
            {"text": "A done"},
            {"text": "B done"},
            {"calls": [{"name": "finish", "arguments": {"outcome": "partial", "message": "Merged."}}]},
        ],
    )

    async def delegate(*_):
        return {}

    result = asyncio.run(h.agent(delegate=delegate, capabilities=("search",)).run("compare A and B"))
    assert result.message == "Merged."
    children = [r for r in model.requests if r["items"][0]["content"] in ("Research A", "Research B")]
    assert len(children) == 2 and all(len(r["items"]) == 1 for r in children)  # Fresh context.
    child = h.agent(delegate=delegate, capabilities=("search",), depth=1)
    assert {"run_subagents", "watch", "schedule_task"}.isdisjoint(t["name"] for t in child.tools())
    output = json.loads(model.requests[-1]["items"][-1]["output"])
    assert [r["task"] for r in output["results"]] == ["Research A", "Research B"]  # browser was not allowed.


def test_console_routes_for_notices_watches_and_connectors(tmp_path):
    from backend.app import create_app

    h, _ = make(tmp_path, [])
    h.notices.add("result", "Done: x", "All good")
    app = create_app(harness_factory=lambda: h, console_token="t" * 32)
    headers = {"x-panini-console": "t" * 32}
    with TestClient(app, base_url="http://127.0.0.1") as client:
        [notice] = client.get("/api/harness/notices", headers=headers).json()["notices"]
        marked = client.post(f"/api/harness/notices/{notice['id']}", json={"status": "read"}, headers=headers)
        assert marked.json()["status"] == "read"
        watch = client.post(
            "/api/harness/tasks",
            json={"goal": "Check the price", "every_minutes": 60, "condition": "below 100"},
            headers=headers,
        )
        assert watch.json()["kind"] == "watch"
        bad = client.post("/api/harness/tasks", json={"goal": "x", "condition": "y"}, headers=headers)
        assert bad.status_code == 422
        assert client.get("/api/harness/connectors", headers=headers).json()["connectors"] == []
        assert client.post("/api/harness/suggestions/refresh", headers=headers).json() == {"created": []}
        assert client.get("/api/harness/status", headers=headers).json()["watches"] == 1


def test_a_capability_refusal_goes_back_to_the_agent_not_into_a_retry(tmp_path):
    from backend.panini.contracts import Fault

    class Picky(Search):
        async def run(self, ctx, instruction, target):
            if len(instruction) > 300:
                raise Fault("SEARCH_TOO_LONG", "Use a Search instruction of 300 characters or fewer.", 422)
            return await super().run(ctx, instruction, target)

    h, model = make(
        tmp_path,
        [
            {
                "calls": [
                    {
                        "name": "use_capability",
                        "arguments": {"capability": "search", "instruction": "x" * 400},
                    }
                ]
            },
        ]
        + finish("Short query worked."),
    )
    h.adapters = {"search": Picky()}
    task = h.tasks.submit("find it")
    assert asyncio.run(Worker(h.tasks, h.run_background).run_once())
    done = h.tasks.get(task["id"])
    assert done["status"] == "completed" and done["attempts"] == 1
    refusal = json.loads(model.requests[1]["items"][-1]["output"])
    assert "300 characters" in refusal["message"]
