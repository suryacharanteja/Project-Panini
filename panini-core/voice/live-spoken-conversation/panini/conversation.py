"""Conversation state adapted from macbrow's Outcome/follow-up design (see LICENSE).

The injected browser owns execution. This module owns conversation memory, interruption,
clarifications and ordered results; it never controls the desktop or evaluates generated code.
"""

import asyncio
import json
import logging
import re
from collections import deque
from uuid import uuid4

from .errors import BrowserUnavailable
from .models import CONVERSATION_RULES, REPORT_RULES, Report, Turn


def normalized(text):
    return " ".join(str(text).split()).casefold()


def control_intent(text):
    """Only complete, explicit session controls; never 'don't stop' or 'stop talking'."""
    text = re.sub(r"[.!?,]+", " ", str(text).casefold())
    text = " ".join(text.split())
    text = re.sub(r"^(?:hey )?panini\s+", "", text)
    text = re.sub(r"^(?:(?:okay|ok|please|just|can you|could you)\s+)+", "", text)
    text = re.sub(r"\s+please$", "", text)
    if re.fullmatch(
        r"(?:end|close|stop) (?:the |this |our )?(?:voice )?(?:conversation|call|session)(?: goodbye)?", text
    ):
        return "end"
    if re.fullmatch(r"(?:stop|pause|cancel)(?: (?:now|the task|the browser|this task))?", text):
        return "stop"
    if re.fullmatch(r"(?:leave it|forget it|never mind)(?: (?:leave it|forget it|never mind))*", text):
        return "stop"
    return None


def is_backchannel(text):
    """Brief listening acknowledgements never replace an active browser task."""
    text = re.sub(r"\[[^\]]*\]", " ", str(text).casefold()).replace("’", "'")
    text = re.sub(r"[^a-z' ]", " ", text)
    return bool(
        re.fullmatch(
            r"\s*(?:(?:okay|ok|yeah|yes|yep|sure|right|fine|good|great|super|thanks|thank you|"
            r"that's fine|that's great|that's good|it's good|mm|mhm|uh huh|coming)\s*)+",
            text,
        )
    )


