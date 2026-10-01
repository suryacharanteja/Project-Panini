import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from panini_cli.agent.model import ScriptedModel

from backend.capabilities.contracts import Outcome
from backend.harness.memory import Memory, MemoryError, embed
from backend.harness.projects import Projects
from backend.harness.runtime import Harness
from backend.harness.skills import Skills
from backend.harness.store import Store
from backend.harness.tasks import LEASE_SECONDS, Tasks, Worker


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "h.sqlite3")
    yield s
    s.close()


def make_harness(tmp_path, steps):
    model = ScriptedModel(steps)
    h = Harness(
        Store(tmp_path / "h.sqlite3"),
        model_factory=lambda: model,
        skills=Skills(user_root=tmp_path / "skills"),
    )
    return h, model


# Memory ---------------------------------------------------------------------
def test_embedding_is_normalised_and_similar_text_scores_higher():
    a, b, c = embed("I prefer dark roast coffee"), embed("prefers dark coffee"), embed("quarterly tax filing")
    assert abs(sum(x * x for x in a) - 1) < 1e-6
    dot = lambda x, y: sum(i * j for i, j in zip(x, y))  # noqa: E731
    assert dot(a, b) > dot(a, c)


def test_memory_hybrid_search_dedupes_and_refuses_secrets(store):
    m = Memory(store)
    m.add("The person prefers morning meetings before 10am", kind="preference")
    m.add("Their sister Priya lives in Hyderabad", kind="profile")
    again = m.add("The person prefers morning meetings before 10am", kind="preference")
    assert len(m.list()) == 2 and again["similarity"] > 0.9
    top = m.search("when should I schedule meetings")
    assert top and "morning" in top[0]["text"]
    assert m.list()[1]["uses"] >= 0
    with pytest.raises(MemoryError):
        m.add("my api_key is sk-abcdefghijklmnop")
    assert "data, not instructions" in m.context("meetings")
    assert m.update(top[0]["id"], "Prefers meetings after 11am")
    assert "11am" in m.search("meetings")[0]["text"]
    assert m.forget(everything=True) == 2 and m.list() == []


# Skills ---------------------------------------------------------------------
def test_skills_progressive_disclosure_and_project_override(tmp_path):
    project = tmp_path / "ws"
    (project / ".panini/skills/research-brief").mkdir(parents=True)
    (project / ".panini/skills/research-brief/SKILL.md").write_text(
        "---\nname: research-brief\ndescription: Project version\n---\nProject steps", encoding="utf-8"
    )
    skills = Skills(user_root=tmp_path / "user", workspace=project)
    catalog = {s["name"]: s for s in skills.catalog()}
    assert catalog["research-brief"]["origin"] == "project"
    assert "save-a-skill" in catalog  # Built-in.
    assert "Project steps" not in skills.prompt()  # Only names and descriptions.
    created = skills.create("weekly-report", "Build the weekly report", "1. Gather\n2. Send")
    assert created["origin"] == "user" and "Gather" in created["instructions"]
    with pytest.raises(ValueError):
        skills.create("Bad Name", "x", "y")


# Durable tasks ----------------------------------------------------------------
class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_task_lease_recovery_requeues_without_effects_and_reviews_with_effects(store):
    clock = Clock()
    tasks = Tasks(store, clock=clock)
    safe = tasks.submit("summarise the news")
    risky = tasks.submit("post the summary")
    a, b = tasks.claim(), tasks.claim()
    assert {a["id"], b["id"]} == {safe["id"], risky["id"]} and tasks.claim() is None
    held = a if a["id"] == risky["id"] else b
    tasks.checkpoint(risky["id"], held["lease_id"], {"step": 1}, dispatched=True)
    clock.now += LEASE_SECONDS + 1  # The process died; leases expire.
    assert tasks.recover() == 2
    assert tasks.get(safe["id"])["status"] == "queued"
    assert tasks.get(risky["id"])["status"] == "needs_review"
    review = tasks.reviews()[0]
    assert review["kind"] == "outcome_unknown"
    assert tasks.resolve(review["id"], "rerun")["status"] == "queued"
    with pytest.raises(KeyError):
        tasks.resolve(review["id"], "rerun")  # One decision per review.


