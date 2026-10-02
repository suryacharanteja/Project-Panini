"""Engines run one case each in an isolated PANINI data folder and return a TurnResult.

V1CoordinatorEngine drives the real coordinator exactly as the shell does (session, lease, renewals) and
decides confirmations automatically per case. Later items add engines (V2-06: the run engine) so the same
cases compare implementations.
"""

import asyncio
import json
import os
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4


@dataclass
class TurnResult:
    outcome: str = "error"
    message: str = ""
    seconds: float = 0.0
    tokens: int = 0
    cost: float = 0.0
    approvals_requested: int = 0
    declined: list = field(default_factory=list)
    evidence_count: int = 0
    task_status: str = ""
    route: str | None = None
    error: str | None = None


class _Environment:
    """Per-case data folder, working folder and environment overrides, restored afterwards."""

    def __init__(self, case, mode):
        self.case, self.mode = case, mode
        self.root = Path(tempfile.mkdtemp(prefix="panini-eval-"))
        self.work = self.root / "work"
        self.work.mkdir()
        self.saved = {}

    def __enter__(self):
        from .cases import expand

        for name, content in self.case.files.items():
            path = self.work / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(expand(content, self.work), encoding="utf-8")
        overrides = {
            "PANINI_DATA_DIR": str(self.root / "data"),
            "PANINI_JEV_GATES": "false" if self.mode == "offline" else os.getenv("PANINI_JEV_GATES", "true"),
            "JEV_REV_DESKTOP_ENABLED": "0",
            "PANINI_CLI_ENABLED": "0",
            "PANINI_CAPABILITY_ROUTER_ENABLED": "0",
        }
        if self.mode == "offline":
            script = self.root / "script.json"
            script.write_text(json.dumps(expand(list(self.case.script), self.work)), encoding="utf-8")
            # No provider can be reached: the agent replays the case's scripted model.
            overrides |= {
                "PANINI_CLI_SCRIPTED_MODEL": str(script),
                "OPENAI_API_KEY": "",
                "AI_GATEWAY_API_KEY": "",
            }
        for key, value in overrides.items():
            self.saved[key] = os.environ.get(key)
            os.environ[key] = value
        return self

    def __exit__(self, *exc):
        for key, value in self.saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class V1CoordinatorEngine:
    name = "v1"

    async def run(self, case, mode, *, timeout=240.0) -> tuple[TurnResult, Path]:
        env = _Environment(case, mode)
        with env:
            result = await self._run(case, env, timeout)
        return result, env.work

    async def _run(self, case, env, timeout):
        from backend import usage_log
        from backend.app import create_app
        from backend.panini.settings import Settings

        from .cases import expand

        app = create_app(panini_settings=Settings(enabled=True, bootstrap_token="e" * 40))
        coordinator, harness = app.state.panini, app.state.harness
        result = TurnResult()
        before = usage_log.summary()["total"]
        started = time.monotonic()
        text = expand(case.turn, env.work)
        try:
            if case.kind == "background":
                harness.start = lambda: None
                task = harness.tasks.submit(text, origin="eval")
                async with asyncio.timeout(timeout):
                    await harness.worker.run_once()
                record = harness.tasks.get(task["id"])
                result.task_status = record["status"]
                result.outcome = (record.get("result") or {}).get("outcome") or record["status"]
                result.message = (record.get("result") or {}).get("message") or ""
            else:
                await self._turn(coordinator, case, text, timeout, result)
        except Exception as exc:  # noqa: BLE001 -- An eval records every failure and continues.
            result.error = f"{type(exc).__name__}: {exc}"[:300]
        finally:
            result.seconds = round(time.monotonic() - started, 2)
            after = usage_log.summary()["total"]
            result.tokens = after["input_tokens"] - before["input_tokens"]
            result.cost = round(after["cost"] - before["cost"], 5)
            # Learning runs after each task in the background; let it finish before the store closes.
            jobs = list(getattr(coordinator, "_learning_jobs", ()))
            if jobs:
                await asyncio.wait(jobs, timeout=30)
            await coordinator.close()
            if harness:
                await harness.close()
        return result

    async def _turn(self, coordinator, case, text, timeout, result):
        from backend.panini.contracts import Turn

        _, principal = coordinator.auth.issue("controller")
        session = coordinator.get((await coordinator.create_session(principal, uuid4()))["session_id"])
        epoch = (await coordinator.acquire(session, principal))["lease_epoch"]

        async def decide():
            revision = 0
            while True:
                await asyncio.sleep(0.2)
                revision += 1
                coordinator.renew(
                    session, principal, session.lease_epoch, revision
                )  # As the open shell does.
                for item in list(session.confirmations.values()):
                    future = item["future"]
                    if future.done() or item.get("eval_seen"):
                        continue
                    item["eval_seen"] = True
                    result.approvals_requested += 1
                    result.declined.append(item["effect"][:120])
                    if case.approvals == "approve":
                        future.set_result(True)
                    elif case.approvals == "decline":
                        future.set_result(False)
                    # "timeout": leave it; the confirmation expires on its own.

        watcher = asyncio.create_task(decide())
        try:
            await coordinator.submit(session, principal, epoch, Turn(command_id=uuid4(), text=text))
            if session.active:
                await asyncio.wait_for(session.active.runner, timeout)
            task = session.last_task
            if task:
                result.outcome, result.message = task.outcome or task.state, task.message
                result.evidence_count = len(task.evidence_ids)
                result.task_status = task.state
                result.route = task.steps[0]["capability"] if task.steps else task.capability
        finally:
            watcher.cancel()
            await coordinator.end(session)


ENGINES = {"v1": V1CoordinatorEngine}
