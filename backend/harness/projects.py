"""Durable, person-controlled context for groups of related tasks."""

import json
import time
from uuid import uuid4


class Projects:
    def __init__(self, store, clock=time.time):
        self.store, self.clock = store, clock

    def create(self, title, goal, context=""):
        title, goal, context = self._validate(title, goal, context)
        project_id, now = uuid4().hex, self.clock()
        self.store.execute(
            "INSERT INTO projects(id,title,goal,context,status,created,updated) VALUES (?,?,?,?,?,?,?)",
            (project_id, title, goal, context, "active", now, now),
        )
        return self.get(project_id)

    @staticmethod
    def _validate(title, goal, context):
        title, goal, context = title.strip(), goal.strip(), context.strip()
        if not 1 <= len(title) <= 120 or not 1 <= len(goal) <= 4000:
            raise ValueError("Project title must be 1-120 characters and goal 1-4000 characters.")
        if len(context) > 12000:
            raise ValueError("Project context must be at most 12000 characters.")
        return title, goal, context

    def get(self, project_id):
        row = self.store.one("SELECT * FROM projects WHERE id=?", (project_id,))
        if not row:
            raise KeyError("Unknown project")
        return row

    def list(self):
        return self.store.all("SELECT * FROM projects ORDER BY updated DESC LIMIT 100")

    def update(self, project_id, *, title=None, goal=None, context=None, status=None):
        current = self.get(project_id)
        title, goal, context = self._validate(
            current["title"] if title is None else title,
            current["goal"] if goal is None else goal,
            current["context"] if context is None else context,
        )
        status = current["status"] if status is None else status
        if status not in ("active", "paused", "completed"):
            raise ValueError("Project status must be active, paused or completed.")
        self.store.execute(
            "UPDATE projects SET title=?,goal=?,context=?,status=?,updated=? WHERE id=?",
            (title, goal, context, status, self.clock(), project_id),
        )
        return self.get(project_id)

    def tasks(self, project_id, limit=100):
        self.get(project_id)
        rows = self.store.all(
            "SELECT id FROM tasks WHERE json_extract(data, '$.project_id')=? ORDER BY updated DESC LIMIT ?",
            (project_id, limit),
        )
        return [row["id"] for row in rows]

    def verified_history(self, project_id, *, before_task=None, limit=5):
        """Recent successful results only; bounded to fit the agent context."""
        self.get(project_id)
        rows = self.store.all(
            "SELECT id, data FROM tasks WHERE json_extract(data, '$.project_id')=? "
            "AND id!=? AND json_extract(data, '$.result.outcome')='verified_complete' "
            "AND json_array_length(json_extract(data, '$.result.evidence'))>0 "
            "ORDER BY updated DESC LIMIT ?",
            (project_id, before_task or "", limit),
        )
        history = []
        for row in rows:
            data = json.loads(row["data"])
            result = data.get("result") or {}
            history.append(
                {
                    "task_id": row["id"],
                    "title": data["title"][:120],
                    "result": result.get("message", "")[:1000],
                    "evidence": result["evidence"][:5],
                }
            )
        return history
