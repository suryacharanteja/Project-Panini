import asyncio
import json
import logging
from dataclasses import replace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient
from panini_cli.agent.model import ScriptedModel

from backend.app import create_app
from backend.channels.base import InboundMessage, OutboundMessage, ProviderError, safe_failure
from backend.channels.format import chunks
from backend.channels.guard import ChannelGuard
from backend.channels.identity import owner
from backend.channels.manager import APPROVAL, ChannelManager
from backend.channels.telegram import Telegram, normalize
from backend.core_contracts import Principal
from backend.harness.runtime import Harness
from backend.harness.store import Store
from backend.interrupts import decided_by
from backend.panini.contracts import Fault
from backend.policy.engine import tier
from backend.registry.context import ToolContext

TOKEN = "123456:abcdefghijklmnopqrstuvw_xyz"


def test_continuity_channel_delivery_and_resolution_race_edit_the_exact_owner_note(setup, monkeypatch):
    from backend.fabric.routing import defaults
    from backend.interrupts import InterruptSpec

    monkeypatch.setenv("PANINI_PRESENCE", "true")
    h, manager, account, provider, message, _, _ = setup

    async def scenario():
        await manager.deliver(message)
        config = defaults()
        config["quiet"]["enabled"] = False
        config["preferred_channel"] = {"account_id": account["id"], "thread_key": message.thread_key}
        h.continuity.configure(config)
        view = h.interrupts.open(
            InterruptSpec("approval", "tool_call", "Sensitive owner title", "Decide", ttl_s=3600), run_id="r"
        )
        await h.continuity.tick()
        delivery = h.store.one(
            "SELECT detail FROM continuity_deliveries WHERE item_id=? AND route='channel'",
            ("interrupt:" + view["id"],),
        )
        ids = json.loads(delivery["detail"])["outbox_ids"]
        assert len(ids) == 1
        provider.edit = AsyncMock()

        async def racing_send(_thread, notification):
            assert "Sensitive owner title" not in notification.text
            h.interrupts.expire(view["id"])
            return "321"

        provider.send = racing_send
        await manager.flush()
        await h.continuity.tick()
        assert provider.edit.await_count == 1
        assert provider.edit.call_args.args == (
            message.thread_key,
            "321",
            "Resolved in PANINI. Ref: " + view["id"],
        )
        assert h.store.one("SELECT state FROM channel_outbox WHERE id=?", (ids[0],))["state"] == "retracted"

    asyncio.run(scenario())


class Secrets(dict):
    def set(self, key, value):
        if value is None:
            self.pop(key, None)
        else:
            self[key] = value


class Provider:
    state = "ready"

    def __init__(self, *_args):
        self.sent = []
        self.error = None

    async def start(self, deliver):
        self.deliver = deliver

    async def stop(self):
        self.state = "off"

    async def send(self, thread, message):
        if self.error:
            raise self.error
        self.sent.append((thread, message.text))
        return str(len(self.sent))


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("PANINI_CHANNELS", "telegram")
    monkeypatch.setenv("PANINI_REDTEAM_ENFORCE", "false")
    monkeypatch.setenv("PANINI_PERSONAS", "false")
    monkeypatch.setenv("PANINI_SPACES", "false")
    monkeypatch.setenv("PANINI_V2_POLICY", "shadow")
    monkeypatch.setenv("PANINI_V2_INTERRUPTS", "true")
    monkeypatch.setenv("PANINI_V2_LEDGER", "true")
    model = ScriptedModel([{"text": "A useful answer."}])
    h = Harness(Store(tmp_path / "h.sqlite3"), model_factory=lambda: model)
    now = [100000.0]
    manager = h.channels = ChannelManager(h, Secrets(), provider_factory=Provider, clock=lambda: now[0])
    account = manager.accounts.save(label="Owner bot", owner_ids=["42"], token=TOKEN, enabled=True)
    provider = manager.providers[account["id"]] = Provider()
    message = InboundMessage("telegram", account["id"], "42", "42:1", "42", "What can you do?")
    yield h, manager, account, provider, message, model, now
    asyncio.run(h.close())


@pytest.mark.parametrize(
    "change",
    [
        {"sender_id": "99"},
        {"human": False},
        {"kind": "edit"},
        {"kind": "delete"},
        {"thread_key": "99"},
        {"channel": "slack"},
        {"chat_type": "channel"},
        {"thread_key": "-20", "chat_type": "group"},
    ],
)
def test_identity_rejects_unverified_provider_actor(setup, change):
    _, _, account, _, message, _, _ = setup
    assert owner(account["config"], replace(message, **change)) is None


