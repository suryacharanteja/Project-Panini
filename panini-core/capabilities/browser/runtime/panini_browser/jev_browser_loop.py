"""One owned browser session with cancellable, streamed Jev-driven runs."""

import asyncio
import logging
import time
from collections import deque

from browser_use import Agent, Browser
from browser_use.tools.service import Tools

from .browser_automation import compress_screenshot
from .chat_jev import ChatJev

logger = logging.getLogger(__name__)


class JevBrowserLoop:
    def __init__(self, headless=False):
        self.headless = headless
        self.browser = None
        self.tools = Tools()
        self.loop_active = False
        self.last_error = None
        self.instruction_history = deque(maxlen=100)
        self._runner = None
        self._cleanup = None
        self._events = None
        self._cancel_requested = False

    async def startup(self):
        if self.browser is not None:
            return
        browser = Browser(headless=self.headless, keep_alive=True, enable_default_extensions=False)
        try:
            await browser.start()
        except BaseException:
            await browser.kill()
            raise
        self.browser = browser
        self.last_error = None

    def cancel(self):
        if self._runner and not self._runner.done() and not self._cancel_requested:
            if not self._cancel_requested and self._events is not None:
                self._cancel_requested = True
                # Cancellation can precede the task's first scheduled instruction.
                # Make room for a terminal event even if a slow client fills the queue.
                if self._events.full():
                    self._events.get_nowait()
                self._events.put_nowait({"event": "cancelled", "message": "Instruction stopped"})
            self._runner.cancel()

    async def shutdown(self):
        self.cancel()
        if self._runner:
            await asyncio.gather(self._runner, return_exceptions=True)
        if self._cleanup:
            await asyncio.shield(self._cleanup)
        if self.browser:
            try:
                await self.browser.kill()
            finally:
                self.browser = None

    async def _snapshot(self):
        if self.browser is None:
            return ""
        try:
            raw = await asyncio.wait_for(self.browser.take_screenshot(), timeout=5)
            return await asyncio.to_thread(compress_screenshot, raw) if isinstance(raw, bytes) else raw or ""
        except Exception as exc:
            logger.debug("Screenshot unavailable: %s", exc)
            return ""

    async def _run_continuous(self, agent, on_step_end):
        """Drive the SDK's public single-step API without an artificial last step."""
        try:
            # Closing a prior agent stops the session event bus while retaining
            # its browser. Restart that bus for follow-up tasks, as Agent.run does.
            await agent.browser_session.start()
            while not agent.state.stopped:
                if agent.state.consecutive_failures >= agent.settings.max_failures:
                    raise RuntimeError("The browser could not make progress after repeated failures.")
                # Each operation remains bounded even when the overall task is not.
                done, _ = await asyncio.wait_for(agent.take_step(), timeout=agent.settings.step_timeout)
                await on_step_end(agent)
                if done:
                    return agent.history
                # Keep the current task/context while bounding retained step records.
                if len(agent.history.history) > 200:
                    del agent.history.history[:-200]
            return agent.history
        finally:
            try:
                await agent.eventbus.stop(clear=True, timeout=3)
            finally:
                await agent.close()

    async def run_instruction(self, instruction, url=None, max_steps=10, timeout_seconds=120):
        if self.loop_active:
            yield {"event": "error", "error": "An instruction is already running"}
            return
        # Reserve before the first await, including browser startup.
        self.loop_active = True
        started = time.monotonic()
        queue = asyncio.Queue(maxsize=32)
        self._events = queue
        self._cancel_requested = False
        step = 0
        llm = None
        reported = 0
        outcome = "cancelled"

        async def on_step_end(agent):
            nonlocal step, reported
            step += 1
            decision_count = getattr(llm, "decision_count", len(llm.decisions))
            fresh = decision_count > reported
            reported = decision_count
            decision = llm.last_decision if fresh else None
            result_text, failed = "", not fresh
            for result in getattr(agent.state, "last_result", None) or []:
                if result.error:
                    failed = True
                    result_text = str(result.error)
                elif result.extracted_content:
                    result_text = str(result.extracted_content)
            try:
                current_url = await agent.browser_session.get_current_page_url()
            except Exception:
                current_url = ""
            await queue.put(
                {
                    "event": "step",
                    "step": step,
                    "action": decision["chosen"] if decision else "(no decision)",
                    "params": decision["params"] if decision else {},
                    "rationale": decision["rationale"] if decision else "No Jev decision was produced",
                    "confidence": decision["confidence"] if decision else 0,
                    "jev_latency_ms": decision["jev_latency_ms"] if decision else 0,
                    "propose_latency_ms": decision["propose_latency_ms"] if decision else 0,
                    "cost": decision["cost"] if decision else 0,
                    "generation_id": decision["generation_id"] if decision else None,
                    "options": decision["options"] if decision else [],
                    "rejected": decision.get("rejected", []) if decision else [],
                    "url": current_url,
                    "success": not failed,
                    "message": result_text[:500],
                    "screenshot_b64": await self._snapshot(),
                }
            )

        async def drive():
            nonlocal llm
            try:
                await self.startup()
                llm = ChatJev(tools=self.tools)
                if url:
                    from browser_use.browser.events import NavigateToUrlEvent

                    await self.browser.event_bus.dispatch(NavigateToUrlEvent(url=url))
                agent = Agent(
                    task=instruction,
                    llm=llm,
                    browser=self.browser,
                    tools=self.tools,
                    use_vision=False,
                    max_history_items=50,
                )
                history = (
                    await self._run_continuous(agent, on_step_end)
                    if max_steps is None
                    else await agent.run(max_steps=max_steps, on_step_end=on_step_end)
                )
                success = history.is_done() and history.is_successful() is True
                await queue.put(
                    {
                        "event": "complete" if success else "incomplete",
                        "message": history.final_result()
                        or (
                            "Browser stopped before task completion"
                            if max_steps is None
                            else "Step limit reached before task completion"
                        ),
                        "steps": step,
                        "success": success,
                        "total_cost": llm.total_cost(),
                        "total_time_ms": int((time.monotonic() - started) * 1000),
                        "screenshot_b64": await self._snapshot(),
                    }
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
                logger.exception("Browser run failed")
                await queue.put({"event": "error", "error": self.last_error[:500], "step": step})

        self._runner = asyncio.create_task(drive())
        try:
            yield {"event": "initialized", "message": "Starting browser agent", "model": "typesafe-ai/jev"}
            while True:
                remaining = (
                    None
                    if timeout_seconds is None
                    else max(0, timeout_seconds - (time.monotonic() - started))
                )
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=remaining)
                except asyncio.TimeoutError:
                    outcome = "timeout"
                    yield {"event": "timeout", "message": f"Stopped after {timeout_seconds}s", "step": step}
                    return
                yield event
                if event["event"] in {"complete", "incomplete", "error", "cancelled"}:
                    outcome = event["event"]
                    return
        finally:
            self.cancel()
            runner = self._runner

            async def release():
                await asyncio.gather(runner, return_exceptions=True)
                # Cleanup has its own task so a disconnected HTTP response's
                # cancellation scope cannot interrupt it and strand the slot.
                self.loop_active = False
                self._runner = None
                self._events = None
                self.instruction_history.append(
                    {
                        "instruction": instruction,
                        "steps": step,
                        "cost": llm.total_cost() if llm else 0,
                        "outcome": outcome,
                        "total_time_ms": int((time.monotonic() - started) * 1000),
                        "timestamp": time.time(),
                    }
                )
                self._cleanup = None

            self._cleanup = asyncio.create_task(release())
            await asyncio.shield(self._cleanup)

    def get_stats(self):
        return {
            "loop_active": self.loop_active,
            "browser_started": self.browser is not None,
            "last_error": self.last_error,
            "actions_available": len(self.tools.registry.registry.actions),
            "total_instructions": len(self.instruction_history),
            "recent_instructions": list(self.instruction_history)[-5:],
        }
