import asyncio

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