def test_task_retry_backoff_recurrence_and_stale_lease(store):
    clock = Clock()
    tasks = Tasks(store, clock=clock)
    t = tasks.submit("check prices", every_minutes=60)
    claim = tasks.claim()
    tasks.retry(t["id"], claim["lease_id"], RuntimeError("network"))
    assert tasks.get(t["id"])["next_run"] == clock.now + 30
    clock.now += 31
    claim = tasks.claim()
    tasks.finish(t["id"], claim["lease_id"], "verified_complete", "Prices unchanged.")
    after = tasks.get(t["id"])
    assert after["status"] == "queued" and after["next_run"] == clock.now + 3600
    assert after["result"]["message"] == "Prices unchanged."
    assert not tasks.heartbeat(t["id"], claim["lease_id"])  # Old lease no longer owns it.
    with pytest.raises(ValueError):
        tasks.submit("too often", every_minutes=1)


def test_projects_persist_and_paused_project_tasks_wait(tmp_path):
    path = tmp_path / "projects.sqlite3"
    first = Store(path)
    projects = Projects(first)
    project = projects.create("Launch", "Prepare a release", "Use concise updates.")
    tasks = Tasks(first)
    assigned = tasks.submit("Check the docs", project_id=project["id"])
    projects.update(project["id"], status="paused")
    assert tasks.claim() is None
    first.close()

    reopened = Store(path)
    projects = Projects(reopened)
    tasks = Tasks(reopened)
    assert projects.get(project["id"])["context"] == "Use concise updates."
    assert projects.tasks(project["id"]) == [assigned["id"]]
    projects.update(project["id"], status="active")
    claim = tasks.claim()
    assert claim["id"] == assigned["id"]
    tasks.finish(assigned["id"], claim["lease_id"], "verified_complete", "Docs reviewed", ["ev1"])
    assert projects.verified_history(project["id"])[0]["result"] == "Docs reviewed"
    unsupported = tasks.submit("Unverified answer", project_id=project["id"])
    claim = tasks.claim()
    assert claim["id"] == unsupported["id"]
    tasks.finish(unsupported["id"], claim["lease_id"], "verified_complete", "No evidence")
    assert len(projects.verified_history(project["id"])) == 1
    projects.update(project["id"], status="completed")
    with pytest.raises(ValueError, match="active project"):
        tasks.submit("Another task", project_id=project["id"])
    reopened.close()


def test_agent_project_tools_and_background_boundary(tmp_path):
    h, _ = make_harness(
        tmp_path,
        [
            {
                "calls": [
                    {
                        "name": "propose_project_plan",
                        "arguments": {"steps": [{"title": "Research", "goal": "Find official sources"}]},
                    }
                ]
            }
        ],
    )

    async def delegate(*_):
        return {}

    approvals = []

    async def confirm(summary, detail):
        approvals.append((summary, detail))
        return True

    agent = h.agent(delegate=delegate, capabilities=(), confirm=confirm)

    async def exercise():
        created, _ = await agent.call(
            "create_project", json.dumps({"title": "Launch", "goal": "Ship", "context": "Check tests"})
        )
        project_id = created["project_id"]
        listed, _ = await agent.call("list_projects", "{}")
        assert listed["projects"][0]["id"] == project_id
        task, _ = await agent.call(
            "add_project_task",
            json.dumps({"project_id": project_id, "goal": "Check release", "delay_minutes": 0}),
        )
        assert h.tasks.get(task["task_id"])["project_id"] == project_id
        draft, _ = await agent.call("draft_project_plan", json.dumps({"project_id": project_id}))
        assert draft["steps"][0]["title"] == "Research"
        approved, _ = await agent.call(
            "approve_project_plan",
            json.dumps({"project_id": project_id, "revision": draft["revision"]}),
        )
        assert approved["status"] == "running" and approvals[0][0] == "Start project plan"
        assert "Success criteria:" in approvals[0][1]
        stopped, _ = await agent.call(
            "cancel_project_plan", json.dumps({"project_id": project_id, "revision": draft["revision"]})
        )
        assert stopped["status"] == "cancelled"
        background = h.agent(delegate=delegate, capabilities=("search",), background=True)
        denied, _ = await background.call(
            "create_project", json.dumps({"title": "Wrong", "goal": "No", "context": ""})
        )
        assert "unavailable" in denied["error"]

    asyncio.run(exercise())


