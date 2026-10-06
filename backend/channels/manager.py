"""Durable intake and reply outbox. No channel text has approval authority."""

import asyncio
import time
from uuid import uuid4

from .. import v2flags
from .accounts import Accounts
from .base import OutboundMessage, safe_failure
from .format import chunks
from .identity import owner
from .identity import permitted as allowed_for


def provider_for(account, accounts):
    """The adapter for an account's channel."""
    from .mail import Email
    from .slack import Slack
    from .telegram import Telegram
    from .whatsapp import WhatsApp

    return {"telegram": Telegram, "slack": Slack, "whatsapp": WhatsApp, "email": Email}[account["channel"]](
        account, accounts
    )


FAILURE = "I couldn't complete that. Check PANINI and try again."
LONG_RESULT = 3000  # Longer verified results become a Space page (V2-19) with a short reply.
APPROVAL = "Approval needed: check PANINI on your PC or phone. Channel messages cannot approve actions."


class ChannelManager:
    def __init__(self, harness, secrets, *, provider_factory=provider_for, clock=time.time):
        self.h, self.store, self.clock = harness, harness.store, clock
        self.accounts = Accounts(self.store, secrets, clock)
        self.provider_factory = provider_factory
        self.providers, self.loop = {}, None
        self._apply_lock = asyncio.Lock()

    def enabled(self, account):
        return account["enabled"] and account["channel"] in v2flags.listed("PANINI_CHANNELS")

    def job(self, task_id):
        return self.store.one("SELECT * FROM channel_jobs WHERE task_id=?", (task_id,))

    def permitted(self, job):
        try:
            account = self.accounts.get(job["account_id"])
        except KeyError:
            return False
        return self.enabled(account) and allowed_for(account, job)

    async def apply(self):
        async with self._apply_lock:
            await self._stop_providers()
            for job in self.store.all("SELECT * FROM channel_jobs WHERE state='pending'"):
                if not self.permitted(job):
                    self.h.tasks.cancel(job["task_id"])
            for account in self.accounts.list():
                if self.enabled(account):
                    provider = self.provider_factory(account, self.accounts)
                    provider.paused = self.h.policy.paused  # Slack does not subscribe threads while paused.
                    fabric = getattr(self.h, "fabric", None)
                    if getattr(provider, "ingress", "absent") is None and fabric is not None:
                        provider.ingress = (
                            fabric.ingress
                        )  # WhatsApp arrives through the verified webhook ingress.
                    self.providers[account["id"]] = provider
                    await provider.start(self.deliver)

    async def start(self):
        if self.loop:
            return
        # A crash after sending may have delivered the reply. Do not blindly send it again.
        self.store.execute(
            "UPDATE channel_outbox SET state='unknown',error_class='InterruptedDelivery' WHERE state='sending'"
        )
        await self.apply()
        self.loop = asyncio.create_task(self.maintain())

    async def _stop_providers(self):
        providers, self.providers = self.providers, {}
        await asyncio.gather(*(provider.stop() for provider in providers.values()), return_exceptions=True)

    async def stop(self):
        if self.loop:
            self.loop.cancel()
            await asyncio.gather(self.loop, return_exceptions=True)
            self.loop = None
        await self._stop_providers()

    async def maintain(self):
        configured = v2flags.value("PANINI_CHANNELS")
        while True:
            try:
                if configured != v2flags.value("PANINI_CHANNELS"):
                    configured = v2flags.value("PANINI_CHANNELS")
                    await self.apply()
                self.reconcile()
                await self.flush()
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001 -- never leak provider details.
                safe_failure(error)
            await asyncio.sleep(1)

    def enqueue(self, account_id, thread_key, text):
        parts = chunks(text)
        total = self.store.one(
            "SELECT COUNT(*) AS n FROM channel_outbox WHERE account_id=? AND state IN ('queued','sending')",
            (account_id,),
        )["n"]
        if total + len(parts) > 200:
            raise ValueError("Channel account reply queue is full.")
        queued = self.store.one(
            "SELECT COUNT(*) AS n FROM channel_outbox WHERE account_id=? AND thread_key=? AND state IN ('queued','sending')",
            (account_id, thread_key),
        )["n"]
        if queued + len(parts) > 20:
            raise ValueError("Channel reply queue is full.")
        identifiers = []
        for part in parts:
            identifier = uuid4().hex
            self.store.execute(
                "INSERT INTO channel_outbox(id,account_id,thread_key,text,available_at,created) VALUES (?,?,?,?,?,?)",
                (identifier, account_id, thread_key, part, self.clock(), self.clock()),
            )
            identifiers.append(identifier)
        return identifiers

    async def deliver(self, message):
        try:
            account = self.accounts.get(message.account)
        except KeyError:
            return
        if not self.enabled(account) or message.channel != account["channel"]:
            return
        if message.kind in {"edit", "delete"} or not message.human:
            return
        # No awaits until identity, dedupe, binding and task creation commit together.
        with self.store.lock:
            self.store.execute("BEGIN IMMEDIATE")
            try:
                inserted = self.store.execute(
                    "INSERT OR IGNORE INTO channel_messages VALUES (?,?,?)",
                    (message.account, message.message_id, self.clock()),
                ).rowcount
                if not inserted:
                    self.store.execute("COMMIT")
                    return
                if not owner(account["config"], message):
                    # Unknown group members receive no response. DMs get at most one notice per day.
                    key = f"unknown:{message.sender_id}"
                    previous = self.accounts.state(message.account, key, 0)
                    if message.chat_type == "private" and self.clock() - previous >= 86400:
                        self.enqueue(message.account, message.thread_key, "I only respond to my owner.")
                        self.accounts.set_state(message.account, key, self.clock())
                elif self.h.policy.paused():
                    self.enqueue(
                        message.account,
                        message.thread_key,
                        "PANINI is paused. Resume it on your PC or phone.",
                    )
                elif message.attachments:
                    self.enqueue(
                        message.account,
                        message.thread_key,
                        "Send a text request. Attachments are not available on channels yet.",
                    )
                elif not message.text.strip() or len(message.text) > 4000:
                    self.enqueue(
                        message.account, message.thread_key, "Send a text request of 1–4,000 characters."
                    )
                elif (
                    self.store.one(
                        "SELECT COUNT(*) AS n FROM channel_jobs WHERE account_id=? AND created>?",
                        (message.account, self.clock() - 60),
                    )["n"]
                    >= 30
                ):
                    pass  # Bound the number of tasks an owner account may create each minute.
                elif self.store.one(
                    "SELECT task_id FROM channel_jobs WHERE account_id=? AND thread_key=? AND state='pending'",
                    (message.account, message.thread_key),
                ):
                    self.enqueue(
                        message.account,
                        message.thread_key,
                        "This conversation is still working or waiting for approval. Check PANINI on your PC or phone.",
                    )
                else:
                    thread_id = self.h.threads.bind(
                        message.channel, f"{message.account}:{message.thread_key}", title=message.text[:80]
                    )
                    task = self.h.tasks.submit(
                        message.text, origin=f"channel:{message.account}", title=message.text[:80]
                    )
                    self.store.execute(
                        "INSERT INTO channel_jobs(task_id,account_id,thread_key,thread_id,sender_id,created) VALUES (?,?,?,?,?,?)",
                        (
                            task["id"],
                            message.account,
                            message.thread_key,
                            thread_id,
                            message.sender_id,
                            self.clock(),
                        ),
                    )
                self.store.execute("DELETE FROM channel_messages WHERE seen<?", (self.clock() - 7 * 86400,))
                self.store.execute("COMMIT")
            except BaseException:
                self.store.execute("ROLLBACK")
                raise
        if owner(account["config"], message) and hasattr(self.h, "continuity") and self.h.continuity.enabled:
            self.h.continuity.presence.signal("channel", {})

    def reconcile(self):
        for job in self.store.all("SELECT * FROM channel_jobs WHERE state='pending'"):
            task = self.h.tasks.get(job["task_id"])
            if task["status"] not in {"completed", "failed", "cancelled", "waiting_input", "needs_review"}:
                continue
            marker = f"notice:{job['task_id']}:{task['status']}"
            if self.accounts.state(job["account_id"], marker, False):
                continue
            result = task.get("result") or {}
            verified = task["status"] == "completed" and result.get("outcome") == "verified_complete"
            text = (
                APPROVAL
                if task["status"] == "waiting_input"
                else (
                    result.get("message") or "The task finished. Check PANINI for the result."
                    if verified
                    else FAILURE
                )
            )
            if verified and len(text) > LONG_RESULT:
                text = self.long_result(job, text)
            with self.store.lock:
                self.store.execute("BEGIN IMMEDIATE")
                try:
                    self.enqueue(job["account_id"], job["thread_key"], text)
                    self.accounts.set_state(job["account_id"], marker, True)
                    if task["status"] != "waiting_input":
                        self.store.execute(
                            "UPDATE channel_jobs SET state='finished' WHERE task_id=?", (job["task_id"],)
                        )
                    self.store.execute("COMMIT")
                except BaseException:
                    self.store.execute("ROLLBACK")
                    raise

    def long_result(self, job, text):
        """Save a long verified result as a page in the owner's default Space and reply with a summary of it.
        Without Spaces or a default Space, the full text is sent in chunks as before."""
        if not v2flags.enabled("PANINI_SPACES") or not hasattr(self.h, "spaces"):
            return text
        try:
            persona = self.h.personas.get("panini") if hasattr(self.h, "personas") else {}
            space_id = (persona or {}).get("default_space_id")
            if not space_id:
                return text
            # Idempotent: a retried reconcile reuses the page saved for this task.
            key = f"page:{job['task_id']}"
            saved = self.accounts.state(job["account_id"], key, None)
            if saved:
                page = self.h.spaces.read(saved)
            else:
                task = self.h.tasks.get(job["task_id"])
                body = f"{text}\n\nSource: channel task `{job['task_id']}` ({task.get('title', '')})"
                page = self.h.spaces.create(
                    space_id,
                    (task.get("title") or "Channel result")[:240],
                    body,
                    source_thread_id=job["thread_id"],
                )
                self.accounts.set_state(job["account_id"], key, page["id"])
        except Exception as error:  # noqa: BLE001 -- the reply still goes out in full.
            safe_failure(error)
            return text
        summary = text[:600].rsplit(" ", 1)[0]
        title = page.get("title", "Result")
        return "\n\n".join([summary + "…", f'The full result is saved in PANINI as the page "{title}".'])

    async def flush(self):
        # One delivery per account/thread per tick; a bounded durable queue survives restart.
        rows = self.store.all("SELECT * FROM channel_outbox WHERE state='queued' ORDER BY created,rowid")
        seen = set()
        sent_per_account = {}
        for row in rows:
            key = (row["account_id"], row["thread_key"])
            if key in seen or row["account_id"] not in self.providers:
                continue
            seen.add(key)
            if row["available_at"] > self.clock():
                continue
            if sent_per_account.get(row["account_id"], 0) >= 30:
                continue
            sent_per_account[row["account_id"]] = sent_per_account.get(row["account_id"], 0) + 1
            account = self.accounts.get(row["account_id"])
            if not self.enabled(account):
                continue
            latest_job = self.store.one(
                "SELECT * FROM channel_jobs WHERE account_id=? AND thread_key=? ORDER BY created DESC,rowid DESC LIMIT 1",
                (row["account_id"], row["thread_key"]),
            )
            if latest_job and not self.permitted(latest_job):
                self.store.execute(
                    "UPDATE channel_outbox SET state='cancelled',error_class='AccessRemoved' WHERE id=?",
                    (row["id"],),
                )
                continue
            self.store.execute("UPDATE channel_outbox SET state='sending' WHERE id=?", (row["id"],))
            try:
                identifier = await self.providers[row["account_id"]].send(
                    row["thread_key"], OutboundMessage(row["text"])
                )
                self.store.execute(
                    "UPDATE channel_outbox SET state='sent',provider_id=? WHERE id=?", (identifier, row["id"])
                )
            except asyncio.CancelledError:
                self.store.execute(
                    "UPDATE channel_outbox SET state='unknown',error_class='CancelledDelivery' WHERE id=?",
                    (row["id"],),
                )
                raise
            except Exception as error:  # noqa: BLE001 -- only explicit rejection is safe to retry.
                failure = safe_failure(error)
                retry = getattr(error, "retry_after", None)
                if getattr(error, "status", None) == 429:
                    self.store.execute(
                        "UPDATE channel_outbox SET state='queued',available_at=?,error_class=? WHERE id=?",
                        (self.clock() + max(1, min(86400, retry or 1)), failure["error_class"], row["id"]),
                    )
                else:
                    self.store.execute(
                        "UPDATE channel_outbox SET state='unknown',error_class=? WHERE id=?",
                        (failure["error_class"], row["id"]),
                    )

    def status(self):
        return {
            "enabled_channels": sorted(v2flags.listed("PANINI_CHANNELS")),
            "implemented": ["telegram", "slack", "whatsapp", "email"],
            "accounts": [
                {
                    **account,
                    "state": self.providers[account["id"]].state
                    if account["id"] in self.providers
                    else "off",
                }
                for account in self.accounts.list()
            ],
            "outbox": self.store.all(
                "SELECT id,account_id,state,error_class,created FROM channel_outbox ORDER BY created DESC LIMIT 50"
            ),
        }
