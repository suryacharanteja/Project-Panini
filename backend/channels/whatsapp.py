"""WhatsApp Cloud API (V2-17 §5.5): inbound through the remote fabric's verified ingress, replies via Graph.

The provider calls PANINI's webhook (Tailscale Funnel or the relay); V2-14 ingress checks the GET challenge
(verify token) and every POST's X-Hub-Signature-256 with the app secret before this module sees anything.
One thread per owner number (wa_id). Replies are plain text; business-initiated messages outside WhatsApp's
24-hour service window need an approved template, so replies to the owner's own messages are the only sends.
"""

import json
import secrets

import httpx

from ..core_contracts import Readiness
from .base import InboundMessage, ProviderError, safe_failure

GRAPH = "https://graph.facebook.com/v21.0/"


class WhatsApp:
    name = "whatsapp"

    def __init__(self, account, accounts, *, transport=None, ingress=None):
        self.account, self.accounts = account, accounts
        self.client = httpx.AsyncClient(
            base_url=GRAPH, timeout=30, transport=transport, follow_redirects=False
        )
        self.ingress = ingress  # The fabric's Ingress; set by start() when the fabric is available.
        self.paused = lambda: False
        self.deliver = None
        self.state = "off"

    @property
    def hook_id(self):
        return f"whatsapp-{self.account['id'][:12]}"

    def hook(self):
        """The ingress hook for this account; the path secret is created once and kept with the account."""
        identifier = self.account["id"]
        path_secret = self.accounts.secret(identifier, "path_secret")
        if not path_secret:
            path_secret = secrets.token_urlsafe(24)
            self.accounts.secrets.set(f"channel:{identifier}:path_secret", path_secret)
        return {
            "kind": "whatsapp",
            "path_secret": path_secret,
            "secret": self.accounts.secret(identifier, "app_secret"),
            "verify_token": self.accounts.secret(identifier, "verify_token"),
        }

    async def start(self, deliver):
        self.deliver = deliver
        if self.ingress is None:
            self.state = "needs_setup"  # WhatsApp needs a public webhook: turn on remote access (V2-14).
            return
        self.ingress.hooks[self.hook_id] = self.hook()
        handlers = self.ingress.handlers.setdefault("whatsapp:accounts", {})
        handlers[self.hook_id] = self.received
        self.ingress.on("whatsapp", _dispatch(self.ingress))
        self.state = "ready"

    async def stop(self):
        if self.ingress is not None:
            self.ingress.hooks.pop(self.hook_id, None)
            self.ingress.handlers.get("whatsapp:accounts", {}).pop(self.hook_id, None)
        await self.client.aclose()
        self.state = "off"

    async def received(self, headers, body):
        """A verified webhook delivery: each owner message becomes an InboundMessage."""
        try:
            data = json.loads(body)
        except ValueError:
            return {"status": 200, "body": ""}
        for message in messages(self.account["id"], data):
            try:
                await self.deliver(message)
            except Exception as error:  # noqa: BLE001 -- answer the provider; the manager logged safely.
                safe_failure(error)
        return {"status": 200, "body": ""}

    async def send(self, thread_key, message):
        token = self.accounts.secret(self.account["id"])
        phone = self.account["config"]["phone_number_id"]
        response = await self.client.post(
            f"{phone}/messages",
            json={
                "messaging_product": "whatsapp",
                "to": thread_key,
                "type": "text",
                "text": {"preview_url": False, "body": message.text},
            },  # fmt: skip
            headers={"Authorization": f"Bearer {token}"},
        )
        if response.status_code >= 400:
            raise ProviderError(response.status_code, 60 if response.status_code == 429 else None)
        try:
            return str(response.json()["messages"][0]["id"])
        except (ValueError, KeyError, IndexError) as error:
            raise ProviderError() from error

    async def typing(self, thread_key):
        return None

    async def readiness(self):
        return Readiness(self.state if self.state == "ready" else "needs_setup", self.state)


def messages(account_id, data):
    """Owner text messages in a Cloud API webhook payload (statuses and other changes are ignored)."""
    out = []
    for entry in data.get("entry") or []:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            for item in value.get("messages") or []:
                sender = str(item.get("from") or "")
                if not sender.isdigit() or not item.get("id"):
                    continue
                kind = item.get("type")
                text = (item.get("text") or {}).get("body", "") if kind == "text" else ""
                out.append(InboundMessage(
                    "whatsapp", account_id, sender, str(item["id"]), sender, str(text), kind="dm", human=True,
                    chat_type="private", attachments=() if kind == "text" else ("unsupported",),
                ))  # fmt: skip
    return out


def _dispatch(ingress):
    async def handler(hook_id, headers, body):
        account = ingress.handlers.get("whatsapp:accounts", {}).get(hook_id)
        return await account(headers, body) if account else {"status": 200, "body": ""}

    return handler
