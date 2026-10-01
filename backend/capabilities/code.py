"""A workspace-bound adapter. Only PANINI's active controller can approve execution."""

import hashlib
import json
import os
import sys
import time
from pathlib import Path
from uuid import uuid4

from .. import config
from ..panini.code_activity import CodeActivity
from ..panini.contracts import Fault
from .approvals import code_action_needs_approval
from .contracts import Outcome, UnavailableAdapter

# Source checkouts and PyInstaller both package the same standalone module.
_source = config.ROOT / "panini-core/capabilities/cli/src"
if _source.is_dir() and str(_source) not in sys.path:
    sys.path.insert(0, str(_source))


class CodeAdapter(UnavailableAdapter):
    def __init__(self, enabled=False):
        super().__init__("code", "Turn on PANINI-CLI and add a workspace in PANINI Admin → Code.")
        self.enabled, self.engines, self.choices = enabled, {}, {}
        self.workspace_error = False
        if enabled:
            try:
                paths = json.loads(
                    os.getenv("PANINI_CLI_WORKSPACES")
                    or os.getenv("PANINI_CLI_WORKSPACES")
                    or os.getenv("PANINI_CLI_WORKSPACES", "[]")
                )
                if not isinstance(paths, list) or len(paths) > 30:
                    raise ValueError("Expected up to 30 workspace paths")
                for value in paths:
                    path = Path(value).expanduser().resolve(strict=True)
                    if not path.is_dir() or path == Path(path.anchor):
                        raise ValueError("Select a project directory, not a volume")
                    identifier = str(uuid4())
                    self.choices[identifier] = {
                        "choice_id": identifier,
                        "target_ref": identifier,
                        "label": str(path),
                        "path": str(path),
                    }
            except (ValueError, OSError, TypeError):
                self.choices.clear()
                self.workspace_error = True

    async def availability(self):
        ready = False
        reason = "PANINI-CLI is off. Turn it on in PANINI Admin → Code, then restart PANINI."
        if self.enabled:
            reason = (
                "The CLI workspace configuration is invalid. Select existing project directories."
                if self.workspace_error
                else "Configure at least one approved CLI workspace."
            )
        if self.enabled and self.choices:
            try:
                from panini_cli.engine import external_runtime

                # PANINI-CLI needs the OpenAI key; a configured external runtime brings its own access.
                ready = bool(external_runtime()) or config.configured("OPENAI_API_KEY")
                reason = "Configure OPENAI_API_KEY for PANINI-CLI."
            except (ImportError, OSError, ValueError):
                reason = (
                    "PANINI_CLI_EXECUTABLE does not name a usable PANINI-CLI runtime. Fix it or remove it."
                )
        return {
            "capability": self.name,
            "state": "ready" if ready else "unavailable",
            "reason": "" if ready else reason,
        }

    async def targets(self):
        return [{k: row[k] for k in ("choice_id", "label")} for row in self.choices.values()]

    async def select(self, choice_id):
        if not self.enabled or choice_id not in self.choices:
            raise Fault("CODE_WORKSPACE_UNKNOWN", "Choose a configured CLI workspace.")
        return dict(self.choices[choice_id])

    async def run(self, context, instruction, target):
        from panini_cli.engine import CodeEngine

        context.check()
        expected = self.choices.get((target or {}).get("choice_id"))
        if (
            not self.enabled
            or not expected
            or any(target.get(key) != expected[key] for key in ("choice_id", "label", "path"))
            or Path(expected["path"]).resolve() != Path(expected["path"])
        ):
            raise Fault("CODE_WORKSPACE_UNKNOWN", "The CLI workspace changed. Select it again.")
        data = Path(
            os.getenv("PANINI_CLI_DATA")
            or os.getenv("PANINI_CLI_DATA")
            or os.getenv("PANINI_CLI_DATA")
            or Path.home() / ".panini-cli"
        )
        engine = CodeEngine(expected["path"], data / "events.sqlite3")
        self.engines[context.task.task_id] = engine
        evidence = []
        model = (
            os.getenv("PANINI_CLI_MODEL")
            or os.getenv("JEV_REV_CODE_MODEL")
            or os.getenv("PANINI_AGENT_MODEL")
            or os.getenv("PANINI_PLANNER_MODEL")
            or os.getenv("EXTENSION_PLANNER_MODEL")
            or "gpt-4.1"
        )
        activity = CodeActivity(expected["label"], model)
        last_activity = 0

        async def confirm(review):
            context.check()
            if not code_action_needs_approval(review):
                return True
            body = json.dumps(review, ensure_ascii=False, indent=2)
            # The confirmation card has a 4 KB review limit. Never approve a hidden/truncated effect.
            if len(body) > 3900:
                context.emit(
                    "blocked",
                    "This PANINI-CLI action is too large to review in PANINI. Use the native CLI interface.",
                )
                return False
            await context.confirm(review, expected["label"], "Apply the displayed coding action once.", body)
            context.check()
            return True

        async def emit(record):
            nonlocal last_activity
            context.check()
            view = activity.update(record)
            # Keep token/output bursts from saturating controller and observer queues.
            if record.get("type") != "item.delta" or time.monotonic() - last_activity >= 0.25:
                context.code_activity(view)
                last_activity = time.monotonic()
            if record.get("receipt") or record.get("paths"):
                compact = {
                    "item_id": record.get("item_id"),
                    "kind": record.get("kind"),
                    "status": record.get("status"),
                    "receipt_sha256": hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest(),
                }
                if receipt := record.get("receipt"):
                    compact["receipt"] = {
                        "exit_code": receipt.get("exit_code"),
                        "command": str(receipt.get("command", ""))[:1000],
                        "output": str(receipt.get("output", ""))[-1000:],
                    }
                if record.get("paths"):
                    compact["paths"] = [str(p)[:250] for p in record["paths"][:8]]
                evidence.append(
                    context.record_evidence(
                        "coding_execution_receipt",
                        "Recorded a coding action; task completion still needs verification.",
                        compact,
                    )
                )
            elif record.get("type") not in {"item.delta", "item.started"}:
                context.emit("running", "The CLI agent is working in " + expected["label"])

        context.emit("running", "Starting coding work in " + expected["label"])
        context.code_activity(activity.value)
        context.dispatch()
        result = await engine.run(
            context.task.task_id,
            instruction,
            confirm=confirm,
            emit=emit,
            check=context.check,
            model=model,
            reasoning_effort=os.getenv("PANINI_CLI_REASONING_EFFORT")
            or os.getenv("PANINI_CLI_REASONING_EFFORT", "low"),
        )
        context.check()
        message = result.message
        if result.status == "partial":
            message = "Coding turn finished; independent task verification is pending.\n" + message
        return Outcome(result.status, message, evidence_ids=evidence[-30:])

    async def stop(self, task_id):
        if engine := self.engines.get(task_id):
            await engine.stop()

    async def release(self, task_id):
        if engine := self.engines.pop(task_id, None):
            await engine.close()

    async def close(self):
        for task_id in list(self.engines):
            await self.release(task_id)
