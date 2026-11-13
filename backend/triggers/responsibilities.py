"""Reviewed, attributed responsibilities with bounded checks and durable page reports."""

import html
import json
import math
from datetime import UTC, datetime
from uuid import uuid4

from ..interrupts import InterruptSpec, digest
from .rules import SLUG, _text

TEMPLATES = {
    "downloads": {"title": "Keep Downloads tidy", "source": "files", "cadence_minutes": 1440},
    "invoices": {"title": "File invoices", "source": "files and email", "cadence_minutes": 1440},
    "flight": {"title": "Watch my flight", "source": "email and web", "cadence_minutes": 60},
    "meeting": {"title": "Prepare for meetings", "source": "calendar", "cadence_minutes": 60},
}
DEFAULT_DAILY_BUDGET_USD = 1.00
TERMINAL = {"completed", "failed", "cancelled", "needs_review", "waiting_input"}


def validate(value):
    if not isinstance(value, dict) or set(value) != {
        "title",
        "persona_id",
        "charter",
        "success_criteria",
        "cadence_minutes",
        "trigger_ids",
        "report",
        "escalation",
        "space_id",
        "budget_usd",
        "daily_budget_usd",
    }:
        raise ValueError("A responsibility needs a charter, success criteria, triggers and report settings.")
    _text(value["title"], 120, "Title")
    _text(value["charter"], 4000, "Charter")
    _text(value["success_criteria"], 2000, "Success criteria")
    _text(value["escalation"], 1000, "Escalation")
    if not isinstance(value["persona_id"], str) or not SLUG.fullmatch(value["persona_id"]):
        raise ValueError("Choose a named persona.")
    if value["cadence_minutes"] is not None and (
        type(value["cadence_minutes"]) is not int or not 5 <= value["cadence_minutes"] <= 10080
    ):
        raise ValueError("Cadence must be 5 minutes to 7 days.")
    if not isinstance(value["report"], str) or value["report"] not in {"daily", "weekly"}:
        raise ValueError("Report cadence must be daily or weekly.")
    if (
        type(value["budget_usd"]) not in (float, int)
        or not math.isfinite(value["budget_usd"])
        or not 0 < value["budget_usd"] <= 0.20
    ):
        raise ValueError("Each check needs a budget from 0 to 0.20 USD.")
    if (
        type(value["daily_budget_usd"]) not in (float, int)
        or not math.isfinite(value["daily_budget_usd"])
        or not value["budget_usd"] <= value["daily_budget_usd"] <= 20
    ):
        raise ValueError("Daily responsibility budget must cover one check and stay under 20 USD.")
    ids = value["trigger_ids"]
    if (
        not isinstance(ids, list)
        or len(ids) > 20
        or any(not isinstance(item, str) or len(item) != 32 for item in ids)
        or len(set(ids)) != len(ids)
    ):
        raise ValueError("Choose up to twenty distinct trigger IDs.")
    if value["cadence_minutes"] is None and not ids:
        raise ValueError("Add a periodic check or at least one trigger.")
    if value["space_id"] is not None and (
        not isinstance(value["space_id"], str) or len(value["space_id"]) != 32
    ):
        raise ValueError("Choose an existing Space or leave it blank for a new one.")
    return value