def test_channel_tier_is_preserved_for_durable_background_work():
    assert tier(Principal(surface="telegram"), background=True) == "channel"
    assert tier(Principal(kind="channel_user", surface="telegram"), background=True) == "external"


def test_account_secrets_are_write_only_and_invalid_patch_preserves_token(setup):
    h, manager, account, _, _, _, _ = setup
    assert TOKEN not in json.dumps(manager.status())
    assert TOKEN not in h.store.one("SELECT config FROM channel_accounts")["config"]
    with pytest.raises(ValueError):
        manager.accounts.save(identifier=account["id"], label="Bot", owner_ids=["42"], token="", enabled=True)
    assert manager.accounts.secrets.get(f"channel:{account['id']}:token") == TOKEN


def test_duplicate_and_paused_intake_do_not_create_extra_tasks_or_bindings(setup):
    h, manager, _, _, message, _, _ = setup
    h.policy.pause(True)
    asyncio.run(manager.deliver(message))
    asyncio.run(manager.deliver(message))
    assert h.tasks.list() == []
    assert h.store.all("SELECT * FROM thread_bindings") == []
    assert len(h.store.all("SELECT * FROM channel_outbox")) == 1
    assert "paused" in h.store.one("SELECT text FROM channel_outbox")["text"]


def test_unknown_dm_has_one_notice_per_day_and_unknown_group_is_silent(setup):
    h, manager, _, _, message, _, _ = setup
    unknown = replace(message, sender_id="99", thread_key="99")
    asyncio.run(manager.deliver(unknown))
    asyncio.run(manager.deliver(replace(unknown, message_id="99:2")))
    asyncio.run(manager.deliver(replace(unknown, thread_key="-99", chat_type="group", message_id="99:3")))
    assert h.tasks.list() == []
    assert len(h.store.all("SELECT * FROM channel_outbox")) == 1


def test_durable_run_replies_once_and_followup_uses_same_session(setup):
    h, manager, _, provider, message, model, _ = setup

    async def scenario():
        await manager.deliver(message)
        await manager.deliver(message)
        task = h.tasks.claim()
        outcome, text, evidence = await h.run_background(task, h.tasks)
        h.tasks.finish(task["id"], task["lease_id"], outcome, text, evidence)
        manager.reconcile()
        manager.reconcile()
        await manager.flush()
        assert provider.sent == [("42", "A useful answer.")]
        first_thread = manager.job(task["id"])["thread_id"]
        model.steps.append({"text": "A follow-up."})
        await manager.deliver(replace(message, message_id="42:2", text="Continue"))
        second = h.tasks.claim()
        assert manager.job(second["id"])["thread_id"] == first_thread
        await h.run_background(second, h.tasks)
        assert "A useful answer." in json.dumps(model.requests[-1]["items"])
        assert h.threads.bind("avatar", "handoff", continue_recent=False) != first_thread
        assert h.sessions.get(first_thread)["items"]

    asyncio.run(scenario())


