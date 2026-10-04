"""The agent capability: open-ended or multi-step goals, run by the harness agent loop.

It delegates each step to the sibling adapters under the same task context, so every
existing guarantee still applies (Chrome routing, confirmations, stop, evidence).
"""

import asyncio

from .. import config
from ..panini.contracts import Fault
from .contracts import Outcome


class AgentAdapter:
    name = "agent"

    def __init__(self, harness, adapters):
        self.harness, self.adapters = harness, adapters
        self.sessions = {}  # Coordinator session -> harness conversation.
        self.running = {}

    async def availability(self):
        ready = config.configured("OPENAI_API_KEY") or bool(
            __import__("os").getenv("PANINI_CLI_SCRIPTED_MODEL")
        )
        return {
            "capability": self.name,
            "state": "ready" if ready else "unavailable",
            "reason": "" if ready else "Configure the OpenAI credential to use the PANINI agent.",
        }

    async def targets(self):
        return []

    async def select(self, choice_id):
        raise Fault("CAPABILITY_UNAVAILABLE", "The agent has no targets.", 422)

    async def run(self, context, instruction, target):
        from .. import brain
        from ..panini.targets import resolve_target

        session_key = context.session.session_id
        if session_key not in self.sessions:
            threads = getattr(self.harness, "threads", None)
            if brain.enabled() and threads is not None:
                # V2-06: the avatar conversation is a durable thread binding, so a restart continues it.
                self.sessions[session_key] = threads.bind("avatar", session_key, title=instruction[:80])
            else:
                self.sessions[session_key] = self.harness.sessions.create(instruction[:80])
        steps = []
        failed = []  # Delegations that were blocked or failed; they are not evidence of anything.

        async def delegate(capability, text):
            adapter = self.adapters.get(capability)
            if adapter is None:
                return {"outcome": "blocked", "message": f"{capability} is not installed.", "results": []}
            available = await adapter.availability()
            if available["state"] != "ready":
                failed.append(capability)
                return {"outcome": "blocked", "message": available["reason"], "results": []}
            task = context.task
            task.capability = capability
            try:
                async with asyncio.timeout(12):
                    target = await resolve_target(context, adapter, "", text)
                async with asyncio.timeout(300 if capability != "search" else 95):
                    outcome = await adapter.run(context, text, target)
            except Fault as exc:
                failed.append(capability)
                message = exc.message
                if exc.code == "TARGET_AMBIGUOUS":
                    message += " Name the application in the instruction (for example 'In Paint, draw ...') and try again."
                return {"outcome": "blocked", "message": message, "results": []}
            finally:
                task.capability = self.name
                await asyncio.wait_for(adapter.release(task.task_id), 4)
            steps.append(
                {
                    "capability": capability,
                    "outcome": outcome.outcome,
                    "message": outcome.message[:500],
                    "evidence_ids": outcome.evidence_ids[:5],
                }
            )
            return {
                "outcome": outcome.outcome,
                "message": outcome.message,
                "results": outcome.results[:5],
                "evidence_ids": outcome.evidence_ids,
            }

        def emit(kind, message, **detail):
            context.emit("running", message, agent_event=kind, **detail)

        from ..harness.agent import _browser_v2

        # V2-10: with Playwright's vocabulary the brain drives browser tools itself; no browser capability loop.
        browsers = () if _browser_v2() and self.harness.registry is not None else ("browser",)
        capabilities = [name for name in ("search", *browsers, "desktop", "code") if name in self.adapters]

        async def confirm(summary, detail, decision=None):
            try:
                if decision is None:
                    await context.confirm(summary, "PANINI agent", summary, detail)
                else:
                    level = decision.level.value
                    action = {"summary": summary, "detail": detail, "approval_level": level}
                    await context.confirm(action, "PANINI agent", summary, detail, level=level)
            except Fault:
                return False
            return True

        agent = self.harness.agent(
            delegate=delegate,
            emit=emit,
            capabilities=capabilities,
            scope=None,
            deadline_seconds=540,
            confirm=confirm,
            surface="avatar",
        )

        agent.run_id = context.task.task_id
        agent.confirm_decision = confirm
        agent.thread_id = self.sessions[session_key]
        from .. import v2flags

        if v2flags.enabled("PANINI_V2_AGUI") or v2flags.enabled("PANINI_PERSONAS"):
            agent.observe = _card_events(context)  # V2-07: the shell shows each tool call as a card.
        agent.reflex = getattr(context.task, "reflex", None)
        browser = self.adapters.get("browser")
        if browser is not None and hasattr(browser, "open_in_chrome"):
            selected = context.session.selected.get("browser")
            agent.open_url = lambda url: browser.open_in_chrome(url, selected)
            if not (selected and selected.get("mode") == "chromium"):

                async def confirm_browser(action):
                    try:
                        await context.confirm(
                            action,
                            "Chrome",
                            f"{action.get('kind', 'click')}: {action.get('label', '')}",
                            str(action.get("text", "")),
                        )
                    except Fault:
                        return False
                    return True

                agent.browser_action = lambda action, url, element, text, submit: browser.direct(
                    action, url, element, text, submit, confirm_browser
                )

        self.harness.foreground += 1
        try:
            result = await agent.run(instruction, session_id=self.sessions[session_key])
            learning = getattr(self.harness, "learning", None)
            if learning:
                learning.note_lessons(context.task.task_id, agent.used_lessons)
        finally:
            self.harness.foreground -= 1
        context.check()
        if result.outcome == "verified_complete":
            from ..harness.proactive import suggest_skill

            suggest_skill(self.harness.notices, instruction, steps)
        if result.outcome == "reply":
            # Conversation needs no evidence; unverified delegated work is only partial. A reply that
            # follows nothing but blocked attempts is not a success: the task did not get done.
            result.outcome = "partial" if steps else "blocked" if failed else "verified_complete"
        evidence = []
        if result.outcome == "verified_complete":
            evidence.append(
                context.record_evidence(
                    "agent_run",
                    "The agent completed its plan with verified steps.",
                    {
                        "plan": result.plan[:12],
                        "steps": steps[-12:],
                        "local_effects": [e for e in result.evidence if ":" in str(e)][:20],
                    },
                )
            )
        outcome = {"clarification": "blocked"}.get(result.outcome, result.outcome)
        return Outcome(outcome, result.message, [], evidence)

    async def stop(self, task_id):
        await asyncio.gather(*(a.stop(task_id) for a in self.adapters.values()), return_exceptions=True)

    async def release(self, task_id):
        pass

    async def close(self):
        pass


def _card_events(context):
    """Tool calls as task events the shell renders as cards; arguments and results are redacted views."""
    import json

    from ..ledger.redact import redact_args, redact_result

    def observe(event, **data):
        if event == "message" and data.get("persona"):
            context.emit("running", data["text"], agent_event="persona.message", persona=data["persona"])
            return
        if event == "tool.started":
            tool = {"id": data["call_id"], "name": data["name"], "args": redact_args(data.get("args") or {})}
        elif event == "tool.finished":
            output = data.get("output")
            text = output if isinstance(output, str) else json.dumps(output, ensure_ascii=False, default=str)
            tool = {"id": data["call_id"], "name": data["name"], "output": redact_result(text, 1200)}
        else:
            return
        context.emit("running", f"{data['name'].replace('_', ' ')}", agent_event=event, tool=tool)

    return observe
