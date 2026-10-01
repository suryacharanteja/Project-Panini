import asyncio
import json
from types import SimpleNamespace

import pytest
from panini_cli.agent.model import ScriptedModel

from backend.harness.project_planner import ProjectPlanner
from backend.harness.projects import Projects
from backend.harness.store import Store
from backend.harness.tasks import Tasks


def planner(store, steps):
    model = ScriptedModel([{"calls": [{"name": "propose_project_plan", "arguments": {"steps": steps}}]}])
    projects, tasks = Projects(store), Tasks(store)
    return ProjectPlanner(projects, tasks, lambda: model), projects, tasks


def test_approved_plan_advances_once_after_verified_results_and_restart(tmp_path):
    path = tmp_path / "plan.sqlite3"
    store = Store(path)
    plan, projects, tasks = planner(
        store,
        [
            {"title": "Find sources", "goal": "Find primary sources for the launch"},
            {"title": "Check changes", "goal": "Check recent changes against those sources"},
        ],
    )
    project = projects.create("Launch", "Prepare launch")
    draft = asyncio.run(plan.draft(project["id"]))
    assert draft["status"] == "draft" and tasks.list() == []
    approved = plan.approve(project["id"], draft["revision"])
    first_id = approved["steps"][0]["task_id"]
    assert first_id and approved["steps"][1]["task_id"] is None
    with pytest.raises(ValueError, match="already approved"):
        plan.approve(project["id"], draft["revision"])
    plan.reconcile()
    assert len(tasks.list()) == 1
    store.close()

    store = Store(path)
    plan = ProjectPlanner(Projects(store), Tasks(store), lambda: None)
    tasks = Tasks(store)
    claim = tasks.claim()
    assert claim["id"] == first_id
    tasks.finish(first_id, claim["lease_id"], "verified_complete", "Sources found", ["e1"])
    plan.reconcile()
    second_id = plan.get(project["id"])["steps"][1]["task_id"]
    assert second_id and second_id != first_id
    plan.reconcile()
    assert len(tasks.list()) == 2
    claim = tasks.claim()
    assert claim["id"] == second_id
    tasks.finish(second_id, claim["lease_id"], "verified_complete", "Changes checked", ["e2"])
    plan.reconcile()
    assert plan.get(project["id"])["status"] == "completed"
    store.close()


def test_plan_holds_on_unverified_result(tmp_path):
    store = Store(tmp_path / "plan.sqlite3")
    plan, projects, tasks = planner(
        store,
        [
            {"title": "Research", "goal": "Find sources"},
            {"title": "Follow up", "goal": "Check changes"},
        ],
    )
    project = projects.create("Research", "Find facts")
    draft = asyncio.run(plan.draft(project["id"]))
    plan.approve(project["id"], draft["revision"])
    claim = tasks.claim()
    tasks.finish(claim["id"], claim["lease_id"], "verified_complete", "Claim without evidence")
    plan.reconcile()
    assert plan.get(project["id"])["status"] == "needs_review"
    assert len(tasks.list()) == 1
    store.close()


def test_draft_cannot_overwrite_a_concurrently_approved_plan(tmp_path):
    store = Store(tmp_path / "race.sqlite3")
    plan, projects, tasks = planner(store, [{"title": "Research", "goal": "Find sources"}])
    project = projects.create("Research", "Find facts")
    initial = asyncio.run(plan.draft(project["id"]))

    class SlowModel:
        async def respond(self, *_args):
            plan.approve(project["id"], initial["revision"])
            return SimpleNamespace(
                calls=[
                    SimpleNamespace(
                        name="propose_project_plan",
                        arguments=json.dumps({"steps": [{"title": "Replacement", "goal": "Find more"}]}),
                    )
                ]
            )

    plan.model_factory = SlowModel
    with pytest.raises(ValueError, match="changed while drafting"):
        asyncio.run(plan.draft(project["id"]))
    assert plan.get(project["id"])["status"] == "running"
    assert plan.get(project["id"])["revision"] == initial["revision"]
    assert len(tasks.list()) == 1
    store.close()


def test_changed_context_requires_redraft_before_approval(tmp_path):
    store = Store(tmp_path / "context.sqlite3")
    plan, projects, tasks = planner(store, [{"title": "Research", "goal": "Find sources"}])
    project = projects.create("Research", "Find facts")
    draft = asyncio.run(plan.draft(project["id"]))
    projects.update(project["id"], context="A different scope")
    with pytest.raises(ValueError, match="context changed"):
        plan.approve(project["id"], draft["revision"])
    assert tasks.list() == []
    assert plan.get(project["id"])["status"] == "draft"
    store.close()