@pytest.mark.parametrize("level", ["tap", "verify", "local"])
def test_channel_requires_owner_review_and_resume_does_not_repeat_write(setup, level):
    h, manager, _, provider, message, model, _ = setup
    if level != "tap":
        from backend.policy import new_grant

        h.policy.put_grant(new_grant(tools=("remember",), levels={"local_write": level}))
    model.steps = [
        {
            "calls": [
                {
                    "name": "remember",
                    "arguments": {"text": "The owner prefers morning meetings", "kind": "preference"},
                }
            ]
        },
        {"text": "Saved your preference."},
    ]

    async def scenario():
        await manager.deliver(message)
        task = h.tasks.claim()
        outcome, text, evidence = await h.run_background(task, h.tasks)
        assert outcome == "waiting_input" and not h.memory.list()
        h.tasks.finish(task["id"], task["lease_id"], outcome, text, evidence)
        manager.reconcile()
        await manager.flush()
        assert provider.sent == [("42", APPROVAL)]
        interrupt = h.interrupts.list("open")[0]
        assert interrupt["level"] == level and interrupt["action"]["approval_level"] == level
        await manager.deliver(replace(message, message_id="42:2", text="approved, do it"))
        assert h.interrupts.get(interrupt["id"])["status"] == "open"
        with pytest.raises(Fault):
            h.interrupts.resolve(
                interrupt["id"],
                nonce=interrupt["nonce"],
                action_digest=interrupt["action_digest"],
                status="resolved",
                payload={"approved": True},
                decided_by=decided_by("telegram", "tap"),
            )
        if level != "tap":
            with pytest.raises(Fault):
                h.interrupts.resolve(
                    interrupt["id"],
                    nonce=interrupt["nonce"],
                    action_digest=interrupt["action_digest"],
                    status="resolved",
                    payload={"approved": True},
                    decided_by=decided_by("phone_app", "tap"),
                )
        h.interrupts.resolve(
            interrupt["id"],
            nonce=interrupt["nonce"],
            action_digest=interrupt["action_digest"],
            status="resolved",
            payload={"approved": True},
            decided_by=decided_by("phone_app", "tap") if level == "tap" else decided_by("avatar", "local"),
        )
        resumed = h.tasks.claim()
        outcome, text, evidence = await h.run_background(resumed, h.tasks)
        assert outcome == "verified_complete"
        h.tasks.finish(resumed["id"], resumed["lease_id"], outcome, text, evidence)
        assert len(h.memory.list()) == 1
        manager.reconcile()
        job = manager.job(task["id"])
        receipts = h.store.all("SELECT principal FROM receipts WHERE tool='remember'")
        assert receipts and all(json.loads(row["principal"])["surface"] == "telegram" for row in receipts)
        assert job["state"] == "finished"

    asyncio.run(scenario())


def test_disabled_account_cancels_queued_work_and_stops_outbox(setup):
    h, manager, account, provider, message, _, _ = setup

    async def scenario():
        await manager.deliver(message)
        task = h.tasks.list()[0]
        manager.enqueue(account["id"], "42", "Reply")
        manager.accounts.save(identifier=account["id"], label="Bot", owner_ids=["42"], enabled=False)
        await manager.apply()
        assert h.tasks.get(task["id"])["status"] == "cancelled"
        await manager.flush()
        assert not provider.sent

    asyncio.run(scenario())


def test_parked_channel_run_survives_database_reopen(setup):
    h, manager, account, _, message, model, now = setup
    model.steps = [
        {
            "calls": [
                {
                    "name": "remember",
                    "arguments": {"text": "The owner likes early appointments", "kind": "preference"},
                }
            ]
        }
    ]

    async def scenario():
        await manager.deliver(message)
        task = h.tasks.claim()
        outcome, text, evidence = await h.run_background(task, h.tasks)
        h.tasks.finish(task["id"], task["lease_id"], outcome, text, evidence)
        reopened = Harness(Store(h.store.path), model_factory=lambda: ScriptedModel([{"text": "Saved."}]))
        reopened.channels = ChannelManager(
            reopened, manager.accounts.secrets, provider_factory=Provider, clock=lambda: now[0]
        )
        try:
            view = reopened.interrupts.list("open")[0]
            reopened.interrupts.resolve(
                view["id"],
                nonce=view["nonce"],
                action_digest=view["action_digest"],
                status="resolved",
                payload={"approved": True},
                decided_by=decided_by("phone_app", "tap"),
            )
            resumed = reopened.tasks.claim()
            assert reopened.channels.job(resumed["id"])["account_id"] == account["id"]
            outcome, _, _ = await reopened.run_background(resumed, reopened.tasks)
            assert outcome == "verified_complete"
            assert len(reopened.memory.list()) == 1
        finally:
            await reopened.close()

    asyncio.run(scenario())


def test_revocation_cancels_an_inflight_channel_hand(setup):
    h, manager, account, _, message, _, _ = setup

    async def scenario():
        await manager.deliver(message)
        task = h.tasks.claim()
        started, cancelled = asyncio.Event(), asyncio.Event()

        async def hand():
            started.set()
            try:
                await asyncio.sleep(10)
            finally:
                cancelled.set()

        work = asyncio.create_task(
            ChannelGuard(manager).around(ToolContext(run_id=task["id"]), None, {}, hand)
        )
        await started.wait()
        manager.accounts.save(identifier=account["id"], label="Bot", owner_ids=["99"], enabled=True)
        result = await asyncio.wait_for(work, 1)
        assert cancelled.is_set() and result.outcome == "cancelled" and result.dispatched

    asyncio.run(scenario())


