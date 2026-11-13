"""Durable ambient event routing through PANINI's existing leased background worker."""

import asyncio
import json
import logging
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from .. import v2flags
from ..fabric.routing import quiet_until
from ..interrupts import InterruptSpec, digest
from .rules import TriggerEvent, goal, matches, validate_rule

logger = logging.getLogger(__name__)
DAY = 86400
DEDUP_DAYS = 7
GLOBAL_DAILY_CAP = 200


class TriggerBus:
    def __init__(self, harness, *, clock=time.time, condition=None):
        self.h, self.store, self.clock = harness, harness.store, clock
        self.condition = condition or self._jev_condition
        self.loop = None
        self._stop = asyncio.Event()
        self.file_watchers = {}
        self.watcher_retry = {}
        self._was_enabled = False
        harness.interrupts.listeners.append(self.review_decided)

    @property
    def enabled(self):
        return v2flags.enabled("PANINI_TRIGGERS") and v2flags.enabled("PANINI_V2_ONE_BRAIN")

    @contextmanager
    def transaction(self):
        with self.store.lock:
            nested = self.store.db.in_transaction
            marker = "triggers_" + uuid4().hex
            self.store.db.execute(f"SAVEPOINT {marker}" if nested else "BEGIN IMMEDIATE")
            try:
                yield
                self.store.db.execute(f"RELEASE {marker}" if nested else "COMMIT")
            except BaseException:
                self.store.db.execute(f"ROLLBACK TO {marker}" if nested else "ROLLBACK")
                if nested:
                    self.store.db.execute(f"RELEASE {marker}")
                raise

    def list(self):
        return [
            self.get(row["id"]) for row in self.store.all("SELECT id FROM triggers ORDER BY created DESC")
        ]

    def get(self, rule_id):
        row = self.store.one("SELECT * FROM triggers WHERE id=?", (rule_id,))
        if not row:
            raise KeyError("Unknown trigger rule.")
        return {**row, **json.loads(row["data"])}

    def status(self):
        return {
            "enabled": self.enabled,
            "flag": v2flags.enabled("PANINI_TRIGGERS"),
            "needs_one_brain": v2flags.enabled("PANINI_TRIGGERS")
            and not v2flags.enabled("PANINI_V2_ONE_BRAIN"),
            "rules": self.list(),
            "responsibilities": self.responsibilities.list(),
            "bindings": self.store.all(
                "SELECT source,server,tool,last_polled,last_error FROM trigger_source_bindings"
            ),
            "recent": self.store.all(
                "SELECT source,received FROM trigger_events ORDER BY received DESC LIMIT 20"
            ),
        }

    def cancel_pending(self):
        """A feature rollback must revoke already queued and leased automation tasks."""
        for row in self.store.all(
            "SELECT id FROM tasks WHERE status IN ('queued','running','waiting_input','needs_review') "
            "AND (json_extract(data,'$.kind')='trigger' OR json_extract(data,'$.kind')='responsibility')"
        ):
            self.h.tasks.cancel(row["id"])

    def propose(self, data):
        validate_rule(data)
        self.h.personas.get(data["persona"])
        if data["persona"] != "panini" and not v2flags.enabled("PANINI_PERSONAS"):
            raise ValueError("Enable personas before assigning this rule to a specialist.")
        if data["source"] == "files":
            roots = {
                str(Path(rule["match"]["watch_root"]).resolve())
                for rule in self.list()
                if rule["source"] == "files" and rule["status"] in {"proposed", "active"}
            }
            roots.add(str(Path(data["match"]["watch_root"]).resolve()))
            if len(roots) > 8:
                raise ValueError("At most eight watched folders may be active at once.")
        identifier, now = uuid4().hex, self.clock()
        # A draft cannot fire, even if the feature flag is already on.
        with self.transaction():
            self.store.execute(
                "INSERT INTO triggers(id,data,status,created,updated) VALUES (?,?,'proposed',?,?)",
                (identifier, json.dumps(data, ensure_ascii=False), now, now),
            )
            action = {
                "type": "activate_trigger",
                "id": identifier,
                "revision": 1,
                "rule_digest": digest(data),
            }
            view = self.h.interrupts.open(
                InterruptSpec(
                    "review",
                    "trigger_rule_activation",
                    "Activate trigger: " + data["name"],
                    "Review when it runs, what it reads, its goal and its daily limit.",
                    preview=data,
                    action=action,
                    level="verify",
                    ttl_s=7 * DAY,
                ),
                run_id="trigger-proposal:" + identifier,
                task_id="trigger-proposal:" + identifier,
                owner_principal={"surface": "trigger_proposal"},
            )
            self.store.execute("UPDATE triggers SET review_id=? WHERE id=?", (view["id"], identifier))
        return self.get(identifier)

    def review_decided(self, event, view):
        if event not in {"interrupt.resolved", "interrupt.expired"} or view.get("reason") not in {
            "trigger_rule_activation",
            "responsibility_activation",
        }:
            return
        action = view.get("action") or {}
        approved = (
            event == "interrupt.resolved"
            and view["status"] == "resolved"
            and (view.get("decision") or {}).get("payload", {}).get("approved") is True
        )
        if view["reason"] == "responsibility_activation":
            self.responsibilities.review_decided(view, action, approved)
            return
        rule = self.store.one("SELECT * FROM triggers WHERE id=?", (action.get("id"),))
        if not rule or rule["review_id"] != view["id"] or rule["status"] != "proposed":
            return
        data = json.loads(rule["data"])
        if rule["revision"] != action.get("revision") or digest(data) != action.get("rule_digest"):
            return
        self.store.execute(
            "UPDATE triggers SET status=?,updated=? WHERE id=? AND status='proposed'",
            ("active" if approved else "paused", self.clock(), rule["id"]),
        )

    def pause(self, rule_id):
        rule = self.get(rule_id)
        if rule["status"] != "retired":
            self.store.execute(
                "UPDATE triggers SET status='paused',updated=? WHERE id=?", (self.clock(), rule_id)
            )
            self._cancel_rule_tasks(rule_id)
        return self.get(rule_id)

    def resume(self, rule_id):
        rule = self.get(rule_id)
        if rule["status"] != "paused" or not rule["review_id"]:
            raise ValueError("Only an approved, paused rule can resume.")
        review = self.h.interrupts.get(rule["review_id"])
        if (
            review["status"] != "resolved"
            or (review.get("decision") or {}).get("payload", {}).get("approved") is not True
        ):
            raise ValueError("The owner must approve this rule first.")
        self.store.execute(
            "UPDATE triggers SET status='active',updated=? WHERE id=?", (self.clock(), rule_id)
        )
        return self.get(rule_id)

    def retire(self, rule_id):
        self.get(rule_id)
        self.store.execute(
            "UPDATE triggers SET status='retired',updated=? WHERE id=?", (self.clock(), rule_id)
        )
        self._cancel_rule_tasks(rule_id)
        return self.get(rule_id)

    def _cancel_rule_tasks(self, rule_id):
        for row in self.store.all(
            "SELECT task_id FROM trigger_firings WHERE rule_id=? AND status IN ('queued','running','waiting_input','needs_review')",
            (rule_id,),
        ):
            self.h.tasks.cancel(row["task_id"])

    def mark_own_write(self, path, receipt_id=None):
        resolved = str(Path(path).resolve()).casefold()
        self.store.execute(
            "INSERT INTO trigger_self_writes(path,at,receipt_id) VALUES (?,?,?) "
            "ON CONFLICT(path) DO UPDATE SET at=excluded.at,receipt_id=excluded.receipt_id",
            (resolved, self.clock(), receipt_id),
        )

    async def emit(self, event: TriggerEvent):
        if not self.enabled:
            return {"status": "disabled", "queued": []}
        if not isinstance(event, TriggerEvent):
            raise ValueError("A typed trigger event is required.")
        now = self.clock()
        if event.source == "files":
            path = str(Path(event.data["path"]).resolve()).casefold()
            own = self.store.one("SELECT at FROM trigger_self_writes WHERE path=?", (path,))
            if own and 0 <= now - own["at"] < 10:
                return {"status": "self_write", "queued": []}
        with self.transaction():
            self.store.execute("DELETE FROM trigger_events WHERE received<?", (now - DEDUP_DAYS * DAY,))
            self.store.execute("DELETE FROM trigger_self_writes WHERE at<?", (now - 60,))
            identifier = uuid4().hex
            inserted = self.store.execute(
                "INSERT OR IGNORE INTO trigger_events(id,source,dedupe_key,event,occurred,received) VALUES (?,?,?,?,?,?)",
                (
                    identifier,
                    event.source,
                    event.dedupe_key,
                    json.dumps(event.record(), ensure_ascii=False),
                    event.occurred_at,
                    now,
                ),
            ).rowcount
            if not inserted:
                return {"status": "duplicate", "queued": []}
        queued = []
        for rule in self.list():
            if rule["status"] != "active" or not matches(rule, event):
                continue
            responsibility_id = self.responsibilities.owner_of(rule["id"])
            if responsibility_id and self.responsibilities.get(responsibility_id)["status"] != "active":
                continue
            if rule["condition"] is not None:
                try:
                    if not await self.condition(rule["condition"], event):
                        continue
                except Exception:  # noqa: BLE001 -- no speculative run when the condition provider fails.
                    logger.warning("Trigger condition unavailable", exc_info=True)
                    continue
            # Recheck activation, cooldown and both quotas inside the same write lock as task creation.
            with self.transaction():
                current = self.store.one("SELECT status,last_fired FROM triggers WHERE id=?", (rule["id"],))
                if not current or current["status"] != "active":
                    continue
                now = self.clock()
                quiet = rule["quiet_hours"]
                due = quiet_until(now, {"enabled": True, **quiet}) if quiet else now
                day = int(now // DAY) * DAY
                due_day = int(due // DAY) * DAY
                created_count = self.store.one(
                    "SELECT COUNT(*) n FROM trigger_firings WHERE created>=? AND created<?",
                    (day, day + DAY),
                )["n"]
                due_count = self.store.one(
                    "SELECT COUNT(*) n FROM trigger_firings WHERE due>=? AND due<?",
                    (due_day, due_day + DAY),
                )["n"]
                created_mine = self.store.one(
                    "SELECT COUNT(*) n FROM trigger_firings WHERE rule_id=? AND created>=? AND created<?",
                    (rule["id"], day, day + DAY),
                )["n"]
                due_mine = self.store.one(
                    "SELECT COUNT(*) n FROM trigger_firings WHERE rule_id=? AND due>=? AND due<?",
                    (rule["id"], due_day, due_day + DAY),
                )["n"]
                if (
                    created_count >= GLOBAL_DAILY_CAP
                    or due_count >= GLOBAL_DAILY_CAP
                    or created_mine >= rule["max_per_day"]
                    or due_mine >= rule["max_per_day"]
                ):
                    continue
                if current["last_fired"] is not None and now - current["last_fired"] < rule["cooldown_s"]:
                    continue
                if responsibility_id and self.responsibilities.get(responsibility_id)["status"] != "active":
                    continue
                if responsibility_id and not self.responsibilities.can_spend(
                    responsibility_id, rule["action"]["budget_usd"], run_at=due
                ):
                    continue
                task = self.h.tasks.submit(
                    goal(rule, event),
                    run_at=due,
                    title=rule["name"],
                    origin="trigger:" + rule["id"],
                    kind="trigger",
                    persona_id=rule["persona"],
                    budget_usd=rule["action"]["budget_usd"],
                )
                self.store.execute(
                    "INSERT INTO trigger_firings(id,event_id,rule_id,responsibility_id,task_id,created,due) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (uuid4().hex, identifier, rule["id"], responsibility_id, task["id"], now, due),
                )
                self.store.execute(
                    "UPDATE triggers SET last_fired=?,updated=? WHERE id=?", (now, now, rule["id"])
                )
                queued.append(task["id"])
        return {"status": "accepted", "queued": queued}

    async def _jev_condition(self, question, event):
        from .. import jev_client, usage_log

        answer = await asyncio.to_thread(
            jev_client.ask,
            json.dumps({"event": event.record()}, ensure_ascii=False),
            {"matches": {"type": "boolean", "instructions": question}},
        )
        usage_log.record_jev("trigger.condition", answer)
        return float(answer["answers"]["matches"]["probability"]) >= 0.7

    def reconcile(self):
        for row in self.store.all(
            "SELECT f.task_id,f.rule_id,f.responsibility_id,f.status,t.status task_status "
            "FROM trigger_firings f JOIN tasks t ON t.id=f.task_id WHERE f.status<>t.status"
        ):
            self.store.execute(
                "UPDATE trigger_firings SET status=? WHERE task_id=?", (row["task_status"], row["task_id"])
            )
            if row["responsibility_id"]:
                self.responsibilities.observe(row["responsibility_id"], row["task_id"], row["task_status"])
            if row["task_status"] == "failed":
                recent = self.store.all(
                    "SELECT status FROM trigger_firings WHERE rule_id=? ORDER BY created DESC LIMIT 5",
                    (row["rule_id"],),
                )
                if len(recent) == 5 and all(item["status"] == "failed" for item in recent):
                    rule = self.get(row["rule_id"])
                    if rule["status"] == "active":
                        self.pause(row["rule_id"])
                        self.h.notices.add(
                            "trigger",
                            "Trigger paused after repeated failures",
                            rule["name"],
                            dedupe="trigger-paused:" + row["rule_id"] + ":" + row["task_id"],
                        )

    def start(self):
        if self.loop is None:
            self.loop = asyncio.create_task(self._loop())

    async def close(self):
        self._stop.set()
        if self.loop:
            self.loop.cancel()
            await asyncio.gather(self.loop, return_exceptions=True)
            self.loop = None
        await self._close_watchers()

    async def _close_watchers(self):
        for task in self.file_watchers.values():
            task.cancel()
        if self.file_watchers:
            await asyncio.gather(*self.file_watchers.values(), return_exceptions=True)
        self.file_watchers.clear()

    async def _loop(self):
        while not self._stop.is_set():
            try:
                if self.enabled:
                    self._was_enabled = True
                    await self._sync_watchers()
                    await self._schedule_events()
                    from .sources.mcp_events import drain_calendar, poll

                    await poll(self)
                    await drain_calendar(self)
                    self.reconcile()
                    await self.responsibilities.tick()
                elif self._was_enabled or self.store.one(
                    "SELECT 1 FROM tasks WHERE status IN ('queued','running','waiting_input','needs_review') "
                    "AND json_extract(data,'$.kind') IN ('trigger','responsibility') LIMIT 1"
                ):
                    self._was_enabled = False
                    self.cancel_pending()
                    await self._close_watchers()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Trigger cycle failed")
            try:
                await asyncio.wait_for(self._stop.wait(), 1.0)
            except TimeoutError:
                pass

    async def _sync_watchers(self):
        from .sources.files import watch

        roots = {
            str(Path(rule["match"]["watch_root"]).resolve())
            for rule in self.list()
            if rule["status"] == "active" and rule["source"] == "files"
        }
        roots = set(sorted(roots)[:8])
        for root in list(self.file_watchers):
            if root not in roots or self.file_watchers[root].done():
                job = self.file_watchers.pop(root)
                if job.done() and root in roots:
                    self.watcher_retry[root] = self.clock() + 60
                    self.h.notices.add(
                        "trigger",
                        "Folder watcher needs attention",
                        root,
                        dedupe="folder-watch:" + root + ":" + str(int(self.clock() // 3600)),
                    )
                job.cancel()
                await asyncio.gather(job, return_exceptions=True)
        for root in roots - self.file_watchers.keys():
            if self.watcher_retry.get(root, 0) <= self.clock():
                self.file_watchers[root] = asyncio.create_task(watch(self, root))

    async def _schedule_events(self):
        """Resume the task event journal after restart without replaying old history."""
        from .rules import TriggerEvent

        marker = self.store.one("SELECT value FROM trigger_meta WHERE key='schedule_cursor'")
        if marker is None:
            latest = self.store.one("SELECT COALESCE(MAX(id),0) id FROM events")["id"]
            self.store.execute(
                "INSERT OR IGNORE INTO trigger_meta(key,value) VALUES ('schedule_cursor',?)", (str(latest),)
            )
            return
        rows = self.store.all(
            "SELECT id,task_id,kind,at FROM events WHERE id>? ORDER BY id LIMIT 100", (int(marker["value"]),)
        )
        for row in rows:
            if row["kind"] in {"started", "completed", "failed"}:
                try:
                    task = self.h.tasks.get(row["task_id"])
                except KeyError:
                    task = None
                if (
                    task
                    and task.get("origin") in {"user", "agent", "suggestion"}
                    and task.get("kind") in {"task", "watch"}
                    and (task.get("scheduled") or task.get("every_minutes") or task.get("kind") == "watch")
                ):
                    kind = "fired" if row["kind"] == "started" else row["kind"]
                    await self.emit(
                        TriggerEvent(
                            "schedule",
                            kind,
                            {"task_id": task["id"], "title": task["title"], "status": row["kind"]},
                            row["at"],
                            str(row["id"]),
                            False,
                        )
                    )
            self.store.execute(
                "UPDATE trigger_meta SET value=? WHERE key='schedule_cursor'", (str(row["id"]),)
            )

    @property
    def responsibilities(self):
        if not hasattr(self, "_responsibilities"):
            from .responsibilities import Responsibilities

            self._responsibilities = Responsibilities(self)
        return self._responsibilities
