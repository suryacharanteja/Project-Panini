"""Slack over Socket Mode (V2-17 §5.5): no public URL and no SDK; the protocol is a WebSocket of envelopes.

`apps.connections.open` (app-level xapp- token) returns a WebSocket URL; every `events_api` envelope is
acknowledged with its envelope_id before any work, and replies go through `chat.postMessage` (bot xoxb- token).

Behaviour ported from OpenDots `slack-channel.ts`:
- a mention subscribes its thread (unless PANINI is paused) and runs; a subscribe failure never blocks the answer;
- follow-up messages in a channel thread run only when the thread is subscribed, and never on unreadable state;
- bots, apps and edited or deleted messages are ignored; the workspace and member IDs must match exactly.
"""

import asyncio
import json
import re

import httpx
import websockets

from ..core_contracts import Readiness
from .base import InboundMessage, ProviderError, safe_failure

API = "https://slack.com/api/"
MENTION = re.compile(r"<@[UW][A-Z0-9]+>\s*")


class SubscriptionError(RuntimeError):
    pass


class Slack:
    name = "slack"

    def __init__(self, account, accounts, *, transport=None, connect=None):
        self.account, self.accounts = account, accounts
        self.client = httpx.AsyncClient(base_url=API, timeout=30, transport=transport, follow_redirects=False)
        self.connect = connect or (lambda url: websockets.connect(url, max_size=2**20))
        self.paused = lambda: False  # Set by the manager.
        self.task = None
        self.state = "off"

    # Web API ---------------------------------------------------------------------------------------
    async def api(self, method, payload, *, token="token"):
        secret = self.accounts.secret(self.account["id"], token)
        response = await self.client.post(method, json=payload, headers={"Authorization": f"Bearer {secret}"})
        if response.status_code >= 400:
            retry = response.headers.get("retry-after")
            raise ProviderError(response.status_code, int(retry) if retry and retry.isdigit() else None)
        try:
            body = response.json()
        except ValueError as error:
            raise ProviderError() from error
        if not body.get("ok"):
            raise ProviderError(429 if body.get("error") == "ratelimited" else None)
        return body

    # Subscriptions (threads PANINI was mentioned in) -------------------------------------------------
    def subscribed(self, thread_key):
        try:
            return bool(self.accounts.state(self.account["id"], f"sub:{thread_key}", False))
        except Exception as error:  # noqa: BLE001 -- unreadable state must never run anything.
            raise SubscriptionError("Slack subscription lookup failed.") from error

    def subscribe(self, thread_key):
        self.accounts.set_state(self.account["id"], f"sub:{thread_key}", True)

    # Events ---------------------------------------------------------------------------------------
    def normalize(self, payload):
        """An InboundMessage for an event PANINI may act on, else None. Mentions also subscribe their thread."""
        event = payload.get("event") or {}
        team = str(payload.get("team_id") or event.get("team") or "")
        kind = event.get("type")
        if kind not in {"app_mention", "message"}:
            return None
        subtype = event.get("subtype")
        if subtype in {"message_changed", "message_deleted"}:
            return InboundMessage("slack", self.account["id"], f"{team}:{event.get('channel', '')}:", "edit",
                                  "", "", kind="edit", human=False)  # fmt: skip
        human = not event.get("bot_id") and subtype in {None, "thread_broadcast"} and bool(event.get("user"))
        if not human:
            return None
        channel, ts = str(event.get("channel", "")), str(event.get("ts", ""))
        if not channel or not ts:
            return None
        text = MENTION.sub("", str(event.get("text") or "")).strip()
        is_dm = event.get("channel_type") == "im" or channel.startswith("D")
        thread_ts = str(event.get("thread_ts") or "")
        if is_dm:
            thread_key, message_kind, chat_type = f"{team}:{channel}:", "dm", "im"
        elif kind == "app_mention":
            thread_key, message_kind, chat_type = f"{team}:{channel}:{thread_ts or ts}", "mention", "channel"
        else:
            if not thread_ts:
                return None  # Ordinary channel chatter is never PANINI's business.
            thread_key, message_kind, chat_type = f"{team}:{channel}:{thread_ts}", "reply", "channel"
            if not self.subscribed(thread_key):  # Raises on unreadable state: nothing runs.
                return None
        if message_kind == "mention" and not self.paused():
            try:
                self.subscribe(thread_key)
            except Exception as error:  # noqa: BLE001 -- still answer the mention (OpenDots).
                safe_failure(error)
        return InboundMessage("slack", self.account["id"], thread_key, f"{channel}:{ts}", str(event["user"]),
                              text, kind=message_kind, human=True, chat_type=chat_type)  # fmt: skip

    async def handle(self, ws, raw, deliver):
        envelope = json.loads(raw)
        kind = envelope.get("type")
        if kind == "hello":
            self.state = "ready"
            return
        if kind == "disconnect":
            raise ConnectionError("Slack asked to reconnect.")
        if envelope.get("envelope_id"):
            await ws.send(
                json.dumps({"envelope_id": envelope["envelope_id"]})
            )  # Ack first: Slack retries otherwise.
        if kind != "events_api":
            return
        message = self.normalize(envelope.get("payload") or {})
        if message is not None:
            await deliver(message)

    async def run(self, deliver):
        backoff = 1
        while True:
            try:
                opened = await self.api("apps.connections.open", {}, token="app_token")
                async with self.connect(opened["url"]) as ws:
                    backoff = 1
                    async for raw in ws:
                        try:
                            await self.handle(ws, raw, deliver)
                        except (SubscriptionError, ValueError) as error:
                            safe_failure(error)
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001 -- provider failures are isolated and redacted.
                self.state = "degraded"
                safe_failure(error)
                await asyncio.sleep(max(1, min(60, getattr(error, "retry_after", None) or backoff)))
                backoff = min(60, backoff * 2)

    async def start(self, deliver):
        if self.task:
            return
        self.state = "connecting"
        self.task = asyncio.create_task(self.run(deliver))

    async def stop(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None
        await self.client.aclose()
        self.state = "off"

    async def send(self, thread_key, message):
        _, channel, thread_ts = (thread_key.split(":", 2) + ["", ""])[:3]
        payload = {"channel": channel, "text": message.text, "unfurl_links": False, "unfurl_media": False}
        if thread_ts:
            payload["thread_ts"] = thread_ts
        return str((await self.api("chat.postMessage", payload))["ts"])

    async def typing(self, thread_key):
        return None  # Slack has no typing indicator for bots.

    async def readiness(self):
        return Readiness(self.state if self.state in {"ready", "degraded"} else "needs_setup", self.state)
