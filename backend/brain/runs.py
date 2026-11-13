"""Run records and checkpoints (docs/panini-enhancements/lld/V2-06-one-brain.md §5.6).

One row per agent run (a turn from any surface, or one background attempt). The checkpoint is the loop's
state after its last finished tool round, compressed; a parked or interrupted background run resumes from it,
and the ledger (V2-04) replays any step that already happened.
"""

import hashlib
import json
import time
import zlib
from datetime import datetime
from uuid import uuid4

MAX_CHECKPOINT = 512 * 1024
OUTPUT_CHARS = 2000
MAX_RESULT_CHARS = 1_000_000
STATES = {"running", "parked", "resumed", "completed", "partial", "blocked", "failed", "interrupted"}


class Runs:
    def __init__(self, store, clock=time.time):
        self.store, self.clock = store, clock

    def start(
        self,
        *,
        mode,
        principal,
        thread_id=None,
        task_id=None,
        parent_run_id=None,
        tier=None,
        budget=None,
        persona_id="panini",
    ) -> str:
        run_id, now = uuid4().hex, self.clock()
        self.store.execute(
            "INSERT INTO runs(id, thread_id, principal, mode, parent_run_id, task_id, state, tier, created, "
            "updated, budget, persona_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                run_id,
                thread_id,
                json.dumps(principal or {}),
                mode,
                parent_run_id,
                task_id,
                "running",
                tier,
                now,
                now,
                json.dumps(budget or {}),
                persona_id or "panini",  # A delegate's run is attributed to the specialist that ran it.
            ),
        )
        return run_id

    def update(self, run_id, *, tier=None, usage=None, state=None):
        fields, params = ["updated=?"], [self.clock()]
        for column, value in (("tier", tier), ("state", state)):
            if value is not None:
                fields.append(f"{column}=?")
                params.append(value)
        if usage is not None:
            fields.append("usage=?")
            params.append(json.dumps(usage))
        self.store.execute(f"UPDATE runs SET {', '.join(fields)} WHERE id=?", (*params, run_id))

    def checkpoint(self, run_id, state: dict) -> int:
        """Store the loop state; long tool outputs are cut and the oldest items dropped to fit."""
        state = dict(state)
        state["items"] = [_trim(item) for item in state.get("items", [])]
        blob = _pack(state)
        while len(blob) > MAX_CHECKPOINT and len(state["items"]) > 2:
            state["items"] = state["items"][len(state["items"]) // 4 or 1 :]
            state["dropped"] = True
            blob = _pack(state)
        self.store.execute("UPDATE runs SET checkpoint=?, updated=? WHERE id=?", (blob, self.clock(), run_id))
        return len(blob)

    def load_checkpoint(self, run_id) -> dict | None:
        row = self.store.one("SELECT checkpoint FROM runs WHERE id=?", (run_id,))
        if row is None or row["checkpoint"] is None:
            return None
        return json.loads(zlib.decompress(row["checkpoint"]).decode())

    def finish(self, run_id, state: str, result: dict, usage: dict | None = None):
        if state not in STATES:
            raise ValueError(f"Unknown run state {state!r}")
        encoded = json.dumps(result, ensure_ascii=False, default=str)
        if len(encoded) > MAX_RESULT_CHARS:
            encoded = json.dumps(
                {
                    "outcome": str(result.get("outcome", "partial"))[:100],
                    "message": str(result.get("message", ""))[:16000],
                    "result_truncated": True,
                    "source_sha256": hashlib.sha256(encoded.encode()).hexdigest(),
                },
                ensure_ascii=False,
            )
        self.store.execute(
            "UPDATE runs SET state=?, result=?, updated=?"
            + (", usage=?" if usage is not None else "")
            + " WHERE id=?",
            (
                state,
                encoded,
                self.clock(),
                *([json.dumps(usage)] if usage is not None else []),
                run_id,
            ),
        )

    def get(self, run_id) -> dict | None:
        row = self.store.one("SELECT * FROM runs WHERE id=?", (run_id,))
        return _view(row) if row else None

    def list(self, *, limit=50, thread_id=None, task_id=None) -> list[dict]:
        clauses, params = [], []
        for column, value in (("thread_id", thread_id), ("task_id", task_id)):
            if value:
                clauses.append(f"{column}=?")
                params.append(value)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = self.store.all(
            f"SELECT * FROM runs{where} ORDER BY created DESC LIMIT ?", (*params, max(1, min(limit, 500)))
        )
        return [_view(r) for r in rows]

    def resumable(self, task_id) -> dict | None:
        """The newest parked run of a background task that left a checkpoint."""
        row = self.store.one(
            "SELECT * FROM runs WHERE task_id=? AND state='parked' AND checkpoint IS NOT NULL "
            "ORDER BY created DESC LIMIT 1",
            (task_id,),
        )
        return _view(row) if row else None

    def close_parked(self, task_id, state: str) -> int:
        """A parked task was resumed (state 'resumed') or declined ('blocked'): its old runs never continue."""
        return self.store.execute(
            "UPDATE runs SET state=?, updated=? WHERE task_id=? AND state='parked'",
            (state, self.clock(), task_id),
        ).rowcount

    def spent_since(self, since: float) -> float:
        row = self.store.one(
            "SELECT COALESCE(SUM(json_extract(usage, '$.usd')), 0) AS usd FROM runs WHERE created >= ?",
            (since,),
        )
        return float(row["usd"] or 0.0)

    def close_parked_tree(self, task_id, state: str):
        """Declining a lead also closes its waiting members, including resumed attempts."""
        if state not in {"blocked", "interrupted"}:
            raise ValueError("A waiting run tree can only be blocked or interrupted.")
        self.store.execute(
            "WITH RECURSIVE family(id) AS ("
            "SELECT id FROM runs WHERE task_id=? UNION "
            "SELECT r.id FROM runs r JOIN family f ON r.parent_run_id=f.id) "
            "UPDATE runs SET state=?, updated=? WHERE state='parked' AND id IN (SELECT id FROM family)",
            (task_id, state, self.clock()),
        )

    def spent_today(self) -> float:
        midnight = datetime.fromtimestamp(self.clock()).replace(hour=0, minute=0, second=0, microsecond=0)
        return self.spent_since(midnight.timestamp())

    def recover(self) -> int:
        """Runs still marked running belonged to the previous process."""
        return self.store.execute(
            "UPDATE runs SET state='interrupted', updated=? WHERE state='running'", (self.clock(),)
        ).rowcount


def _pack(state) -> bytes:
    return zlib.compress(json.dumps(state, ensure_ascii=False, default=str).encode(), 6)


def _trim(item):
    if isinstance(item, dict) and item.get("type") == "function_call_output":
        output = item.get("output")
        if isinstance(output, str) and len(output) > OUTPUT_CHARS:
            return {**item, "output": output[:OUTPUT_CHARS] + "…"}
    return item


def _view(row) -> dict:
    view = {k: row[k] for k in row.keys() if k != "checkpoint"}
    for key in ("principal", "budget", "usage", "result"):
        if view.get(key):
            view[key] = json.loads(view[key])
    view["has_checkpoint"] = row["checkpoint"] is not None
    return view
