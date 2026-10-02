"""Durable background tasks: leased, checkpointed, retried, and never blindly replayed.

States: queued -> running -> completed | failed | needs_review | cancelled.
A worker claims a due task with a time-limited lease and heartbeats while it runs.
If the lease expires (crash, sleep, kill), recovery requeues the task only when no
effect was dispatched; otherwise the outcome is unknown and a person reviews it.
Recurring tasks (every_minutes) are rescheduled after each run.
"""

import asyncio
import json
import logging
import time
from uuid import uuid4

logger = logging.getLogger(__name__)
LEASE_SECONDS = 90
MAX_ATTEMPTS = 3
REVIEW_SECONDS = 24 * 3600


class Tasks:
    def __init__(self, store, clock=time.time):
        self.store, self.clock = store, clock

    # Records --------------------------------------------------------------
    def submit(
        self,
        goal,
        *,
        run_at=None,
        every_minutes=None,
        title="",
        origin="user",
        condition=None,
        hold=False,
        project_id=None,
    ):
        """Queue a task; hold=True waits for the person's approval first (e.g. a request from another agent)."""
        goal = " ".join(str(goal).split())
        if not 1 <= len(goal) <= 4000:
            raise ValueError("A task goal must be 1-4000 characters.")
        if every_minutes is not None and not 5 <= int(every_minutes) <= 7 * 24 * 60:
            raise ValueError("Repeat every 5 minutes to 7 days.")
        if condition and not every_minutes:
            raise ValueError("A watch needs every_minutes.")
        if project_id:
            project = self.store.one("SELECT status FROM projects WHERE id=?", (project_id,))
            if not project or project["status"] != "active":
                raise ValueError("A task needs an active project.")
        task_id = uuid4().hex
        data = {
            "goal": goal,
            "title": (title or goal)[:120],
            "origin": origin,
            "project_id": project_id,
            "every_minutes": int(every_minutes) if every_minutes else None,
            # A watch is a recurring check that notifies only when its condition holds.
            "kind": "watch" if condition else "task",
            "condition": " ".join(str(condition).split())[:1000] if condition else None,
            "attempts": 0,
            "dispatched": 0,
            "checkpoint": None,
            "result": None,
            "created": self.clock(),
        }
        if hold:
            self._write(task_id, data, "needs_review", None)
            self.store.event(task_id, "awaiting_approval", data["title"])
            self.open_review(task_id, "approval", f"{data['title']}\n\nRequest: {goal[:1500]}")
            return self.get(task_id)
        self._write(task_id, data, "queued", run_at or self.clock())
        self.store.event(task_id, "queued", data["title"])
        return self.get(task_id)

    def _write(self, task_id, data, status, next_run, lease_id=None, lease_until=None):
        self.store.execute(
            "INSERT INTO tasks(id, data, status, next_run, lease_id, lease_until, updated) VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET data=excluded.data, status=excluded.status, next_run=excluded.next_run, "
            "lease_id=excluded.lease_id, lease_until=excluded.lease_until, updated=excluded.updated",
            (
                task_id,
                json.dumps(data, ensure_ascii=False),
                status,
                next_run,
                lease_id,
                lease_until,
                self.clock(),
            ),
        )

    def get(self, task_id):
        row = self.store.one("SELECT * FROM tasks WHERE id=?", (task_id,))
        if not row:
            raise KeyError("Unknown task")
        return {
            "id": row["id"],
            "status": row["status"],
            "next_run": row["next_run"],
            **json.loads(row["data"]),
        }

    def list(self, limit=100):
        rows = self.store.all("SELECT id FROM tasks ORDER BY updated DESC LIMIT ?", (limit,))
        return [self.get(r["id"]) for r in rows]

    def events(self, task_id=None, since=0, limit=200):
        if task_id:
            return self.store.all(
                "SELECT * FROM events WHERE task_id=? AND id>? ORDER BY id LIMIT ?", (task_id, since, limit)
            )
        return self.store.all("SELECT * FROM events WHERE id>? ORDER BY id DESC LIMIT ?", (since, limit))

    def cancel(self, task_id):
        task = self.get(task_id)
        if task["status"] in ("completed", "failed", "cancelled"):
            return task
        self.store.execute(
            "UPDATE tasks SET status='cancelled', lease_id=NULL, lease_until=NULL, updated=? WHERE id=?",
            (self.clock(), task_id),
        )
        self.store.event(task_id, "cancelled", "Cancelled by the person.")
        return self.get(task_id)

    def counts(self):
        rows = self.store.all("SELECT status, COUNT(*) AS n FROM tasks GROUP BY status")
        counts = {r["status"]: r["n"] for r in rows}
        counts["reviews"] = self.store.one(
            "SELECT COUNT(*) AS n FROM reviews WHERE status='open' AND expires>?", (self.clock(),)
        )["n"]
        return counts

    # Leases ---------------------------------------------------------------
    def claim(self):
        """Atomically lease the oldest due task."""
        now, lease = self.clock(), uuid4().hex
        with self.store.lock:
            row = self.store.one(
                "SELECT tasks.id FROM tasks LEFT JOIN projects ON projects.id=json_extract(tasks.data, '$.project_id') "
                "WHERE tasks.status='queued' AND tasks.next_run<=? "
                "AND (json_extract(tasks.data, '$.project_id') IS NULL OR projects.status='active') "
                "ORDER BY tasks.next_run LIMIT 1",
                (now,),
            )
            if not row:
                return None
            self.store.execute(
                "UPDATE tasks SET status='running', lease_id=?, lease_until=?, updated=? WHERE id=?",
                (lease, now + LEASE_SECONDS, now, row["id"]),
            )
        task = self.get(row["id"])
        task["attempts"] += 1
        self._save(task, lease)
        self.store.event(task["id"], "started", f"Attempt {task['attempts']}")
        return {**task, "lease_id": lease}

    def _save(self, task, lease_id):
        data = {k: v for k, v in task.items() if k not in ("id", "status", "next_run", "lease_id")}
        self.store.execute(
            "UPDATE tasks SET data=?, updated=? WHERE id=? AND lease_id=?",
            (json.dumps(data, ensure_ascii=False), self.clock(), task["id"], lease_id),
        )

    def owns(self, task_id, lease_id):
        row = self.store.one("SELECT status, lease_id FROM tasks WHERE id=?", (task_id,))
        return bool(row) and row["status"] == "running" and row["lease_id"] == lease_id

    def heartbeat(self, task_id, lease_id):
        return bool(
            self.store.execute(
                "UPDATE tasks SET lease_until=? WHERE id=? AND lease_id=? AND status='running'",
                (self.clock() + LEASE_SECONDS, task_id, lease_id),
            ).rowcount
        )

    def resume(self, task_id, approved_digest=None):
        """Requeue a parked task; the approved action may run once without asking again (V2-05)."""
        task = self.get(task_id)
        if task["status"] != "waiting_input":
            return task
        if approved_digest:
            task.setdefault("pre_approved", []).append(approved_digest)
        task["waiting_on"] = None
        self._write(task_id, _data(task), "queued", self.clock())
        self.store.event(task_id, "resumed", "Approved; continuing.")
        return self.get(task_id)

    def decline(self, task_id, message):
        task = self.get(task_id)
        if task["status"] != "waiting_input":
            return task
        task["waiting_on"] = None
        task["result"] = {
            "outcome": "cancelled",
            "message": message[:1000],
            "evidence": [],
            "at": self.clock(),
        }
        self._write(task_id, _data(task), "cancelled", None)
        self.store.event(task_id, "cancelled", message[:1000])
        return self.get(task_id)

    def consume_preapproval(self, task_id, approved_digest) -> bool:
        task = self.get(task_id)
        approved = task.get("pre_approved") or []
        if approved_digest not in approved:
            return False
        approved.remove(approved_digest)
        task["pre_approved"] = approved
        self.store.execute(
            "UPDATE tasks SET data=? WHERE id=?", (json.dumps(_data(task), ensure_ascii=False), task_id)
        )
        return True

    def checkpoint(self, task_id, lease_id, checkpoint=None, *, dispatched=False):
        """Record progress; dispatched marks that an effect may have left the process."""
        if not self.owns(task_id, lease_id):
            raise LeaseLost(task_id)
        task = self.get(task_id)
        if checkpoint is not None:
            task["checkpoint"] = checkpoint
        if dispatched:
            task["dispatched"] += 1
        self._save(task, lease_id)

    def finish(self, task_id, lease_id, outcome, message, evidence=None):
        if not self.owns(task_id, lease_id):
            raise LeaseLost(task_id)
        task = self.get(task_id)
        task["result"] = {
            "outcome": outcome,
            "message": message[:4000],
            "evidence": (evidence or [])[:20],
            "at": self.clock(),
        }
        task["dispatched"], task["checkpoint"] = 0, None
        if outcome == "waiting_input":
            # V2-05: the run parked on an interrupt; it resumes when the person decides.
            task["waiting_on"] = next(
                (str(e).split(":", 1)[1] for e in (evidence or []) if str(e).startswith("interrupt:")), None
            )
            self._write(task_id, _data(task), "waiting_input", None)
            self.store.event(task_id, "waiting_input", message[:1000])
            return
        if task["every_minutes"] and outcome != "cancelled":
            status, next_run, task["attempts"] = "queued", self.clock() + task["every_minutes"] * 60, 0
        else:
            status, next_run = ("completed" if outcome == "verified_complete" else "failed"), None
            if outcome == "clarification":
                status = "needs_review"
                self.open_review(task_id, "question", message)
        self._write(task_id, _data(task), status, next_run)
        self.store.event(task_id, outcome, message[:1000])

    def retry(self, task_id, lease_id, error):
        if not self.owns(task_id, lease_id):
            return
        task = self.get(task_id)
        if task["dispatched"]:
            self._unknown(task, "It stopped after an action was sent; its outcome is unknown.")
        elif task["attempts"] >= MAX_ATTEMPTS:
            self._write(task_id, _data(task), "failed", None)
            self.store.event(task_id, "failed", str(error)[:1000])
        else:
            delay = 30 * 2 ** (task["attempts"] - 1)
            self._write(task_id, _data(task), "queued", self.clock() + delay)
            self.store.event(task_id, "retrying", f"{str(error)[:500]} Retrying in {delay} s.")

    def recover(self):
        """Handle leases that expired because a worker died or the PC slept."""
        rows = self.store.all(
            "SELECT id FROM tasks WHERE status='running' AND lease_until<?", (self.clock(),)
        )
        for row in rows:
            task = self.get(row["id"])
            if task["dispatched"]:
                self._unknown(
                    task, "PANINI stopped while this task was acting; check the result before rerunning."
                )
            else:
                self._write(task["id"], _data(task), "queued", self.clock())
                self.store.event(
                    task["id"], "recovered", "Requeued after an interrupted run with no effects."
                )
        return len(rows)

    def _unknown(self, task, message):
        self._write(task["id"], _data(task), "needs_review", None)
        self.store.event(task["id"], "outcome_unknown", message)
        self.open_review(task["id"], "outcome_unknown", message)

    # Reviews --------------------------------------------------------------
    def open_review(self, task_id, kind, message, data=None):
        review_id = uuid4().hex
        self.store.execute(
            "INSERT INTO reviews(id, task_id, data, status, expires, created) VALUES (?,?,?,?,?,?)",
            (
                review_id,
                task_id,
                json.dumps({"kind": kind, "message": message[:2000], **(data or {})}),
                "open",
                self.clock() + REVIEW_SECONDS,
                self.clock(),
            ),
        )
        return review_id

    def reviews(self, include_closed=False):
        self.store.execute(
            "UPDATE reviews SET status='expired' WHERE status='open' AND expires<=?", (self.clock(),)
        )
        where = "" if include_closed else "WHERE status='open'"
        rows = self.store.all(f"SELECT * FROM reviews {where} ORDER BY created DESC LIMIT 100")
        return [
            {**{k: r[k] for k in ("id", "task_id", "status", "expires", "created")}, **json.loads(r["data"])}
            for r in rows
        ]

    def resolve(self, review_id, decision, answer=""):
        """decision: rerun (optionally with an answer appended to the goal) or dismiss."""
        row = self.store.one("SELECT * FROM reviews WHERE id=? AND status='open'", (review_id,))
        if not row or row["expires"] <= self.clock():
            raise KeyError("This review is no longer open.")
        if decision not in ("rerun", "dismiss"):
            raise ValueError("decision must be rerun or dismiss")
        self.store.execute("UPDATE reviews SET status=? WHERE id=?", (decision, review_id))
        task = self.get(row["task_id"])
        if decision == "rerun":
            if answer.strip():
                task["goal"] = f"{task['goal']}\nThe person answered: {answer.strip()[:1000]}"
            task["attempts"], task["dispatched"] = 0, 0
            self._write(task["id"], _data(task), "queued", self.clock())
        else:
            if json.loads(row["data"]).get("kind") == "approval":
                task["rejected"] = True  # The person declined another agent's request.
            self._write(task["id"], _data(task), "cancelled", None)
        self.store.event(task["id"], f"review_{decision}", answer[:500])
        return self.get(task["id"])


