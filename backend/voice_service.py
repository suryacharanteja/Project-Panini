"""Composition of PANINI's live conversation with the app's existing browser owner."""

import asyncio
import json
import logging
import os
import secrets
import time
from collections import deque
from contextvars import ContextVar
from copy import deepcopy
from types import SimpleNamespace

from fastapi import HTTPException
from openai import AsyncOpenAI
from panini import Conversation
from panini.errors import BrowserUnavailable
from panini.live import LiveConnection

from . import config, usage_log
from .routes.browser import BrowserRunRequest, get_bridge, prepare_run


def workspace_evidence(page):
    """Carry source-labelled prior readings into spoken results, not DOM actions."""
    workspace = page.get("workspace")
    if not workspace:
        return page
    approved = {tab["id"] for tab in workspace.get("tabs", [])}
    previous = [
        item
        for item in page.get("visited_pages") or []
        if item.get("tab_id") in approved and item.get("tab_id") != page.get("tab_id")
    ][-5:]
    text = str(page.get("text", ""))[:12000]
    for item in previous:
        text += f"\n\nPreviously observed in tab {item['tab_id']} at {item.get('url', '')}:\n{str(item.get('text', ''))[:2000]}"
    return {
        **page,
        "text": text,
        "actions": [
            *page.get("actions", []),
            *[{"href": item.get("url", ""), "text": item.get("title", "")} for item in previous],
        ],
    }


class VoiceBrowser:
    def __init__(self, app, target, owner="The live conversation"):
        self.request = SimpleNamespace(app=app)
        self.target = target
        self.owner = owner
        self.loop = None
        self.last = {}
        self.completed_page = None

    async def run(self, instruction):
        self.last = {}
        self.completed_page = None
        seen_page = None
        payload = BrowserRunRequest(instruction=instruction, run_policy="continuous", **self.target)
        try:
            self.loop, options = prepare_run(payload, self.request, voice_owner=True, owner=self.owner)
        except HTTPException as exc:
            raise BrowserUnavailable("browser_unavailable", str(exc.detail)) from exc
        # Clarifications stay in conversation memory; every resumed run freshly observes the page.
        options["instruction"] += (
            "\nIf required personal information is missing, stop and ask for it. "
            "Never invent personal answers. Report actual results and any remaining question."
        )
        async for event in self.loop.run_instruction(**options):
            if self.target["mode"] == "extension":
                page = self.loop.last_run.get("observation")
                if page and page is not seen_page:
                    seen_page = page
                    yield {"event": "observation", **page}
                if event.get("event") == "complete" and page:
                    self.completed_page = deepcopy(page)
            if event.get("event") in {"complete", "incomplete", "error", "timeout"}:
                self.last = event
            yield event

    def cancel(self):
        if self.loop:
            self.loop.cancel()

    async def observation(self):
        if self.target["mode"] == "extension" and self.loop:
            bridge = self.loop.bridge
            with bridge.bind_target(self.target["target_id"]):
                # Reading does not reset the action-space marker. Full rendered
                # text includes result cards below the current viewport.
                page = await bridge.text()
            previous = self.completed_page or self.loop.last_run.get("observation", {})
            if previous.get("url") == page.get("url"):
                return workspace_evidence(
                    {
                        **previous,
                        **page,
                        "text": "\n".join(dict.fromkeys([previous.get("text", ""), page.get("text", "")]))[
                            :20000
                        ],
                        "actions": previous.get("actions", []),
                        "completion_verified": self.completed_page is not None,
                    }
                )
            if self.completed_page:
                return workspace_evidence(
                    {
                        **self.completed_page,
                        "completion_verified": True,
                        "current_page": {key: page.get(key, "") for key in ("url", "title", "text")},
                    }
                )
            return {**page, "actions": []}
        if self.loop and self.loop.browser:
            page = await asyncio.wait_for(self.loop.browser.get_current_page(), 10)
            if page:
                snapshot = await asyncio.wait_for(
                    page.evaluate("""() => ({
                    url: location.href, title: document.title,
                    text: document.body.innerText.slice(0, 16000),
                    actions: [...document.querySelectorAll('a[href]')]
                        .filter(a => a.getClientRects().length).slice(0, 150)
                        .map(a => ({href: a.href, text: a.innerText.slice(0, 150)}))
                })"""),
                    10,
                )
                return json.loads(snapshot) if isinstance(snapshot, str) else snapshot
        return {"text": self.last.get("message", ""), "url": "", "actions": []}


