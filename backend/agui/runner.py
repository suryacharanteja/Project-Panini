"""AG-UI runs: one PANINI agent run per request, streamed as AG-UI events; approvals and client tools park the
run on durable interrupts and a later request resumes it (docs/panini-enhancements/lld/V2-07-agui-adapter.md).

A conversation is a PANINI thread bound to the client's threadId (V2-06 thread_bindings). One run is active
per thread. Events fan out to every connected stream of the thread, so `connect` can re-attach.
"""

import asyncio
import json
import logging
import os
import time
from types import SimpleNamespace
from uuid import uuid4

from ..panini.contracts import Fault
from .input import check_resume, client_tools, last_user_text, untrusted_context
from .projection import Projector

logger = logging.getLogger(__name__)
DONE = object()
APPROVAL_TTL_S = 3600
CLIENT_RESULT_SCHEMA = {"type": "object", "properties": {"result": {}}, "required": ["result"]}
# Desktop and the person's own browser follow the avatar's screen lease and selected targets; console runs get
# the hands that do not share the screen (later items add sandboxed browsers and computers).
DEFAULT_CAPABILITIES = "search,code"
# Loop bookkeeping that the tool cards already show, or that only matters inside PANINI.
QUIET_ACTIVITY = {"context", "run", "step"}


def capabilities(adapters) -> tuple[str, ...]:
    wanted = os.getenv("PANINI_AGUI_CAPABILITIES", DEFAULT_CAPABILITIES)
    return tuple(c.strip() for c in wanted.split(",") if c.strip() in adapters)


class ConsoleContext:
    """The TaskContext surface v1 adapters use, for a console (AG-UI) run."""

    class _Task:
        def __init__(self, task_id):
            self.task_id, self.capability, self.dispatched = task_id, "agui", 0

    def __init__(self, ledger_run, confirm, publish, cancelled):
        self.ledger_run, self._confirm, self.publish, self.cancelled = ledger_run, confirm, publish, cancelled
        self.task = self._Task(f"agui-{uuid4().hex[:12]}")
        self.session = SimpleNamespace(session_id=ledger_run, selected={}, results={}, evidence={})
        self.evidence = {}

    def check(self):
        if self.cancelled.is_set():
            raise asyncio.CancelledError()

    def emit(self, phase, message, **_detail):
        self.check()
        self.publish("activity", kind=phase, message=message)

    def dispatch(self):
        self.check()
        self.task.dispatched += 1
        from ..ledger import mark_dispatched

        mark_dispatched()

    def code_activity(self, activity):
        self.check()

    def computer_activity(self, activity):
        self.check()

    def record_evidence(self, kind, summary, details):
        self.check()
        evidence_id = uuid4().hex
        self.evidence[evidence_id] = {"kind": kind, "summary": str(summary)[:1000], "at": time.time()}
        from ..ledger import mirror_evidence

        mirror_evidence(self.ledger_run, self.task.capability, kind, summary, details)
        return evidence_id

    async def confirm(self, action, application, effect, data):
        if not await self._confirm(f"{application}: {effect}", data):
            raise Fault("CONFIRMATION_REJECTED", "The proposed action was not approved.")
        return None