def test_large_project_plan_is_not_approved_with_hidden_steps(tmp_path):
    h, _ = make_harness(
        tmp_path,
        [
            {
                "calls": [
                    {
                        "name": "propose_project_plan",
                        "arguments": {
                            "steps": [
                                {"title": f"Step {i}", "goal": "g" * 500, "success_criteria": "c" * 1000}
                                for i in range(6)
                            ]
                        },
                    }
                ]
            }
        ],
    )

    async def confirm(*_args):
        raise AssertionError("A truncated plan must never reach confirmation")

    async def delegate(*_args):
        return {}

    async def go():
        project = h.projects.create("Large plan", "Research")
        draft = await h.project_planner.draft(project["id"])
        agent = h.agent(delegate=delegate, capabilities=(), confirm=confirm)
        result, _ = await agent.call(
            "approve_project_plan", json.dumps({"project_id": project["id"], "revision": draft["revision"]})
        )
        assert "too long" in result["error"]
        assert h.tasks.list() == []
        await h.close()

    asyncio.run(go())


def test_reviews_expire(store):
    clock = Clock()
    tasks = Tasks(store, clock=clock)
    t = tasks.submit("x")
    rid = tasks.open_review(t["id"], "question", "Which account?")
    clock.now += 2 * 24 * 3600
    assert tasks.reviews() == []
    with pytest.raises(KeyError):
        tasks.resolve(rid, "dismiss")


# Agent loop -----------------------------------------------------------------
def test_agent_remembers_uses_capability_and_requires_evidence(tmp_path):
    h, model = make_harness(
        tmp_path,
        [
            {
                "calls": [
                    {
                        "name": "update_plan",
                        "arguments": {"steps": [{"step": "Research", "status": "in_progress"}]},
                    },
                    {
                        "name": "remember",
                        "arguments": {"text": "Prefers concise briefs", "kind": "preference"},
                    },
                ]
            },
            {
                "calls": [
                    {
                        "name": "use_capability",
                        "arguments": {"capability": "search", "instruction": "EV sales 2026"},
                    }
                ]
            },
            {
                "calls": [
                    {
                        "name": "finish",
                        "arguments": {"outcome": "verified_complete", "message": "Brief ready."},
                    }
                ]
            },
        ],
    )
    calls = []

    async def delegate(capability, instruction):
        calls.append((capability, instruction))
        return {"outcome": "verified_complete", "message": "3 sources", "results": [], "evidence_ids": ["e1"]}

    result = asyncio.run(h.agent(delegate=delegate, capabilities=("search",)).run("Brief me on EV sales"))
    assert result.outcome == "verified_complete" and "e1" in result.evidence
    assert calls == [("search", "EV sales 2026")]
    assert result.plan[0]["step"] == "Research"
    assert h.memory.list()[0]["text"] == "Prefers concise briefs"
    assert "Available skills" in model.requests[0]["instructions"]


def test_agent_downgrades_unverified_success_and_blocks_unlisted_capability(tmp_path):
    h, _ = make_harness(
        tmp_path,
        [
            {
                "calls": [
                    {"name": "use_capability", "arguments": {"capability": "browser", "instruction": "buy"}}
                ]
            },
            {
                "calls": [
                    {"name": "finish", "arguments": {"outcome": "verified_complete", "message": "Bought!"}}
                ]
            },
        ],
    )

    async def delegate(capability, instruction):
        raise AssertionError("must not be called")

    result = asyncio.run(h.agent(delegate=delegate, capabilities=("search",)).run("buy it"))
    assert result.outcome == "partial"


def test_agent_injects_memory_and_persists_session(tmp_path):
    h, model = make_harness(tmp_path, [{"text": "Your sister lives in Hyderabad."}, {"text": "Noted."}])
    h.memory.add("Sister Priya lives in Hyderabad", kind="profile")
    sid = h.sessions.create()

    async def delegate(*_):
        return {}

    agent = h.agent(delegate=delegate, capabilities=())
    assert asyncio.run(agent.run("Where does my sister live?", session_id=sid)).outcome == "reply"
    assert "Hyderabad" in model.requests[0]["instructions"]
    asyncio.run(h.agent(delegate=delegate, capabilities=()).run("thanks", session_id=sid))
    assert len(h.sessions.get(sid)["items"]) == 4  # user, reply, user, reply.


def test_agent_compacts_long_sessions(tmp_path):
    h, model = make_harness(tmp_path, [{"text": "SUMMARY: talked about coffee."}, {"text": "ok"}])
    h.sessions.budget = 2000
    sid = h.sessions.create()
    h.sessions.save(sid, [{"role": "user", "content": "coffee " * 50}] * 30)

    async def delegate(*_):
        return {}

    asyncio.run(h.agent(delegate=delegate, capabilities=()).run("continue", session_id=sid))
    saved = h.sessions.get(sid)
    assert saved["summary"].startswith("SUMMARY") and len(saved["items"]) <= 18
    assert "SUMMARY" in model.requests[1]["instructions"]


