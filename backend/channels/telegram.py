"""Telegram Bot API long polling. Offsets advance only after durable delivery."""

import asyncio
import logging

import httpx

from ..core_contracts import Readiness
from .base import BotUrlFilter, InboundMessage, ProviderError, safe_failure


class Telegram:
    name = "telegram"

    def __init__(self, account, accounts, *, transport=None):
        self.account, self.accounts = account, accounts
        token = accounts.secrets.get(f"channel:{account['id']}:token")
        self.client = httpx.AsyncClient(
            base_url=f"https://api.telegram.org/bot{token}/",
            timeout=60,
            transport=transport,
            follow_redirects=False,
        )
        self.task = None
        self.state = "off"
        if not any(isinstance(f, BotUrlFilter) for f in logging.getLogger("httpx").filters):
            logging.getLogger("httpx").addFilter(BotUrlFilter())

    async def api(self, method, payload):
        response = await self.client.post(method, json=payload)
        if response.status_code >= 400:
            retry = None
            if response.status_code == 429:
                try:
                    retry = int(response.json().get("parameters", {}).get("retry_after", 1))
                except (ValueError, TypeError):
                    retry = 1
            raise ProviderError(response.status_code, retry)
        try:
            body = response.json()
            if not body.get("ok"):
                raise ProviderError(body.get("error_code"))
            return body["result"]
        except (ValueError, KeyError, AttributeError) as error:
            raise ProviderError() from error

    async def start(self, deliver):
        if self.task:
            return
        self.state = "connecting"
        self.task = asyncio.create_task(self.poll(deliver))

    async def stop(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None
        await self.client.aclose()
        self.state = "off"

    async def poll(self, deliver):
        backoff = 1
        while True:
            try:
                await self.poll_once(deliver)
                self.state, backoff = "ready", 1
                await asyncio.sleep(0.1)
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001 -- provider failures are isolated and redacted.
                self.state = "degraded"
                safe_failure(error)
                delay = getattr(error, "retry_after", None) or backoff
                await asyncio.sleep(max(1, min(3600, delay)))
                backoff = min(60, backoff * 2)

    async def poll_once(self, deliver):
        identifier = self.account["id"]
        offset = self.accounts.state(identifier, "offset", 0)
        updates = await self.api(
            "getUpdates", {"offset": offset, "timeout": 50, "allowed_updates": ["message"]}
        )
        if not isinstance(updates, list):
            raise ProviderError()
        for update in sorted(updates, key=lambda row: row["update_id"]):
            message = normalize(identifier, update)
            if message:
                await deliver(message)
            self.accounts.set_state(identifier, "offset", int(update["update_id"]) + 1)

    @staticmethod
    def destination(thread_key):
        chat, _, topic = thread_key.partition(":")
        result = {"chat_id": int(chat)}
        if topic:
            result["message_thread_id"] = int(topic)
        return result

    async def send(self, thread_key, message):
        result = await self.api("sendMessage", {**self.destination(thread_key), "text": message.text})
        return str(result["message_id"])

    async def edit(self, thread_key, message_id, text):
        destination = self.destination(thread_key)
        destination.pop("message_thread_id", None)
        await self.api("editMessageText", {**destination, "message_id": int(message_id), "text": text})

    async def typing(self, thread_key):
        await self.api("sendChatAction", {**self.destination(thread_key), "action": "typing"})

    async def readiness(self):
        return Readiness(self.state if self.state in {"ready", "degraded"} else "needs_setup", self.state)


def normalize(account_id, update):
    message = update.get("message")
    if not isinstance(message, dict) or not isinstance(message.get("from"), dict):
        return None
    sender, chat = message["from"], message.get("chat", {})
    if type(sender.get("id")) is not int or type(chat.get("id")) is not int:
        return None
    if type(message.get("message_id")) is not int:
        return None
    topic = message.get("message_thread_id")
    thread = str(chat["id"]) + (f":{topic}" if type(topic) is int else "")
    text = message.get("text") or message.get("caption") or ""
    if not isinstance(text, str):
        return None
    attachments = (
        ("unsupported",)
        if any(key in message for key in ("photo", "document", "voice", "audio", "video"))
        else ()
    )
    return InboundMessage(
        "telegram",
        account_id,
        thread,
        f"{chat['id']}:{message['message_id']}",
        str(sender["id"]),
        text,
        kind="dm" if chat.get("type") == "private" else "mention",
        human=sender.get("is_bot") is False,
        chat_type=chat.get("type", "unknown"),
        attachments=attachments,
    )