def test_cancel_plan_fences_the_running_task_and_preserves_unrelated_work(tmp_path):
    store = Store(tmp_path / "cancel.sqlite3")
    plan, projects, tasks = planner(store, [{"title": "Research", "goal": "Find sources"}])
    project = projects.create("Research", "Find facts")
    draft = asyncio.run(plan.draft(project["id"]))
    plan.approve(project["id"], draft["revision"])
    claim = tasks.claim()
    other = tasks.submit("Different task", project_id=project["id"])
    with pytest.raises(ValueError, match="plan changed"):
        plan.cancel(project["id"], draft["revision"] + 1)
    assert tasks.owns(claim["id"], claim["lease_id"])
    assert plan.cancel(project["id"], draft["revision"])["status"] == "cancelled"
    assert not tasks.owns(claim["id"], claim["lease_id"])
    assert tasks.get(other["id"])["status"] == "queued"
    plan.reconcile()
    assert len(tasks.list()) == 2
    store.close()


def test_running_plan_stops_before_dispatch_after_scope_changes(tmp_path):
    store = Store(tmp_path / "scope.sqlite3")
    plan, projects, tasks = planner(store, [{"title": "Research", "goal": "Find sources"}])
    project = projects.create("Research", "Find facts")
    draft = asyncio.run(plan.draft(project["id"]))
    plan.approve(project["id"], draft["revision"])
    projects.update(project["id"], goal="Different goal")
    plan.reconcile()
    assert plan.get(project["id"])["status"] == "needs_review"
    assert "context changed" in plan.get(project["id"])["review_reason"]
    assert tasks.claim() is None
    store.close()


def test_app_planner_receives_only_granted_read_only_tools_and_queues_criteria(tmp_path):
    store = Store(tmp_path / "apps.sqlite3")
    projects, tasks = Projects(store), Tasks(store)
    server = SimpleNamespace(
        name="notes",
        error="",
        spec={"background_read_only": ["lookup", "write"]},
        tools=[
            {"name": "lookup", "description": "Search notes", "read_only": True},
            {"name": "private_lookup", "description": "No grant", "read_only": True},
            {"name": "write", "description": "Change notes", "read_only": False},
        ],
    )

    class Connectors:
        servers = {"notes": server}

        async def status(self):
            return []

    class Model:
        async def respond(self, _prompt, messages, _tools):
            data = json.loads(messages[0]["content"])
            assert [item["tool"] for item in data["app_tools"]] == ["lookup"]
            return SimpleNamespace(
                calls=[
                    SimpleNamespace(
                        name="propose_project_plan",
                        arguments=json.dumps(
                            {
                                "steps": [
                                    {
                                        "title": "Find notes",
                                        "goal": "Use notes.lookup to find launch decisions",
                                        "success_criteria": "Cite a connector receipt for each finding",
                                    }
                                ]
                            }
                        ),
                    )
                ]
            )

    plan = ProjectPlanner(projects, tasks, Model, connectors=Connectors())
    project = projects.create("Launch", "Review launch decisions")
    draft = asyncio.run(plan.draft(project["id"]))
    plan.approve(project["id"], draft["revision"])
    assert "Cite a connector receipt" in tasks.list()[0]["goal"]
    assert plan.get(project["id"])["app_tools"] == draft["app_tools"]
    store.close()


def test_interrupted_plan_step_recovers_without_duplicate_queue(tmp_path):
    path = tmp_path / "interrupted.sqlite3"

    class Clock:
        value = 1000.0

        def __call__(self):
            return self.value

    clock = Clock()
    store = Store(path)
    projects, tasks = Projects(store, clock), Tasks(store, clock)
    model = ScriptedModel(
        [
            {
                "calls": [
                    {
                        "name": "propose_project_plan",
                        "arguments": {"steps": [{"title": "Research", "goal": "Find sources"}]},
                    }
                ]
            }
        ]
    )
    plan = ProjectPlanner(projects, tasks, lambda: model, clock)
    project = projects.create("Research", "Find sources")
    draft = asyncio.run(plan.draft(project["id"]))
    plan.approve(project["id"], draft["revision"])
    first = tasks.claim()
    store.close()  # Simulate a process dying after it leased the task.

    clock.value += 91
    store = Store(path)
    projects, tasks = Projects(store, clock), Tasks(store, clock)
    plan = ProjectPlanner(projects, tasks, lambda: None, clock)
    assert tasks.recover() == 1
    plan.reconcile()
    assert len(tasks.list()) == 1
    resumed = tasks.claim()
    assert resumed["id"] == first["id"]
    tasks.checkpoint(resumed["id"], resumed["lease_id"], dispatched=True)
    store.close()  # An action was dispatched; recovery must not replay it.

    clock.value += 91
    store = Store(path)
    projects, tasks = Projects(store, clock), Tasks(store, clock)
    plan = ProjectPlanner(projects, tasks, lambda: None, clock)
    assert tasks.recover() == 1
    plan.reconcile()
    assert len(tasks.list()) == 1
    assert tasks.get(first["id"])["status"] == "needs_review"
    assert plan.get(project["id"])["status"] == "needs_review"
    store.close()