def test_worker_runs_background_task_with_search_only(tmp_path):
    h, model = make_harness(
        tmp_path,
        [
            {
                "calls": [
                    {"name": "use_capability", "arguments": {"capability": "search", "instruction": "q"}}
                ]
            },
            {
                "calls": [
                    {"name": "finish", "arguments": {"outcome": "verified_complete", "message": "Found it."}}
                ]
            },
        ],
    )

    class Search:
        async def availability(self):
            return {"state": "ready", "reason": ""}

        async def run(self, ctx, instruction, target):
            ctx.check()
            return Outcome("verified_complete", "ok", [], [ctx.record_evidence("retrieved", "src", {})])

        async def release(self, task_id):
            pass

    h.adapters = {"search": Search(), "browser": object()}
    project = h.projects.create("Research", "Track the answer", "Prefer primary sources.")
    previous = h.tasks.submit("Earlier work", project_id=project["id"])
    claim = h.tasks.claim()
    h.tasks.finish(previous["id"], claim["lease_id"], "verified_complete", "Found source A", ["ev-old"])
    task = h.tasks.submit("find the answer", project_id=project["id"])

    async def go():
        return await Worker(h.tasks, h.run_background).run_once()

    assert asyncio.run(go())
    done = h.tasks.get(task["id"])
    assert done["status"] == "completed" and done["result"]["message"] == "Found it."
    assert "Prefer primary sources." in str(model.requests[0]["items"])
    assert "Found source A" in str(model.requests[0]["items"])
    assert (
        model.requests
        and "browser"
        not in str(model.requests[0]["instructions"]).split("Capabilities available now:")[1][:40]
    )


# API ------------------------------------------------------------------------
def test_console_and_shell_api(tmp_path, monkeypatch):
    from backend.app import create_app

    h, _ = make_harness(tmp_path, [])
    app = create_app(harness_factory=lambda: h, console_token="t" * 32)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.get("/api/harness/status").status_code == 401  # Console sign-in required.
        headers = {"x-panini-console": "t" * 32}
        added = client.post(
            "/api/harness/memories", json={"text": "Likes jazz", "kind": "preference"}, headers=headers
        )
        assert added.status_code == 200
        assert (
            client.post(
                "/api/harness/memories", json={"text": "password is hunter2"}, headers=headers
            ).status_code
            == 422
        )
        assert client.get("/api/harness/memories?q=music jazz", headers=headers).json()["memories"]
        project = client.post(
            "/api/harness/projects",
            json={"title": "Release", "goal": "Prepare launch", "context": "Review evidence first."},
            headers=headers,
        )
        assert project.status_code == 200
        project_id = project.json()["id"]
        task = client.post(
            "/api/harness/tasks",
            json={"goal": "Remind me", "delay_minutes": 60, "project_id": project_id},
            headers=headers,
        )
        assert task.json()["status"] == "queued"
        assert (
            client.get(f"/api/harness/projects/{project_id}", headers=headers).json()["tasks"][0]["id"]
            == task.json()["id"]
        )
        assert (
            client.patch(
                f"/api/harness/projects/{project_id}", json={"status": "paused"}, headers=headers
            ).json()["status"]
            == "paused"
        )
        status = client.get("/api/harness/status", headers=headers).json()
        assert status["queued"] == 1 and status["memories"] == 1
        assert client.get("/api/harness/skills", headers=headers).json()["skills"]
        assert (
            client.post(f"/api/harness/tasks/{task.json()['id']}/cancel", headers=headers).json()["status"]
            == "cancelled"
        )
        assert client.delete("/api/harness/memories", headers=headers).json()["deleted"] == 1
        assert client.get("/api/panini/harness/status").status_code in (401, 403, 404)