class Conversation:
    def __init__(self, browser, parse, emit):
        self.browser, self.parse, self.emit = browser, parse, emit
        self.history = deque(maxlen=24)
        self.items = []
        self.objective = ""
        self.collect_results = False
        self.question = ""
        self.state = "listening"
        self.progress = "No browser task has started."
        self.page = {}
        self.task = None
        self.revision = 0
        self.cancel_epoch = 0
        self.closed = False
        self._turn_lock = asyncio.Lock()
        self._seen = deque(maxlen=100)

    def context(self):
        return {
            "conversation": list(self.history),
            "objective": self.objective,
            "collect_results": self.collect_results,
            "question": self.question,
            "results": self.items,
            "task_state": self.state,
            "progress": self.progress,
            "observed_page": self.page,
        }

    async def say(self, message, **extra):
        if message and not self.closed:
            await self.emit({"type": "message", "message": message, **extra})

    async def handle(self, text, turn_id=None):
        text = str(text).strip()[:5000]
        if not text or self.closed or (turn_id and turn_id in self._seen):
            return
        if turn_id:
            self._seen.append(turn_id)
        if self.objective and not self.question and is_backchannel(text):
            self.history.append({"role": "user", "text": text})
            await self.emit({"type": "routing", "stage": "acknowledged", "intent": "reply", "message": self.progress})
            return
        # A complete, unambiguous stop command needn't wait behind a planner call.
        control = control_intent(text)
        if control:
            self.history.append({"role": "user", "text": text})
            await self.stop()
            await self.say(
                "Goodbye. Ending the conversation."
                if control == "end"
                else "Stopped. Tell me what you want to do next.",
                end=control == "end",
            )
            return
        cancel_epoch = self.cancel_epoch
        async with self._turn_lock:
            if self.closed or cancel_epoch != self.cancel_epoch:
                return
            turn = await self.parse(CONVERSATION_RULES, {**self.context(), "user": text}, Turn)
            if self.closed or cancel_epoch != self.cancel_epoch:
                return
            self.history.append({"role": "user", "text": text})
            await self.emit(
                {"type": "routing", "stage": "interpreted", "intent": turn.intent, "message": self.progress}
            )
            if turn.intent in {"stop", "end"}:
                await self.stop()
                await self.say(turn.reply or "Stopped.", end=turn.intent == "end")
                return
            if turn.intent == "reply":
                self.history.append({"role": "assistant", "text": turn.reply})
                await self.say(turn.reply)
                return
            if not turn.instruction.strip():
                await self.say("What would you like me to do in the browser?")
                return
            if self.task and not self.task.done() and normalized(turn.instruction) == normalized(self.objective):
                await self.emit(
                    {"type": "routing", "stage": "acknowledged", "intent": "reply", "message": self.progress}
                )
                return
            selected = next((item for item in self.items if item["id"] == turn.item_id), None)
            if turn.item_id and selected is None:
                await self.say("Which result do you mean? Please say its title.")
                return
            await self.stop(announce=False)
            self.objective = turn.instruction[:4000]
            self.collect_results = turn.collect_results
            if self.collect_results:
                self.items = []
                await self.emit({"type": "results", "items": []})
            if selected:
                self.objective += "\nSelected result from our conversation: " + json.dumps(selected, ensure_ascii=False)
            self.question = ""
            self.page = {}
            self.state = "working"
            self.progress = turn.reply or "Working in your selected browser."
            await self.say(self.progress)
            await self.emit({"type": "task", "state": self.state, "message": self.progress})
            self.task = asyncio.create_task(self._run(self.revision))

    async def _run(self, revision):
        terminal = {"event": "incomplete", "message": "No verified result was returned."}
        try:
            async for event in self.browser.run(self.objective):
                if revision != self.revision or self.closed:
                    return
                if event.get("event") == "observation":
                    self.page = {key: event.get(key, "") for key in ("url", "title", "text")}
                    await self.emit({"type": "page", **self.page})
                    continue
                if event.get("message"):
                    self.progress = event["message"][:1200]
                await self.emit({"type": "browser", "event": event})
                if event.get("event") in {"complete", "incomplete", "error", "timeout", "cancelled"}:
                    terminal = event
            if revision != self.revision or self.closed:
                return
            observation = await self.browser.observation()
            self.page = {key: observation.get(key, "") for key in ("url", "title", "text")}
            report_input = {
                **self.context(),
                "complete": terminal.get("event") == "complete",
                "outcome": terminal,
                "observation": observation,
            }
            report = await self.parse(REPORT_RULES, report_input, Report)
            if revision != self.revision or self.closed:
                return
            observed = normalized(observation.get("text", ""))
            # LinkedIn inserts connection badges between names and headlines.
            # A model can concatenate those fields into a non-contiguous quote.
            # Repair the extraction once against the SAME observed page; never
            # restart a successful browser search or weaken evidence validation.
            if (
                self.collect_results
                and report.items
                and not any(
                    len(normalized(item.evidence)) >= 8 and normalized(item.evidence) in observed
                    for item in report.items[:5]
                )
            ):
                report = await self.parse(
                    REPORT_RULES,
                    {
                        **report_input,
                        "rejected_extraction": report.model_dump(),
                        "validation_error": (
                            "None of the evidence quotes matched the observed text. Copy one exact contiguous "
                            "span per result, such as its name or headline (at least 8 characters). Do not "
                            "join names/headlines/location while skipping intervening connection badges or words. "
                            "Correct extraction only; the browser task must not be rerun."
                        ),
                    },
                    Report,
                )
                if revision != self.revision or self.closed:
                    return
            links = {a.get("href") for a in observation.get("actions", []) if a.get("href")}
            links.add(observation.get("url", ""))
            items = []
            for item in report.items[:5] if self.collect_results else []:
                if len(normalized(item.evidence)) < 8 or normalized(item.evidence) not in observed:
                    continue
                record = item.model_dump()
                record["url"] = item.url if item.url in links and item.url.startswith(("https://", "http://")) else ""
                items.append({"id": uuid4().hex[:12], **record})
            if items:
                self.items = items
                await self.emit({"type": "results", "items": items})
                if len(items) < len(report.items):
                    report.message = f"I verified {len(items)} visible results."
            elif self.collect_results and report.items:
                report.message = "I reached the page, but could not verify that result list. I can inspect it again."
            self.question = report.question
            self.state = "needs_information" if self.question else "listening"
            self.progress = report.message
            self.history.append({"role": "assistant", "text": report.message, "question": self.question})
            await self.emit({"type": "task", "state": self.state, "message": self.progress})
            await self.say(
                " ".join(filter(None, [report.message, report.question])),
                items=items,
                feedback=True,
                outcome=terminal.get("event"),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if not self.closed and revision == self.revision:
                self.state = "listening"
                logging.getLogger(__name__).warning("Voice browser task failed: %s", type(exc).__name__)
                self.progress = (
                    str(exc)
                    if isinstance(exc, BrowserUnavailable)
                    else (
                        "The browser task encountered an error. I don't have a verified result. "
                        "The task details show where it stopped."
                    )
                )
                await self.emit(
                    {
                        "type": "task",
                        "state": self.state,
                        "message": self.progress,
                        "error_code": getattr(exc, "code", "browser_task_failed"),
                    }
                )
                await self.say(self.progress, feedback=True, outcome="error")

    async def stop(self, announce=True):
        self.revision += 1
        if announce:
            self.cancel_epoch += 1
        task, self.task = self.task, None
        if task and not task.done():
            self.browser.cancel()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.state = "listening"
        if announce:
            self.progress = "Browser task stopped."
            await self.emit({"type": "task", "state": self.state, "message": self.progress})

    async def close(self):
        self.closed = True
        await self.stop(announce=False)
