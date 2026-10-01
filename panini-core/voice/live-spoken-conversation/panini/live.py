"""OpenAI Live media session. Audio uses WebRTC; private work uses a server sideband."""

import asyncio
import contextlib
import json
import logging
import time
from collections import deque
from uuid import uuid4

import httpx
from websockets.asyncio.client import connect

from .conversation import control_intent
from .prompts import LIVE_INSTRUCTIONS


class LiveConnection:
    def __init__(self, api_key, on_event, on_request, *, model="gpt-live-1", voice="marin"):
        self.api_key, self.on_event, self.on_request = api_key, on_event, on_request
        self.model, self.voice = model, voice
        self.session_id = None
        self.socket = None
        self.reader = None
        self.ready = asyncio.Event()
        self.finalized = asyncio.Event()
        self.input = deque(maxlen=200)
        self.last_input_at = 0
        self.input_changed = asyncio.Event()
        self.delegations = deque(maxlen=100)
        self.handlers = set()
        self.closing = False
        self.send_lock = asyncio.Lock()
        self.usage = None
        self.control_timer = None
        self.turn_timer = None
        self.active_requests = 0

    def configuration(self):
        return {
            "model": self.model,
            "instructions": LIVE_INSTRUCTIONS,
            "audio": {"output": {"voice": self.voice}},
            "delegation": {"type": "client"},
            "store": False,
        }

    async def create(self, sdp):
        async with httpx.AsyncClient(timeout=25) as client:
            response = await client.post(
                "https://api.openai.com/v1/live/sessions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"session": self.configuration(), "transport": {"type": "webrtc", "sdp": sdp}},
            )
        if response.status_code not in {200, 201}:
            raise RuntimeError(
                f"Live voice connection was rejected ({response.status_code}). Check OpenAI model access and billing."
            )
        body = response.json()
        self.session_id = body["session"]["id"]
        try:
            self.socket = await connect(
                f"wss://api.openai.com/v1/live/sessions/{self.session_id}/attach",
                additional_headers={"Authorization": f"Bearer {self.api_key}"},
                open_timeout=15,
                max_size=2_000_000,
                ping_interval=20,
                ping_timeout=20,
            )
            self.reader = asyncio.create_task(self._read())
            await asyncio.wait_for(self.ready.wait(), 15)
        except BaseException:
            await self.close()
            raise
        return body["transport"]["sdp"]

    async def send(self, kind, **values):
        if not self.socket:
            return
        async with self.send_lock:
            await self.socket.send(json.dumps({"type": kind, "event_id": uuid4().hex, **values}))

    async def speak(self, text, delegation_id=None):
        # The API bounds each context append to 500 tokens. Keep independent spoken updates short.
        await self.send("session.commentary.append", delegation_id=delegation_id, content=text[:1200])

    async def handle_event(self, event):
        kind = event.get("type", "")
        if kind == "session.started":
            self.ready.set()
        elif kind == "session.closed":
            self.usage = event.get("usage")
            self.finalized.set()
        elif kind == "session.input_transcript.delta":
            self.input.append(
                {
                    "start": event.get("start_ms", 0),
                    "end": event.get("end_ms", 0),
                    "text": str(event.get("delta", "")),
                    "claimed": False,
                }
            )
            self.last_input_at = time.monotonic()
            self.input_changed.set()
            await self.on_event(
                {
                    "type": "transcript",
                    "role": "user",
                    "delta": event.get("delta", ""),
                    "start_ms": event.get("start_ms", 0),
                    "end_ms": event.get("end_ms", 0),
                }
            )
            if self.control_timer:
                self.control_timer.cancel()
            self.control_timer = asyncio.create_task(self._explicit_control())
            if self.turn_timer:
                self.turn_timer.cancel()
            self.turn_timer = asyncio.create_task(self._settled_turn())
        elif kind == "session.output_transcript.delta":
            await self.on_event(
                {
                    "type": "transcript",
                    "role": "assistant",
                    "delta": event.get("delta", ""),
                    "start_ms": event.get("start_ms", 0),
                    "end_ms": event.get("end_ms", 0),
                }
            )
        elif kind == "session.delegation.created":
            delegation = event.get("delegation", {})
            identifier = delegation.get("id")
            if not identifier or identifier in self.delegations or delegation.get("target") != "client":
                return
            self.delegations.append(identifier)
            offset = event.get("offset_ms", 0)
            # Transcript delivery can lag delegation. Never advance a timestamp
            # cursor before the corresponding speech has actually arrived.
            self._track(self._delegated_turn(identifier, offset))
        elif kind == "error":
            error = event.get("error", {})
            logging.getLogger(__name__).warning(
                "Live event rejected: code=%s param=%s", error.get("code"), error.get("param")
            )
            # Never echo provider bodies or audio into logs or UI.
            await self.on_event(
                {
                    "type": "error",
                    "message": "The voice provider rejected a session event. Reconnect if the conversation stops.",
                }
            )

    async def _explicit_control(self):
        # Native audio still handles conversation. A debounced, exact control is
        # also enforced by the app when the model merely says goodbye or okay.
        await asyncio.sleep(0.6)
        fragments = [item for item in self.input if not item["claimed"]]
        # Un-delegated small talk from earlier in the session is not part of a
        # new explicit command. Keep only the latest contiguous spoken phrase.
        for index in range(len(fragments) - 1, 0, -1):
            if fragments[index]["start"] - fragments[index - 1]["end"] > 1200:
                fragments = fragments[index:]
                break
        text = "".join(item["text"] for item in fragments).strip()
        if control_intent(text) and not self.closing:
            self._dispatch(fragments, None, "control")

    def _track(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.handlers.add(task)
        task.add_done_callback(self._handled)
        return task

    def _dispatch(self, fragments, identifier, source):
        # This synchronous claim is shared by native delegations, controls and
        # the fallback. A late delegation can never replay the same actions.
        fragments = [item for item in fragments if not item["claimed"]]
        text = "".join(item["text"] for item in fragments).strip()
        if not text or self.closing:
            return
        if self.active_requests >= 4 and not control_intent(text):
            return  # Leave unclaimed; the next completed handler drains it.
        for item in fragments:
            item["claimed"] = True
        self.active_requests += 1

        async def request():
            try:
                await self.on_event({"type": "routing", "stage": "received", "source": source})
                await self.on_request(text, identifier)
            finally:
                self.active_requests -= 1
                if not self.closing and any(not item["claimed"] for item in self.input):
                    if self.turn_timer:
                        self.turn_timer.cancel()
                    self.turn_timer = asyncio.create_task(self._settled_turn())

        self._track(request())

    async def _delegated_turn(self, identifier, offset):
        deadline = time.monotonic() + 4
        while not self.closing:
            self.input_changed.clear()
            fragments = [item for item in self.input if not item["claimed"] and item["start"] <= offset]
            if fragments:
                # A short grace period lets the remainder of a delivered
                # phrase arrive. Records, not audio timestamps, own deduping.
                while time.monotonic() - self.last_input_at < 1.1 and not self.closing:
                    await asyncio.sleep(0.1)
                # The model may delegate midway through the user's sentence.
                # Include its settled remainder rather than cutting at offset.
                self._dispatch(list(self.input), identifier, "delegation")
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                if any(item["claimed"] and item["start"] <= offset for item in self.input):
                    return  # Already routed by the fallback or an earlier delegation.
                await self.on_event({"type": "routing", "stage": "awaiting_speech"})
                await self.speak(
                    "The browser has not received the spoken task yet. Please repeat the full instruction.",
                    delegation_id=identifier,
                )
                return
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self.input_changed.wait(), remaining)

    async def _settled_turn(self):
        # Native audio remains duplex. This is a safety net for an absent
        # delegation, not a record-then-transcribe voice transport.
        await asyncio.sleep(1.6)
        if not self.closing:
            self._dispatch(list(self.input), None, "speech_fallback")

    def _handled(self, task):
        self.handlers.discard(task)
        if not task.cancelled():
            task.exception()  # consume; request handler surfaces a user-facing failure

    async def _read(self):
        try:
            async for raw in self.socket:
                await self.handle_event(json.loads(raw))
                if self.finalized.is_set():
                    break
        except (Exception, asyncio.CancelledError):
            pass
        finally:
            if not self.closing:
                await self.on_event(
                    {
                        "type": "disconnected",
                        "message": "Voice connection lost. Browser work is stopping; reconnect to continue.",
                    }
                )

    async def close(self):
        if self.closing:
            return
        self.closing = True
        timers = [task for task in (self.control_timer, self.turn_timer) if task]
        for task in timers:
            task.cancel()
        await asyncio.gather(*timers, return_exceptions=True)
        for task in list(self.handlers):
            task.cancel()
        await asyncio.gather(*self.handlers, return_exceptions=True)
        if self.socket:
            with contextlib.suppress(Exception):
                await self.send("session.close")
                await asyncio.wait_for(self.finalized.wait(), 4)
            with contextlib.suppress(Exception):
                await self.socket.close()
        if self.reader and self.reader is not asyncio.current_task():
            self.reader.cancel()
            await asyncio.gather(self.reader, return_exceptions=True)
        if self.session_id and not self.finalized.is_set():
            with contextlib.suppress(Exception):
                async with httpx.AsyncClient(timeout=8) as client:
                    await client.post(
                        f"https://api.openai.com/v1/live/sessions/{self.session_id}/hangup",
                        headers={"Authorization": f"Bearer {self.api_key}"},
                    )