def test_agent_adapter_delegates_under_the_task_context(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from backend.capabilities.agent import AgentAdapter

    monkeypatch.setenv("PANINI_CLI_SCRIPTED_MODEL", "unused")
    h, _ = make_harness(
        tmp_path,
        [
            {
                "calls": [
                    {"name": "use_capability", "arguments": {"capability": "search", "instruction": "q"}}
                ]
            },
            {
                "calls": [
                    {"name": "finish", "arguments": {"outcome": "verified_complete", "message": "Done."}}
                ]
            },
        ],
    )
    seen, evidence = [], {}

    class Context:
        session = SimpleNamespace(session_id="s1")
        task = SimpleNamespace(task_id="t1", capability="agent")

        def check(self):
            pass

        def emit(self, phase, message, **detail):
            pass

        def record_evidence(self, kind, summary, details):
            evidence[kind] = self.task.capability
            return kind

    class Search:
        async def availability(self):
            return {"state": "ready", "reason": ""}

        async def run(self, ctx, instruction, target):
            seen.append(ctx.task.capability)
            return Outcome("verified_complete", "ok", [], [ctx.record_evidence("retrieved", "s", {})])

        async def release(self, task_id):
            pass

    adapter = AgentAdapter(h, {"search": Search()})
    assert asyncio.run(adapter.availability())["state"] == "ready"
    outcome = asyncio.run(adapter.run(Context(), "research x", None))
    assert outcome.outcome == "verified_complete" and outcome.evidence_ids == ["agent_run"]
    # Sub-steps record evidence as their own capability; the summary is the agent's.
    assert seen == ["search"] and evidence == {"retrieved": "search", "agent_run": "agent"}


def desktop_world(tmp_path, instruction, final):
    """An agent that asks Desktop to do something, then answers in plain text; many windows are open."""
    from types import SimpleNamespace

    from backend.capabilities.agent import AgentAdapter
    from backend.panini.events import EventHub

    h, _ = make_harness(
        tmp_path,
        [
            {
                "calls": [
                    {
                        "name": "use_capability",
                        "arguments": {"capability": "desktop", "instruction": instruction},
                    }
                ]
            },
            {"text": final},
        ],
    )
    windows = [
        {"choice_id": "paint", "label": "Untitled - Paint", "application": "mspaint"},
        {"choice_id": "chrome", "label": "News - Google Chrome", "application": "chrome"},
        {"choice_id": "term", "label": "PANINI-CLI", "application": "windowsterminal"},
    ]
    ran = []

    class Desktop:
        async def availability(self):
            return {"state": "ready", "reason": ""}

        async def targets(self):
            return [dict(w) for w in windows]

        async def select(self, choice_id):
            return next(dict(w) for w in windows if w["choice_id"] == choice_id)

        async def run(self, ctx, text, target):
            ran.append(target["label"])
            return Outcome("verified_complete", "drawn", [], [ctx.record_evidence("drawing", "d", {})])

        async def release(self, task_id):
            pass

    class Context:
        session = SimpleNamespace(session_id="s1", selected={}, hub=EventHub("instance", "s1"))
        task = SimpleNamespace(task_id="t1", capability="agent")

        def check(self):
            pass

        def emit(self, phase, message, **detail):
            pass

        def record_evidence(self, kind, summary, details):
            return kind

    return AgentAdapter(h, {"desktop": Desktop()}), Context(), ran


def test_the_agent_names_the_window_so_many_open_apps_are_not_ambiguous(tmp_path, monkeypatch):
    monkeypatch.setenv("PANINI_CLI_SCRIPTED_MODEL", "unused")
    adapter, context, ran = desktop_world(tmp_path, "In Paint, draw a simple elephant", "Drew it.")
    outcome = asyncio.run(adapter.run(context, "draw an elephant", None))
    assert ran == ["Untitled - Paint"] and outcome.outcome == "verified_complete"


def test_a_reply_after_only_failed_attempts_is_never_reported_as_success(tmp_path, monkeypatch):
    monkeypatch.setenv("PANINI_CLI_SCRIPTED_MODEL", "unused")
    # No window is named, so Desktop cannot choose among three: the attempt fails, then the agent asks.
    adapter, context, ran = desktop_world(tmp_path, "click the button", "Which window did you mean?")
    outcome = asyncio.run(adapter.run(context, "click it", None))
    assert not ran and outcome.outcome == "blocked"


def test_plain_answer_after_verified_steps_is_complete(tmp_path):
    h, _ = make_harness(
        tmp_path,
        [
            {
                "calls": [
                    {"name": "use_capability", "arguments": {"capability": "search", "instruction": "q"}}
                ]
            },
            {"text": "Node.js LTS is v24 (source: nodejs.org)."},
        ],
    )

    async def delegate(capability, instruction):
        return {"outcome": "verified_complete", "message": "ok", "results": [], "evidence_ids": ["e1"]}

    result = asyncio.run(h.agent(delegate=delegate, capabilities=("search",)).run("node lts?"))
    assert result.outcome == "verified_complete" and result.evidence == ["e1"]


def test_agent_steps_need_no_coordinator_target():
    from types import SimpleNamespace

    from backend.panini.targets import resolve_target

    ctx = SimpleNamespace(session=SimpleNamespace(selected={}), task=SimpleNamespace(capability="agent"))
    assert asyncio.run(resolve_target(ctx, object(), "")) is None