class AguiRuns:
    def __init__(self, harness):
        self.h = harness
        self.active: dict[str, asyncio.Task] = {}
        self.listeners: dict[str, list[asyncio.Queue]] = {}
        self.preapproved: dict[str, list[str]] = {}
        self.client_results: dict[str, dict] = {}
        self.cancelled: dict[str, asyncio.Event] = {}

    # Streams ---------------------------------------------------------------
    def _listen(self, thread):
        queue = asyncio.Queue()
        self.listeners.setdefault(thread, []).append(queue)
        return queue

    def _unlisten(self, thread, queue):
        queues = self.listeners.get(thread, [])
        if queue in queues:
            queues.remove(queue)

    def _broadcast(self, thread, events):
        for queue in list(self.listeners.get(thread, [])):
            for event in events:
                queue.put_nowait(event)

    def busy(self, thread) -> bool:
        task = self.active.get(thread)
        return task is not None and not task.done()

    # Run -------------------------------------------------------------------
    async def stream(self, run):
        """AG-UI events for one RunAgentInput, until the run finishes or parks."""
        thread = self.h.threads.bind("agui", run.thread_id, title=last_user_text(run)[:80] or "AG-UI")
        projector = Projector(run.thread_id, run.run_id, run.parent_run_id)
        if self.busy(thread):
            for event in projector.error("This conversation is already running.", "THREAD_BUSY"):
                yield event
            return
        try:
            plan = self._prepare(run, thread)
        except Fault as exc:
            for event in projector.started() + projector.error(exc.message, exc.code):
                yield event
            return
        queue = self._listen(thread)
        try:
            for event in projector.started():
                yield event
            if plan["kind"] == "declined":
                for event in projector.text(plan["message"]) + projector.finished(
                    "cancelled", plan["message"]
                ):
                    yield event
                return
            self.active[thread] = asyncio.create_task(self._run(thread, projector, plan))
            while True:
                event = await queue.get()
                if event is DONE:
                    break
                yield event
        finally:
            self._unlisten(thread, queue)

    def _prepare(self, run, thread):
        """A new turn, or the resume of this thread's parked run."""
        from .. import interrupts

        service = interrupts.active()
        parked = next(
            (
                r
                for r in self.h.runs.list(thread_id=thread, limit=50)
                if r["state"] == "parked" and r["mode"] != "subagent"
            ),
            None,
        )
        open_views = []
        if parked and service is not None:
            open_views = [
                v
                for v in service.list("open", limit=50)
                if v["run_id"] == parked["task_id"] and v["thread_id"] == thread
            ]
        if run.resume:
            if not parked or not open_views:
                raise Fault("INTERRUPT_UNKNOWN", "This conversation is not waiting for an answer.", 409)
            entries = check_resume(run, {v["id"] for v in open_views})
            declined = False
            for view in open_views:
                entry = entries[view["id"]]
                meta = entry.metadata or {}
                payload = entry.payload if isinstance(entry.payload, dict) else {}
                decided = service.resolve(
                    view["id"],
                    nonce=meta.get("nonce", ""),
                    action_digest=meta.get("actionDigest"),
                    status=entry.status,
                    payload=payload if entry.status == "resolved" else None,
                    decided_by=interrupts.decided_by("agui", "tap"),
                )
                approved = entry.status == "resolved" and (
                    view["kind"] != "approval" or bool(payload.get("approved"))
                )
                if not approved:
                    declined = True
                elif view["kind"] == "approval":
                    self.preapproved.setdefault(thread, []).append(
                        decided.get("action_digest") or view["action_digest"]
                    )
                elif view.get("reason") == "client_tool":
                    self.client_results.setdefault(thread, {})[view["tool_call_id"]] = payload.get("result")
            if declined:
                self.h.runs.close_parked_tree(parked["task_id"], "blocked")
                return {
                    "kind": "declined",
                    "message": "Okay, I did not do that waiting step. Earlier completed work remains recorded.",
                }
            checkpoint = self.h.runs.load_checkpoint(parked["id"]) or {}
            self.h.runs.close_parked(parked["task_id"], "resumed")
            return {
                "kind": "resume",
                "goal": checkpoint.get("goal") or "",
                "checkpoint": checkpoint,
                "ledger_run": parked["task_id"],
                "client_tools": checkpoint.get("client_tools") or client_tools(run),
            }
        text = last_user_text(run)
        if not text:
            raise Fault("EMPTY_TURN", "Send a user message to start a run.", 422)
        if parked:
            # A new message instead of an answer: the waiting step is dropped, nothing runs.
            for view in open_views:
                service.cancel(view["id"], "The person sent a new message instead.")
            self.h.runs.close_parked_tree(parked["task_id"], "blocked")
        return {
            "kind": "turn",
            "goal": text + untrusted_context(run),
            "checkpoint": None,
            "ledger_run": f"agui-{uuid4().hex}",
            "client_tools": client_tools(run),
        }

    async def _run(self, thread, projector, plan):
        from ..interrupts import Parked

        cancelled = self.cancelled.setdefault(thread, asyncio.Event())
        cancelled.clear()
        said = []

        def publish(event, **data):
            try:
                self._broadcast(thread, self._project(projector, event, data, said))
            except Exception:  # noqa: BLE001 -- A malformed event drops one event, never the run.
                logger.warning("AG-UI event projection failed", exc_info=True)

        agent = self._agent(thread, plan, publish, cancelled)
        try:
            if plan["checkpoint"] is not None:
                plan["checkpoint"]["client_tools"] = plan["client_tools"]
            result = await agent.run(plan["goal"], session_id=thread, resume=plan["checkpoint"])
            if (plan["checkpoint"] or {}).get("persona_calls"):
                self.h.runs.close_parked_tree(plan["ledger_run"], "blocked")
            events = []
            if result.message and result.message not in said:
                events += projector.text(result.message)
            events += projector.finished(result.outcome, result.message, result.evidence)
        except Parked as parked:
            from .. import interrupts

            service = interrupts.active()
            views = [service.get(parked.interrupt_id, include_secret=True)] if service else []
            events = projector.interrupted(views)
        except Fault as exc:
            events = projector.error(exc.message, exc.code)
        except asyncio.CancelledError:
            events = projector.error("The run was stopped.", "CANCELLED")
        except Exception:  # noqa: BLE001 -- The client gets a safe message; details go to the log.
            logger.exception("AG-UI run failed")
            events = projector.error("PANINI could not finish this run.")
        self._broadcast(thread, events + [DONE])

    def _project(self, projector, event, data, said):
        if event == "tool.started":
            return projector.tool_started(data["call_id"], data["name"], data.get("args"))
        if event == "tool.finished":
            return projector.tool_finished(data["call_id"], data.get("output"))
        if event == "message":
            said.append(data["text"])
            return projector.text(data["text"], persona_id=data.get("persona_id"))
        if event in {"subagent.started", "subagent.finished"}:
            return projector.activity(
                event, f"{data.get('persona_id', 'Specialist')}: {event.rsplit('.', 1)[-1]}"
            )
        if event == "plan":
            return projector.plan(data.get("plan") or [])
        return projector.activity(data.get("kind", event), data.get("message", ""))

    def _agent(self, thread, plan, publish, cancelled):
        from ..interrupts import InterruptSpec, Parked, active, digest

        ledger_run = plan["ledger_run"]

        async def confirm(summary, detail):
            action = {"summary": str(summary)[:500], "detail": str(detail)[:3000]}
            key = digest(action)
            approved = self.preapproved.get(thread, [])
            if key in approved:
                approved.remove(key)  # An approval covers exactly one action.
                return True
            service = active()
            if service is None:
                return False  # Without durable interrupts nobody can be asked from AG-UI.
            view = service.open(
                InterruptSpec(
                    "approval",
                    "tool_call",
                    str(summary)[:200],
                    f"PANINI wants to: {summary}",
                    preview={"detail": str(detail)[:3000]},
                    action=action,
                    level="tap",
                    ttl_s=APPROVAL_TTL_S,
                ),
                run_id=ledger_run,
                thread_id=thread,
                owner_principal={"surface": "agui"},
            )
            raise Parked(view["id"], str(summary)[:200])

        async def client_call(call_id, name, arguments):
            results = self.client_results.get(thread, {})
            if call_id in results:
                return results.pop(call_id)
            service = active()
            if service is None:
                return {"error": "The app's tools need durable interrupts (PANINI_V2_INTERRUPTS)."}
            view = service.open(
                InterruptSpec(
                    "question",
                    "client_tool",
                    name.removeprefix("client_"),
                    f"Run {name.removeprefix('client_')} in your app.",
                    preview={"arguments": _loads(arguments)},
                    response_schema=CLIENT_RESULT_SCHEMA,
                    level="tap",
                    ttl_s=APPROVAL_TTL_S,
                    tool_call_id=call_id,
                ),
                run_id=ledger_run,
                thread_id=thread,
                owner_principal={"surface": "agui"},
            )
            raise Parked(view["id"], name)

        context = ConsoleContext(ledger_run, confirm, publish, cancelled)
        adapters = self.h.adapters
        chosen = capabilities(adapters)

        async def delegate(capability, instruction):
            adapter = adapters.get(capability)
            if adapter is None or capability not in chosen:
                return {
                    "outcome": "blocked",
                    "message": f"{capability} is not available here.",
                    "results": [],
                }
            ready = await adapter.availability()
            if ready["state"] != "ready":
                return {"outcome": "blocked", "message": ready["reason"], "results": []}
            context.task.capability = capability
            try:
                outcome = await adapter.run(context, instruction, None)
            except Fault as exc:
                return {"outcome": "blocked", "message": exc.message, "results": []}
            finally:
                await adapter.release(context.task.task_id)
            return {
                "outcome": outcome.outcome,
                "message": outcome.message,
                "results": outcome.results[:5],
                "evidence_ids": outcome.evidence_ids,
            }

        def emit(kind, message, **detail):
            if kind == "plan":
                publish("plan", plan=detail.get("plan"))
            elif kind not in QUIET_ACTIVITY:
                publish("activity", kind=kind, message=message)

        agent = self.h.agent(
            delegate=delegate,
            emit=emit,
            capabilities=chosen,
            confirm=confirm,
            deadline_seconds=540,
            surface="agui",
        )
        agent.run_id, agent.thread_id = ledger_run, thread
        agent.observe = publish
        agent.durable = True  # Run records and checkpoints, so a parked run resumes in place.
        agent.client_tools = plan["client_tools"]
        agent.client_call = client_call
        return agent

    async def stop(self, thread_external_id: str) -> bool:
        thread = self.h.threads.bind("agui", thread_external_id)
        if not self.busy(thread):
            return False
        self.cancelled.setdefault(thread, asyncio.Event()).set()
        self.active[thread].cancel()
        return True

    # Connect -----------------------------------------------------------------
    async def connect(self, thread_external_id: str, run_id: str | None = None):
        """MESSAGES_SNAPSHOT and STATE_SNAPSHOT of a thread, then live events of its active run."""
        from ag_ui.core import AssistantMessage, MessagesSnapshotEvent, StateSnapshotEvent, UserMessage

        from .. import interrupts

        thread = self.h.threads.bind("agui", thread_external_id)
        session = self.h.sessions.get(thread)
        messages = []
        for item in session["items"]:
            role, content = item.get("role"), item.get("content")
            if role not in ("user", "assistant"):
                continue
            if isinstance(content, list):
                content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
            if not isinstance(content, str) or not content:
                continue
            cls = UserMessage if role == "user" else AssistantMessage
            messages.append(cls(id=uuid4().hex, content=content[:8000]))
        latest = next(iter(self.h.runs.list(thread_id=thread, limit=1)), None)
        plan = (self.h.runs.load_checkpoint(latest["id"]) or {}).get("plan", []) if latest else []
        service = interrupts.active()
        waiting = []
        if latest and latest["state"] == "parked" and service is not None:
            waiting = [
                {"id": v["id"], "kind": v["kind"], "title": v["title"], "message": v["message"]}
                for v in service.list("open", limit=50)
                if v["thread_id"] == thread and v["run_id"] == latest["task_id"]
            ]
        yield MessagesSnapshotEvent(messages=messages[-100:])
        yield StateSnapshotEvent(snapshot={"plan": plan, "interrupts": waiting, "running": self.busy(thread)})
        if not self.busy(thread):
            return
        queue = self._listen(thread)
        try:
            while True:
                event = await queue.get()
                if event is DONE:
                    return
                yield event
        finally:
            self._unlisten(thread, queue)


def _loads(arguments):
    try:
        return json.loads(arguments or "{}")
    except (TypeError, ValueError):
        return {}