class Responsibilities:
    def __init__(self, bus):
        self.bus, self.h, self.store, self.clock = bus, bus.h, bus.store, bus.clock

    def get(self, identifier):
        row = self.store.one("SELECT * FROM responsibilities WHERE id=?", (identifier,))
        if not row:
            raise KeyError("Unknown responsibility.")
        return {**row, **json.loads(row["data"]), "health": json.loads(row["health"])}

    def list(self):
        return [
            self.get(row["id"])
            for row in self.store.all("SELECT id FROM responsibilities ORDER BY created DESC")
        ]

    def owner_of(self, rule_id):
        for row in self.store.all("SELECT id,data FROM responsibilities WHERE status='active'"):
            if rule_id in json.loads(row["data"])["trigger_ids"]:
                return row["id"]
        return None

    def can_spend(self, identifier, cost, *, run_at=None):
        """Reserve at most the reviewed maximum for every queued check in this UTC day."""
        current = self.get(identifier)
        day = int((run_at if run_at is not None else self.clock()) // 86400) * 86400
        periodic = (
            self.store.one(
                "SELECT COUNT(*) n FROM responsibility_runs WHERE responsibility_id=? AND created>=? AND created<?",
                (identifier, day, day + 86400),
            )["n"]
            * current["budget_usd"]
        )
        triggered = self.store.one(
            "SELECT COALESCE(SUM(CAST(json_extract(t.data,'$.action.budget_usd') AS REAL)),0) n "
            "FROM trigger_firings f JOIN triggers t ON t.id=f.rule_id "
            "WHERE f.responsibility_id=? AND f.due>=? AND f.due<?",
            (identifier, day, day + 86400),
        )["n"]
        return periodic + triggered + cost <= current.get("daily_budget_usd", DEFAULT_DAILY_BUDGET_USD) + 1e-9

    def propose(self, data):
        if isinstance(data, dict):
            data = {**data, "daily_budget_usd": data.get("daily_budget_usd", DEFAULT_DAILY_BUDGET_USD)}
        validate(data)
        self.h.personas.get(data["persona_id"])
        from .. import v2flags

        if data["persona_id"] != "panini" and not v2flags.enabled("PANINI_PERSONAS"):
            raise ValueError("Enable personas before assigning a specialist.")
        if data["space_id"]:
            self.h.spaces.authorize(data["space_id"], data["persona_id"])
        for rule_id in data["trigger_ids"]:
            rule = self.bus.get(rule_id)
            if rule["persona"] != data["persona_id"]:
                raise ValueError("A responsibility's rules must use its persona.")
        identifier, now = uuid4().hex, self.clock()
        with self.bus.transaction():
            self.store.execute(
                "INSERT INTO responsibilities(id,data,status,created,updated) VALUES (?,?,'proposed',?,?)",
                (identifier, json.dumps(data, ensure_ascii=False), now, now),
            )
            action = {"type": "activate_responsibility", "id": identifier, "digest": digest(data)}
            view = self.h.interrupts.open(
                InterruptSpec(
                    "review",
                    "responsibility_activation",
                    "Take on responsibility: " + data["title"],
                    "Review its charter, limits, triggers, Space reports and escalation.",
                    preview=data,
                    action=action,
                    level="verify",
                    ttl_s=7 * 86400,
                ),
                run_id="responsibility-proposal:" + identifier,
                task_id="responsibility-proposal:" + identifier,
                owner_principal={"surface": "responsibility_proposal"},
            )
            self.store.execute("UPDATE responsibilities SET review_id=? WHERE id=?", (view["id"], identifier))
        return self.get(identifier)

    def review_decided(self, view, action, approved):
        row = self.store.one("SELECT * FROM responsibilities WHERE id=?", (action.get("id"),))
        if not row or row["status"] != "proposed" or row["review_id"] != view["id"]:
            return
        data = json.loads(row["data"])
        if digest(data) != action.get("digest"):
            return
        if approved:
            try:
                self.h.personas.get(data["persona_id"])
                if data["space_id"]:
                    self.h.spaces.authorize(data["space_id"], data["persona_id"])
                for rule_id in data["trigger_ids"]:
                    rule = self.bus.get(rule_id)
                    if (
                        rule["status"] != "active"
                        or rule["persona"] != data["persona_id"]
                        or self.owner_of(rule_id)
                    ):
                        raise ValueError("Review active, unassigned rules before taking this on.")
            except (KeyError, ValueError):
                self.store.execute(
                    "UPDATE responsibilities SET status='paused',updated=? WHERE id=?",
                    (self.clock(), row["id"]),
                )
                self.h.notices.add(
                    "responsibility",
                    "Responsibility needs setup",
                    data["title"],
                    dedupe="responsibility-setup:" + row["id"],
                )
                return
        now = self.clock()
        period = 86400 if data["report"] == "daily" else 7 * 86400
        self.store.execute(
            "UPDATE responsibilities SET status=?,next_check=?,next_report=?,updated=? WHERE id=? AND status='proposed'",
            (
                "active" if approved else "paused",
                now + data["cadence_minutes"] * 60 if approved and data["cadence_minutes"] else None,
                now + period if approved else None,
                now,
                row["id"],
            ),
        )

    def pause(self, identifier, *, reason="Paused by the owner."):
        row = self.get(identifier)
        if row["status"] == "retired":
            return row
        self.store.execute(
            "UPDATE responsibilities SET status='paused',updated=? WHERE id=?", (self.clock(), identifier)
        )
        for task in self.store.all(
            "SELECT r.task_id FROM responsibility_runs r JOIN tasks t ON t.id=r.task_id "
            "WHERE r.responsibility_id=? AND t.status IN ('queued','running','waiting_input','needs_review')",
            (identifier,),
        ):
            self.h.tasks.cancel(task["task_id"])
        for firing in self.store.all(
            "SELECT f.task_id FROM trigger_firings f JOIN tasks t ON t.id=f.task_id "
            "WHERE f.responsibility_id=? AND t.status IN ('queued','running','waiting_input','needs_review')",
            (identifier,),
        ):
            self.h.tasks.cancel(firing["task_id"])
        health = row["health"]
        health["paused_reason"] = reason[:300]
        self.store.execute(
            "UPDATE responsibilities SET health=? WHERE id=?", (json.dumps(health), identifier)
        )
        return self.get(identifier)

    def resume(self, identifier):
        row = self.get(identifier)
        if row["status"] != "paused":
            raise ValueError("Only a paused responsibility can resume.")
        if not row["review_id"]:
            raise ValueError("The owner must review this responsibility first.")
        review = self.h.interrupts.get(row["review_id"])
        if (
            review["status"] != "resolved"
            or (review.get("decision") or {}).get("payload", {}).get("approved") is not True
        ):
            raise ValueError("The owner must approve this responsibility first.")
        self.h.personas.get(row["persona_id"])
        if row["space_id"]:
            self.h.spaces.authorize(row["space_id"], row["persona_id"])
        for rule_id in row["trigger_ids"]:
            rule = self.bus.get(rule_id)
            if rule["status"] != "active" or self.owner_of(rule_id):
                raise ValueError("Restore its active, unassigned trigger rules first.")
        now = self.clock()
        self.store.execute(
            "UPDATE responsibilities SET status='active',next_check=?,next_report=?,updated=? WHERE id=?",
            (
                now + row["cadence_minutes"] * 60 if row["cadence_minutes"] else None,
                now + (86400 if row["report"] == "daily" else 7 * 86400),
                now,
                identifier,
            ),
        )
        return self.get(identifier)

    def retire(self, identifier):
        self.pause(identifier)
        self.store.execute(
            "UPDATE responsibilities SET status='retired',updated=? WHERE id=?", (self.clock(), identifier)
        )
        return self.get(identifier)

    def observe(self, identifier, task_id, status):
        if status not in TERMINAL:
            return
        row = self.get(identifier)
        health = row["health"]
        health["last_check"] = self.clock()
        health["last_task"] = task_id
        health["last_status"] = status
        if status == "completed":
            health["successes"] = health.get("successes", 0) + 1
            health["consecutive_failures"] = 0
        elif status == "failed":
            health["failures"] = health.get("failures", 0) + 1
            health["consecutive_failures"] = health.get("consecutive_failures", 0) + 1
            if row["status"] == "active" and row["cadence_minutes"]:
                backoff = row["cadence_minutes"] * 60 * 2 ** min(health["consecutive_failures"], 4)
                self.store.execute(
                    "UPDATE responsibilities SET next_check=? WHERE id=?",
                    (max(row["next_check"] or 0, self.clock() + backoff), identifier),
                )
        elif status in {"waiting_input", "needs_review"}:
            health["interventions"] = health.get("interventions", 0) + 1
        self.store.execute(
            "UPDATE responsibilities SET health=?,updated=? WHERE id=?",
            (json.dumps(health), self.clock(), identifier),
        )
        if health.get("consecutive_failures", 0) >= 5 and row["status"] == "active":
            self.pause(identifier, reason="Five consecutive checks failed.")
            self.h.notices.add(
                "responsibility",
                "Responsibility paused after failures",
                row["title"],
                dedupe="responsibility-paused:" + identifier + ":" + task_id,
                urgent=True,
            )

    async def tick(self):
        # A task outcome is processed once, including after a process restart.
        for row in self.store.all(
            "SELECT r.task_id,r.responsibility_id,r.status,t.status task_status FROM responsibility_runs r "
            "JOIN tasks t ON t.id=r.task_id WHERE r.status<>t.status"
        ):
            self.store.execute(
                "UPDATE responsibility_runs SET status=? WHERE task_id=?",
                (row["task_status"], row["task_id"]),
            )
            self.observe(row["responsibility_id"], row["task_id"], row["task_status"])
        for row in self.list():
            if row["status"] != "active":
                continue
            try:
                if row["next_check"] and row["next_check"] <= self.clock():
                    self._check(row)
                if row["next_report"] and row["next_report"] <= self.clock():
                    self._report(row)
            except Exception:  # noqa: BLE001 -- keep other responsibilities running.
                import logging

                logging.getLogger(__name__).exception("Responsibility cycle failed: %s", row["id"])
                # Retry later while preserving the rest of the scheduler's progress.
                if row["next_check"] and row["next_check"] <= self.clock():
                    self.store.execute(
                        "UPDATE responsibilities SET next_check=? WHERE id=?", (self.clock() + 300, row["id"])
                    )
                if row["next_report"] and row["next_report"] <= self.clock():
                    self.store.execute(
                        "UPDATE responsibilities SET next_report=? WHERE id=?",
                        (self.clock() + 300, row["id"]),
                    )
                self.h.notices.add(
                    "responsibility",
                    "Responsibility needs attention",
                    row["title"],
                    dedupe="responsibility-error:" + row["id"] + ":" + str(int(self.clock() // 3600)),
                )

    def _check(self, row):
        now = self.clock()
        with self.bus.transaction():
            current = self.get(row["id"])
            if current["status"] != "active" or not current["next_check"] or current["next_check"] > now:
                return
            pending = self.store.one(
                "SELECT COUNT(*) n FROM responsibility_runs r JOIN tasks t ON t.id=r.task_id "
                "WHERE r.responsibility_id=? AND t.status IN ('queued','running','waiting_input','needs_review')",
                (row["id"],),
            )["n"]
            self.store.execute(
                "UPDATE responsibilities SET next_check=?,updated=? WHERE id=?",
                (now + row["cadence_minutes"] * 60, now, row["id"]),
            )
            if pending or not self.can_spend(row["id"], row["budget_usd"]):
                health = current["health"]
                key = "misses" if pending else "budget_skips"
                health[key] = health.get(key, 0) + 1
                self.store.execute(
                    "UPDATE responsibilities SET health=? WHERE id=?", (json.dumps(health), row["id"])
                )
                return
            task = self.h.tasks.submit(
                f"Reviewed responsibility: {row['charter']}\nSuccess criteria: {row['success_criteria']}\n"
                "Run one bounded read-only check; report what you verified and what needs the owner.",
                title=row["title"],
                origin="responsibility:" + row["id"],
                kind="responsibility",
                persona_id=row["persona_id"],
                budget_usd=row["budget_usd"],
            )
            self.store.execute(
                "INSERT INTO responsibility_runs(task_id,responsibility_id,created) VALUES (?,?,?)",
                (task["id"], row["id"], now),
            )

    def _report(self, row):
        now = self.clock()
        with self.bus.transaction():
            current = self.get(row["id"])
            if current["status"] != "active" or not current["next_report"] or current["next_report"] > now:
                return
            period = 86400 if current["report"] == "daily" else 7 * 86400
            space_id = current["space_id"]
            if not space_id:
                persona = self.h.personas.get(current["persona_id"])
                space_id = persona.get("default_space_id")
            if not space_id:
                space = self.h.spaces.create_space(
                    "PANINI Responsibilities", "Reviewed responsibility reports"
                )
                space_id = space["id"]
                if current["persona_id"] != "panini":
                    self.h.spaces.grant(space_id, current["persona_id"], True)
                data = {
                    key: current[key]
                    for key in (
                        "title",
                        "persona_id",
                        "charter",
                        "success_criteria",
                        "cadence_minutes",
                        "trigger_ids",
                        "report",
                        "escalation",
                        "space_id",
                        "budget_usd",
                        "daily_budget_usd",
                    )
                }
                data["space_id"] = space_id
                self.store.execute(
                    "UPDATE responsibilities SET data=? WHERE id=?",
                    (json.dumps(data, ensure_ascii=False), row["id"]),
                )
            self.h.spaces.authorize(space_id, current["persona_id"])
            start = current["next_report"] - period
            recent = self.store.all(
                "SELECT t.data,t.status FROM responsibility_runs r JOIN tasks t ON t.id=r.task_id "
                "WHERE r.responsibility_id=? AND r.created>=? ORDER BY r.created DESC LIMIT 20",
                (row["id"], start),
            )
            recent += self.store.all(
                "SELECT t.data,t.status FROM trigger_firings r JOIN tasks t ON t.id=r.task_id "
                "WHERE r.responsibility_id=? AND r.created>=? ORDER BY r.created DESC LIMIT 20",
                (row["id"], start),
            )
            findings = []
            for item in recent[:20]:
                task = json.loads(item["data"])
                message = str((task.get("result") or {}).get("message", "Pending"))[:500]
                findings.append(f"- {html.escape(item['status'])}: {html.escape(message)}")
            health = current["health"]
            stamp = datetime.fromtimestamp(now, UTC).strftime("%Y-%m-%d")
            text = (
                f"# {current['title']} — {stamp}\n\n"
                f"Charter: {html.escape(current['charter'])}\n\n"
                f"Success criteria: {html.escape(current['success_criteria'])}\n\n"
                f"Checks: {len(recent)} · Failures: {health.get('failures', 0)} · "
                f"Missed checks: {health.get('misses', 0)} · Interventions: {health.get('interventions', 0)}\n\n"
                "## What happened\n"
                + ("\n".join(findings) if findings else "No check completed in this period.")
                + "\n\n## What needs you\n"
                + html.escape(current["escalation"])
            )
            page = self.h.spaces.create(
                space_id, f"{current['title']} — {stamp}", text, persona_id=current["persona_id"]
            )
            self.store.execute(
                "UPDATE responsibilities SET report_page_id=?,next_report=?,updated=? WHERE id=?",
                (page["id"], now + period, now, row["id"]),
            )
        self.h.notices.add(
            "responsibility",
            "Responsibility report ready: " + row["title"],
            "A reviewed report is in your Space.",
            action={"type": "page", "page_id": page["id"]},
            dedupe="responsibility-report:" + row["id"] + ":" + stamp,
        )