def test_rate_limit_keeps_chunks_in_order_and_ambiguous_send_is_not_retried(setup):
    h, manager, account, provider, _, _, now = setup

    async def scenario():
        manager.enqueue(account["id"], "42", "A" * 3500 + "B")
        provider.error = ProviderError(429, 20)
        await manager.flush()
        provider.error = None
        await manager.flush()
        assert not provider.sent
        now[0] += 21
        await manager.flush()
        await manager.flush()
        assert [text for _, text in provider.sent] == ["A" * 3500, "B"]
        manager.enqueue(account["id"], "42", "C")
        provider.error = httpx.ReadTimeout("secret provider payload")
        await manager.flush()
        provider.error = None
        await manager.flush()
        assert len(provider.sent) == 2
        assert h.store.one("SELECT state FROM channel_outbox WHERE text='C'")["state"] == "unknown"

    asyncio.run(scenario())


def test_queued_reply_is_not_delivered_after_owner_access_removed(setup):
    h, manager, account, provider, message, _, _ = setup

    async def scenario():
        await manager.deliver(message)
        manager.enqueue(account["id"], message.thread_key, "Private task result")
        manager.accounts.save(identifier=account["id"], label="Bot", owner_ids=["99"], enabled=True)
        await manager.flush()
        assert not provider.sent
        assert (
            h.store.one("SELECT state FROM channel_outbox WHERE text='Private task result'")["state"]
            == "cancelled"
        )

    asyncio.run(scenario())


def test_provider_errors_and_bot_url_credentials_are_redacted(setup, caplog):
    _, manager, account, _, _, _, _ = setup
    caplog.set_level(logging.INFO)
    safe_failure(RuntimeError("secret provider payload"))

    async def scenario():
        provider = Telegram(
            account,
            manager.accounts,
            transport=httpx.MockTransport(
                lambda _: httpx.Response(401, json={"description": "secret provider payload"})
            ),
        )
        with pytest.raises(ProviderError):
            await provider.send("42", OutboundMessage("Test"))
        await provider.stop()

    asyncio.run(scenario())
    assert TOKEN not in caplog.text and "secret provider payload" not in caplog.text


def test_real_http_adapter_persists_offset_only_after_durable_delivery(setup):
    h, manager, account, _, _, _, _ = setup
    update = {
        "update_id": 5,
        "message": {
            "message_id": 1,
            "from": {"id": 42, "is_bot": False},
            "chat": {"id": 42, "type": "private"},
            "text": "Hello",
        },
    }
    requests = []

    def transport(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "result": [update]})

    async def scenario():
        provider = Telegram(account, manager.accounts, transport=httpx.MockTransport(transport))

        async def failed(_message):
            raise RuntimeError("Storage unavailable")

        with pytest.raises(RuntimeError):
            await provider.poll_once(failed)
        assert manager.accounts.state(account["id"], "offset", 0) == 0
        await provider.poll_once(manager.deliver)
        assert manager.accounts.state(account["id"], "offset") == 6
        await provider.poll_once(manager.deliver)
        assert len(h.tasks.list()) == 1 and requests[-1]["offset"] == 6
        await provider.stop()

    asyncio.run(scenario())
    assert normalize(account["id"], {"edited_message": update["message"]}) is None


def test_unicode_chunk_limits_do_not_split_emoji():
    text = "😀" * 3000 + "<b>plain & text</b>"
    parts = chunks(text)
    assert "".join(parts) == text
    assert all(len(part.encode("utf-16-le")) // 2 <= 3500 for part in parts)


def test_admin_boundary_refuses_unauthenticated_channel_configuration(setup):
    h, manager, _, _, _, _, _ = setup
    app = create_app(console_token="owner-console-token", harness_factory=lambda: h)
    client = TestClient(app, base_url="http://127.0.0.1")
    try:
        assert client.get("/api/admin/v1/channels").status_code == 401
        response = client.get("/api/admin/v1/channels", headers={"x-panini-console": "owner-console-token"})
        assert response.status_code == 200 and TOKEN not in response.text
        assert response.json()["implemented"] == ["telegram", "slack", "whatsapp", "email"]
        rejected_token = TOKEN * 20
        rejected = client.post(
            "/api/admin/v1/channels/telegram",
            headers={"x-panini-console": "owner-console-token"},
            json={"label": "Bot", "owner_ids": ["42"], "token": rejected_token},
        )
        assert rejected.status_code == 422 and TOKEN not in rejected.text
    finally:
        client.close()