class VoiceSession:
    def __init__(self, app, target, *, connection_factory=LiveConnection):
        self.app = app
        self.token = secrets.token_urlsafe(32)
        self.events = deque(maxlen=200)
        self.sequence = 0
        self.changed = asyncio.Event()
        self.closed = False
        self.closing = False
        self.stream_connected = False
        self.created = time.monotonic()
        self.dialogue = deque(maxlen=60)
        self.diagnostics = {"requests": 0, "runs": 0, "actions": 0, "stage": "connecting", "error_code": None}
        self.last_progress_at = 0
        self.outcome = None
        self.guard = None
        try:
            self.duration = max(60, min(86400, int(os.getenv("VOICE_SESSION_SECONDS", "3600"))))
        except ValueError:
            self.duration = 3600
        self.delegation = ContextVar("voice_delegation", default=None)
        self.client = AsyncOpenAI(timeout=30, max_retries=0)
        self.conversation = Conversation(VoiceBrowser(app, target), self.parse, self.publish)
        self.connection = connection_factory(
            os.environ["OPENAI_API_KEY"],
            self.provider_event,
            self.request,
            model=os.getenv("VOICE_MODEL", "gpt-live-1"),
            voice=os.getenv("VOICE_NAME", "marin"),
        )

    async def parse(self, rules, payload, schema):
        # The conversation module already sends its own recent history, so the native
        # transcript only needs the latest exchanges (it was 16k characters on every call).
        context, remaining = [], 4000
        for item in reversed(self.dialogue):
            if remaining <= 0:
                break
            text = item["text"][-remaining:]
            context.append({"role": item["role"], "text": text})
            remaining -= len(text)
        payload = {**payload, "native_conversation": list(reversed(context))}
        response = await self.client.chat.completions.parse(
            model=config.EXTENSION_PLANNER_MODEL,
            temperature=0,
            max_completion_tokens=2200,
            messages=[
                {"role": "system", "content": rules},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            response_format=schema,
            prompt_cache_key="panini-voice",
        )
        usage_log.record_openai("voice", config.EXTENSION_PLANNER_MODEL, getattr(response, "usage", None))
        result = response.choices[0].message.parsed
        if result is None:
            raise RuntimeError("Conversation planner could not understand the turn")
        return result

    async def start(self, sdp):
        answer = await self.connection.create(sdp)
        self.guard = asyncio.create_task(self._guard())
        await self.publish({"type": "connected", "message": "PANINI is ready. Speak naturally."})
        return answer

    async def publish(self, event):
        kind = event.get("type")
        if kind == "routing":
            self.diagnostics["stage"] = event["stage"]
            if event["stage"] == "received":
                self.diagnostics["source"] = event.get("source")
            if "intent" in event:
                self.diagnostics["intent"] = event["intent"]
        elif kind == "task":
            self.diagnostics["stage"] = event["state"]
            self.diagnostics["error_code"] = event.get("error_code")
            if event["state"] == "working":
                self.diagnostics["runs"] += 1
                self.outcome = None
        elif kind == "browser":
            browser_event = event["event"].get("event")
            if browser_event == "step":
                self.diagnostics["actions"] += 1
                self.diagnostics["stage"] = "executing"
            elif browser_event in {"complete", "incomplete", "cancelled", "error", "timeout"}:
                self.outcome = browser_event
        self.sequence += 1
        # Retain only the latest browser image, never 200 copies or any audio payloads.
        if event.get("type") == "browser" and event["event"].get("screenshot_b64"):
            for previous in self.events:
                if previous.get("type") == "browser":
                    previous["event"].pop("screenshot_b64", None)
        self.events.append({"sequence": self.sequence, **event})
        self.changed.set()
        # Quiet, application-owned status stops the voice frontend guessing
        # that an ongoing browser operation has failed. No page instructions
        # or private model reasoning are forwarded as steering instructions.
        if not self.closing and isinstance(self.connection, LiveConnection):
            now = time.monotonic()
            if kind in {"task", "connected", "page"} or (
                kind == "browser" and now - self.last_progress_at >= 5
            ):
                self.last_progress_at = now
                await self.connection.send(
                    "session.thinking.append",
                    delegation_id=self.delegation.get(),
                    content=json.dumps(
                        {
                            "browser_state": self.conversation.state,
                            "actions_executed": self.diagnostics["actions"],
                            "worker_outcome": self.outcome or "No verified outcome yet",
                            "task_feedback": event.get("message", "")[:350] if kind == "task" else "",
                            "page_observation_untrusted": {
                                "title": self.conversation.page.get("title", "")[:100],
                                "text_excerpt": self.conversation.page.get("text", "")[:350],
                            },
                            "reading_limit": "This excerpt may be partial. Missing results in it do not establish no results or a login requirement.",
                        }
                    ),
                )
        if event.get("type") == "message" and not self.closing:
            content = event["message"]
            if event.get("items"):
                content += "\nVerified results in order: " + "; ".join(
                    f"{index + 1}. {item['title']} — {item['detail']}"
                    for index, item in enumerate(event["items"])
                )
            await self.connection.speak(content, delegation_id=self.delegation.get())
            if event.get("end"):
                asyncio.create_task(self._end_after_reply())

    async def provider_event(self, event):
        if event.get("type") == "transcript":
            role = event["role"]
            if self.dialogue and self.dialogue[-1]["role"] == role:
                self.dialogue[-1]["text"] = (self.dialogue[-1]["text"] + event["delta"])[-5000:]
            else:
                self.dialogue.append({"role": role, "text": event["delta"][:5000]})
        await self.publish(event)
        if event.get("type") == "disconnected":
            asyncio.create_task(self.close())

    async def request(self, text, identifier=None):
        self.diagnostics["requests"] += 1
        context = self.delegation.set(identifier)
        try:
            await self.conversation.handle(text, identifier)
        except Exception as exc:
            logging.getLogger(__name__).warning("Voice request failed: %s", type(exc).__name__)
            self.diagnostics.update(stage="failed", error_code="planner_unavailable")
            await self.publish(
                {
                    "type": "message",
                    "message": "The task planner failed before it could start this request. No new browser actions were started. Please repeat the instruction.",
                }
            )
        finally:
            self.delegation.reset(context)

    async def _end_after_reply(self):
        await asyncio.sleep(2)
        await self.close()

    async def _guard(self):
        # A failed browser negotiation must not leave a paid microphone session running.
        while not self.closing:
            await asyncio.sleep(1)
            elapsed = time.monotonic() - self.created
            reason = None
            if elapsed > 20 and not self.stream_connected:
                reason = "Voice event connection was not established. Start a new conversation."
            elif elapsed >= self.duration:
                reason = "The voice session reached its configured duration. Start a new conversation."
            elif self.conversation.browser.target["mode"] == "extension":
                bridge = get_bridge(self.conversation.browser.request)
                if not bridge.connected or bridge.target_id != self.conversation.browser.target["target_id"]:
                    reason = "The attached browser tab changed or disconnected. Browser work stopped. Check the attached tab and start a new conversation."
            if reason:
                await self.conversation.stop()
                self.diagnostics.update(stage="disconnected", error_code="session_target_or_connection_lost")
                try:
                    await self.publish({"type": "error", "message": reason})
                    await self.publish({"type": "message", "message": reason})
                finally:
                    await self.close(reason)
                return

    async def close(self, reason="Conversation ended. Microphone released."):
        if self.closing:
            return
        self.closing = True
        await self.conversation.close()
        if self.guard and self.guard is not asyncio.current_task():
            self.guard.cancel()
            await asyncio.gather(self.guard, return_exceptions=True)
        try:
            await self.connection.close()
        finally:
            await self.client.close()
            self.closed = True
            await self.publish(
                {
                    "type": "closed",
                    "message": reason,
                    "usage": self.connection.usage,
                }
            )
            if self.app.state.voice_session is self:
                self.app.state.voice_last_diagnostics = dict(self.diagnostics)
                self.app.state.voice_session = None