class LeaseLost(RuntimeError):
    pass


def _data(task):
    return {k: v for k, v in task.items() if k not in ("id", "status", "next_run", "lease_id")}


class Worker:
    """Runs due tasks in the backend process, one at a time, with heartbeats."""

    def __init__(self, tasks, runner, poll=5.0, on_cycle=None):
        self.tasks, self.runner, self.poll = tasks, runner, poll
        self.on_cycle = on_cycle
        self.current = None
        self._stop = asyncio.Event()
        self._job = None

    def start(self):
        self.tasks.recover()
        self._job = asyncio.create_task(self._loop())

    async def close(self):
        self._stop.set()
        if self._job:
            self._job.cancel()
            await asyncio.gather(self._job, return_exceptions=True)

    async def run_once(self):
        task = self.tasks.claim()
        if not task:
            return False
        self.current = task
        beat = asyncio.create_task(self._heartbeat(task))
        try:
            outcome, message, evidence = await self.runner(task, self.tasks)
            self.tasks.finish(task["id"], task["lease_id"], outcome, message, evidence)
        except LeaseLost:
            logger.warning("Task %s lost its lease", task["id"])
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Background task %s failed", task["id"])
            self.tasks.retry(task["id"], task["lease_id"], exc)
        finally:
            beat.cancel()
            self.current = None
        return True

    async def _heartbeat(self, task):
        while True:
            await asyncio.sleep(LEASE_SECONDS / 3)
            if not self.tasks.heartbeat(task["id"], task["lease_id"]):
                return

    async def _loop(self):
        while not self._stop.is_set():
            try:
                self.tasks.recover()
                while await self.run_once():
                    pass
                if self.on_cycle:
                    self.on_cycle()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Background worker cycle failed")
            try:
                await asyncio.wait_for(self._stop.wait(), self.poll)
            except TimeoutError:
                pass
