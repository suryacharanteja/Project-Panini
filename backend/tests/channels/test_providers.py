"""V2-17 Slack, WhatsApp and email.

Slack cases are ported from OpenDots `tests/slack-channel.test.ts`: explicit tenant and user allow-lists; an allowed
mention answered once, then only subscribed follow-ups; non-human actors and changed/deleted messages ignored;
pause reported without subscribing or running; a failed run isolated behind a safe reply; mentions answered when
subscribing fails, but never a run on unreadable subscription state; a failed notice surfaced safely.
"""

import asyncio
import base64
import hashlib
import hmac
import json
import logging
from email.message import EmailMessage

import httpx
import pytest
from panini_cli.agent.model import ScriptedModel

from backend.channels.base import OutboundMessage, ProviderError
from backend.channels.identity import owner
from backend.channels.mail import Email, authenticated, automated, root_of
from backend.channels.manager import FAILURE, ChannelManager
from backend.channels.slack import Slack, SubscriptionError
from backend.channels.whatsapp import WhatsApp, messages
from backend.fabric.ingress import Ingress
from backend.harness.runtime import Harness
from backend.harness.store import Store

BOT, APP = "xoxb-" + "1" * 24, "xapp-" + "2" * 24


class Secrets(dict):
    def set(self, key, value):
        if value is None:
            self.pop(key, None)
        else:
            self[key] = value


class Recorder:
    """A provider that records sends; the real adapters are tested separately below."""

    def __init__(self, *_):
        self.sent, self.error, self.state = [], None, "ready"

    async def start(self, deliver):
        self.deliver = deliver

    async def stop(self):
        pass

    async def send(self, thread, message):
        if self.error:
            raise self.error
        self.sent.append((thread, message.text))
        return str(len(self.sent))


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("PANINI_CHANNELS", "slack,whatsapp,email")
    for flag, value in {"PANINI_PERSONAS": "false", "PANINI_SPACES": "false", "PANINI_V2_POLICY": "shadow",
                        "PANINI_V2_INTERRUPTS": "true", "PANINI_V2_LEDGER": "true"}.items():  # fmt: skip
        monkeypatch.setenv(flag, value)
    model = ScriptedModel([{"text": "Done."}])
    h = Harness(Store(tmp_path / "h.sqlite3"), model_factory=lambda: model)
    manager = h.channels = ChannelManager(h, Secrets(), provider_factory=Recorder, clock=lambda: 100000.0)
    yield h, manager
    asyncio.run(h.close())


def slack_account(manager, **change):
    values = dict(channel="slack", label="Workspace", owner_ids=["UOWNER01"], group_ids=["CTEAM001"],
                  token=BOT, secrets={"app_token": APP}, settings={"team_id": "TTEAM001"}, enabled=True)  # fmt: skip
    return manager.accounts.save(**{**values, **change})


def slack_event(kind="app_mention", **event):
    base = {"type": kind, "user": "UOWNER01", "text": "<@UBOT0001> help", "ts": "1.1", "channel": "CTEAM001"}
    return {"team_id": "TTEAM001", "event": {**base, **event}}


def adapter(manager, account):
    slack = Slack(account, manager.accounts)
    slack.paused = manager.h.policy.paused
    return slack


# Accounts -----------------------------------------------------------------------------------------
def test_each_channel_validates_its_own_ids_settings_and_write_only_secrets(world):
    _, manager = world
    account = slack_account(manager)
    assert account["token_configured"] and account["secrets_configured"] == {"app_token": True}
    assert BOT not in json.dumps(account) and APP not in json.dumps(account)
    with pytest.raises(ValueError, match="member IDs"):
        slack_account(manager, owner_ids=["owner"])
    with pytest.raises(ValueError, match="workspace ID"):
        slack_account(manager, settings={})
    with pytest.raises(ValueError, match="every credential"):
        manager.accounts.save(channel="whatsapp", label="WA", owner_ids=["+447700900001"], token="t" * 30,
                              settings={"phone_number_id": "12345"}, enabled=True)  # fmt: skip
    wa = manager.accounts.save(channel="whatsapp", label="WA", owner_ids=["+447700900001"], token="t" * 30,
                               settings={"phone_number_id": "12345"},
                               secrets={"app_secret": "a" * 32, "verify_token": "v" * 20}, enabled=True)  # fmt: skip
    assert wa["config"]["owner_ids"] == ["447700900001"]
    with pytest.raises(ValueError, match="cannot change channel"):
        manager.accounts.save(identifier=wa["id"], channel="slack", label="X", owner_ids=["UOWNER01"],
                              settings={"team_id": "TTEAM001"})  # fmt: skip


# Slack (ported from OpenDots) -------------------------------------------------------------------
def test_slack_requires_the_exact_workspace_and_member_ids(world):
    _, manager = world
    account = slack_account(manager)
    slack = adapter(manager, account)
    message = slack.normalize(slack_event())
    assert owner(account["config"], message).surface == "slack"
    other_team = slack.normalize({**slack_event(), "team_id": "TOTHER01"})
    assert owner(account["config"], other_team) is None
    assert owner(account["config"], slack.normalize(slack_event(user="USTRANGER"))) is None
    assert owner(account["config"], slack.normalize(slack_event(channel="COTHER01"))) is None


def test_slack_answers_a_mention_once_then_only_subscribed_follow_ups(world):
    h, manager = world
    account = slack_account(manager)
    slack = adapter(manager, account)

    async def scenario():
        await manager.deliver(slack.normalize(slack_event()))
        await manager.deliver(slack.normalize(slack_event()))  # The same event again: deduplicated.
        follow = slack.normalize(slack_event("message", text="and then?", ts="1.2", thread_ts="1.1"))
        unsubscribed = slack.normalize(slack_event("message", text="hi", ts="9.2", thread_ts="9.1"))
        return follow, unsubscribed

    follow, unsubscribed = asyncio.run(scenario())
    assert len(h.store.all("SELECT * FROM channel_jobs")) == 1
    assert follow is not None and follow.kind == "reply" and unsubscribed is None
    assert slack.normalize(slack_event("message", text="chatter", ts="3.3")) is None  # Not in a thread.


def test_slack_ignores_bots_apps_and_changed_or_deleted_messages(world):
    _, manager = world
    slack = adapter(manager, slack_account(manager))
    assert slack.normalize(slack_event(bot_id="B1")) is None
    assert slack.normalize(slack_event(subtype="bot_message")) is None
    assert slack.normalize(slack_event(user=None)) is None
    for subtype in ("message_changed", "message_deleted"):
        assert slack.normalize(slack_event("message", subtype=subtype)).kind == "edit"


def test_slack_reports_pause_without_subscribing_or_running(world):
    h, manager = world
    account = slack_account(manager)
    slack = adapter(manager, account)
    h.policy.pause(True)
    message = slack.normalize(slack_event())
    asyncio.run(manager.deliver(message))
    assert manager.accounts.state(account["id"], f"sub:{message.thread_key}", False) is False
    assert h.store.all("SELECT * FROM channel_jobs") == []
    assert "paused" in h.store.one("SELECT text FROM channel_outbox")["text"]


def test_slack_isolates_a_failed_run_behind_a_safe_reply(world):
    h, manager = world
    account = slack_account(manager)
    slack = adapter(manager, account)
    asyncio.run(manager.deliver(slack.normalize(slack_event())))
    task = h.tasks.claim()
    h.tasks.finish(task["id"], task["lease_id"], "failed", "SECRET provider details xoxb-123", [])
    manager.reconcile()
    reply = h.store.one("SELECT text FROM channel_outbox")["text"]
    assert reply == FAILURE and "SECRET" not in reply


def test_slack_answers_mentions_when_subscribing_fails_but_never_runs_on_unreadable_state(world, monkeypatch):
    h, manager = world
    account = slack_account(manager)
    slack = adapter(manager, account)
    monkeypatch.setattr(slack, "subscribe", lambda key: (_ for _ in ()).throw(RuntimeError("Failure")))
    asyncio.run(manager.deliver(slack.normalize(slack_event())))
    assert len(h.store.all("SELECT * FROM channel_jobs")) == 1  # Answered anyway.
    monkeypatch.setattr(manager.accounts, "state", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db")))
    with pytest.raises(SubscriptionError):
        slack.normalize(slack_event("message", ts="1.5", thread_ts="1.1"))


def test_a_failed_notice_is_surfaced_safely_and_never_runs(world, caplog):
    h, manager = world
    account = slack_account(manager)
    provider = manager.providers[account["id"]] = Recorder()
    provider.error = ProviderError(500)
    h.policy.pause(True)
    caplog.set_level(logging.WARNING)
    asyncio.run(manager.deliver(adapter(manager, account).normalize(slack_event())))
    asyncio.run(manager.flush())
    assert h.store.all("SELECT * FROM channel_jobs") == []
    assert h.store.one("SELECT state, error_class FROM channel_outbox") == {
        "state": "unknown",
        "error_class": "ProviderError",
    }
    assert "SECRET" not in caplog.text and BOT not in caplog.text


def test_slack_socket_mode_acks_every_envelope_before_work_and_posts_in_thread(world):
    _, manager = world
    account = slack_account(manager)
    calls = []

    def api(request):
        calls.append(
            (request.url.path, request.headers["authorization"], json.loads(request.content or b"{}"))
        )
        if request.url.path.endswith("apps.connections.open"):
            return httpx.Response(200, json={"ok": True, "url": "wss://example.invalid/socket"})
        return httpx.Response(200, json={"ok": True, "ts": "2.2"})

    order = []

    class Socket:
        def __init__(self):
            self.frames = [json.dumps({"type": "hello"}),
                           json.dumps({"type": "events_api", "envelope_id": "E1", "payload": slack_event()})]  # fmt: skip

        async def send(self, data):
            order.append(("ack", json.loads(data)["envelope_id"]))

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def __aiter__(self):
            return self

        async def __anext__(self):
            if not self.frames:
                raise asyncio.CancelledError()
            return self.frames.pop(0)

    slack = Slack(account, manager.accounts, transport=httpx.MockTransport(api), connect=lambda url: Socket())

    async def deliver(message):
        order.append(("deliver", message.thread_key))

    async def scenario():
        with pytest.raises(asyncio.CancelledError):
            await slack.run(deliver)
        return await slack.send("TTEAM001:CTEAM001:1.1", OutboundMessage("Hello"))

    assert asyncio.run(scenario()) == "2.2"
    assert order == [("ack", "E1"), ("deliver", "TTEAM001:CTEAM001:1.1")] and slack.state == "ready"
    assert calls[0][1] == f"Bearer {APP}" and calls[1][1] == f"Bearer {BOT}"
    assert calls[1][2] == {"channel": "CTEAM001", "text": "Hello", "unfurl_links": False, "unfurl_media": False,
                           "thread_ts": "1.1"}  # fmt: skip


# WhatsApp -----------------------------------------------------------------------------------------
def test_whatsapp_messages_arrive_through_verified_ingress_and_replies_use_graph(world):
    h, manager = world
    account = manager.accounts.save(channel="whatsapp", label="WA", owner_ids=["447700900001"], token="t" * 30,
                                    settings={"phone_number_id": "12345"},
                                    secrets={"app_secret": "a" * 32, "verify_token": "v" * 20}, enabled=True)  # fmt: skip
    ingress = Ingress(h.store, {})
    sent = []

    def graph(request):
        sent.append((request.url.path, request.headers["authorization"], json.loads(request.content)))
        return httpx.Response(200, json={"messages": [{"id": "wamid.1"}]})

    wa = WhatsApp(account, manager.accounts, transport=httpx.MockTransport(graph), ingress=ingress)
    payload = {"entry": [{"changes": [{"value": {"messages": [
        {"from": "447700900001", "id": "wamid.in1", "type": "text", "text": {"body": "Book a table"}}]}}]}]}  # fmt: skip
    body = json.dumps(payload).encode()

    async def scenario():
        await wa.start(manager.deliver)
        hook = ingress.hooks[wa.hook_id]
        challenge = await ingress({"method": "GET", "path": f"/{wa.hook_id}/{hook['path_secret']}",
                                   "query": f"hub.mode=subscribe&hub.verify_token={'v' * 20}&hub.challenge=77"})  # fmt: skip
        forged = await ingress({"method": "POST", "path": f"/{wa.hook_id}/{hook['path_secret']}",
                                "headers": {"X-Hub-Signature-256": "sha256=00"}, "body": base64.b64encode(body).decode()})  # fmt: skip
        signature = "sha256=" + hmac.new(b"a" * 32, body, hashlib.sha256).hexdigest()
        real = await ingress({"method": "POST", "path": f"/{wa.hook_id}/{hook['path_secret']}",
                              "headers": {"X-Hub-Signature-256": signature}, "body": base64.b64encode(body).decode()})  # fmt: skip
        reply = await wa.send("447700900001", OutboundMessage("Booked."))
        await wa.stop()
        return challenge, forged, real, reply

    challenge, forged, real, reply = asyncio.run(scenario())
    assert challenge == {"status": 200, "body": "77"} and forged["status"] == 401 and real["status"] == 200
    assert [j["thread_key"] for j in h.store.all("SELECT thread_key FROM channel_jobs")] == ["447700900001"]
    assert (
        reply == "wamid.1" and sent[0][0].endswith("/12345/messages") and sent[0][2]["to"] == "447700900001"
    )
    assert messages("a", {"entry": [{"changes": [{"value": {"statuses": [{"id": "x"}]}}]}]}) == []


# Email ---------------------------------------------------------------------------------------------
def mail(sender="owner@example.com", auth="mx.example.net; dkim=pass header.d=example.com", **headers):
    message = EmailMessage()
    message["From"] = sender
    message["To"] = "panini@example.org"
    message["Subject"] = "Plan"
    message["Message-ID"] = headers.pop("message_id", "<m1@example.com>")
    if auth:
        message["Authentication-Results"] = auth
    for key, value in headers.items():
        message[key.replace("_", "-")] = value
    message.set_content("Plan my week.\n\nOn Mon, someone wrote:\n> old text")
    return message


def email_account(manager):
    return manager.accounts.save(channel="email", label="Mail", owner_ids=["Owner@Example.com"], token="pw-1234",
                                 settings={"address": "panini@example.org", "username": "panini",
                                           "imap_host": "imap.example.org", "smtp_host": "smtp.example.org"},
                                 enabled=True)  # fmt: skip


def test_email_owner_needs_aligned_authentication_and_auto_mail_is_never_answered(world):
    _, manager = world
    account = email_account(manager)
    box = Email(account, manager.accounts)
    good = box.normalize(mail())
    assert good.text == "Plan my week." and owner(account["config"], good).surface == "email"
    spoofed = box.normalize(mail(auth="mx.example.net; dkim=pass header.d=evil.test"))
    assert owner(account["config"], spoofed) is None  # Allow-listed address, but not authenticated for it.
    assert owner(account["config"], box.normalize(mail(auth=None, message_id="<m2@x>"))) is None
    assert authenticated(mail(auth="mx; spf=pass smtp.mailfrom=mail.example.com"))
    for header in (
        {"List_Id": "<news.example.com>"},
        {"Auto_Submitted": "auto-replied"},
        {"Precedence": "bulk"},
    ):
        assert automated(mail(**header)) and box.normalize(mail(**header)) is None
    assert box.normalize(mail(sender="panini@example.org")) is None  # Never answers itself.


def test_email_threads_by_root_and_replies_in_thread_once_per_uid(world):
    h, manager = world
    account = email_account(manager)
    first = mail()
    second = mail(
        message_id="<m3@example.com>", References="<m1@example.com>", In_Reply_To="<m1@example.com>"
    )
    assert root_of(first) == root_of(second) == "<m1@example.com>"
    sent = []

    class Imap:
        def login(self, user, password):
            assert password == "pw-1234"

        def select(self, *a, **k):
            return "OK", []

        def uid(self, command, *args):
            if command == "SEARCH":
                return "OK", [b"7 8"]
            uid = int(args[0])
            return "OK", [(b"", (first if uid == 7 else second).as_bytes())]

        def logout(self):
            pass

    class Smtp:
        def login(self, user, password):
            pass

        def send_message(self, message):
            sent.append(message)

        def quit(self):
            pass

    box = Email(account, manager.accounts, imap=Imap, smtp=Smtp)

    async def scenario():
        await box.poll_once(manager.deliver)
        await box.poll_once(manager.deliver)  # Already processed UIDs are not fetched again.
        return await box.send("<m1@example.com>", OutboundMessage("Here is your plan."))

    message_id = asyncio.run(scenario())
    assert manager.accounts.state(account["id"], "last_uid") == 8
    jobs = h.store.all("SELECT thread_key FROM channel_jobs")
    assert [j["thread_key"] for j in jobs] == [
        "<m1@example.com>"
    ]  # The second mail waits on the same thread.
    reply = sent[0]
    assert reply["To"] == "owner@example.com" and reply["Subject"] == "Re: Plan"
    assert reply["In-Reply-To"] == "<m3@example.com>" and "<m1@example.com>" in reply["References"]
    assert reply["Auto-Submitted"] == "auto-replied" and reply["Message-ID"] == message_id


# Invariants ---------------------------------------------------------------------------------------
def test_no_channel_message_can_resolve_an_approval(world):
    """I4: channel text has no approval authority, whatever it says."""
    h, manager = world
    account = slack_account(manager)
    from backend.interrupts import InterruptSpec

    card = h.interrupts.open(
        InterruptSpec("approval", "tool_call", "Pay", "Pay?", action={"a": 1}), run_id="r"
    )
    slack = adapter(manager, account)
    for text in ("approve", "yes", f"approve {card['id']}", "/approve"):
        asyncio.run(manager.deliver(slack.normalize(slack_event(text=text, ts=f"{len(text)}.0"))))
    assert h.interrupts.get(card["id"])["status"] == "open"


def test_channel_routes_save_slack_whatsapp_and_email_with_write_only_secrets(world):
    from types import SimpleNamespace

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.routes import channels as routes

    h, manager = world

    async def apply():
        return None

    manager.apply = apply
    app = FastAPI()
    app.include_router(routes.router)
    app.state.harness = SimpleNamespace(channels=manager)
    client = TestClient(app)
    created = client.post("/api/admin/v1/channels/slack", json={
        "label": "Work", "owner_ids": ["UOWNER01"], "team_id": "TTEAM001", "token": BOT, "app_token": APP,
        "enabled": True})  # fmt: skip
    assert created.status_code == 200 and BOT not in created.text and APP not in created.text
    bad = client.post("/api/admin/v1/channels/slack", json={"label": "Work", "owner_ids": ["UOWNER01"],
                                                            "team_id": "TTEAM001", "token": "xoxb-secret-but-bad"})  # fmt: skip
    assert bad.status_code == 422 and "xoxb-secret-but-bad" not in bad.text
    kept = client.put(f"/api/admin/v1/channels/slack/{created.json()['id']}", json={
        "label": "Work 2", "owner_ids": ["UOWNER01"], "team_id": "TTEAM001", "enabled": True})  # fmt: skip
    assert (
        kept.status_code == 200
        and kept.json()["token_configured"]
        and kept.json()["secrets_configured"]["app_token"]
    )
    mail_account = client.post("/api/admin/v1/channels/email", json={
        "label": "Mail", "owner_ids": ["me@example.com"], "address": "panini@example.org", "username": "panini",
        "imap_host": "imap.example.org", "smtp_host": "smtp.example.org", "token": "pw-1234"})  # fmt: skip
    assert mail_account.status_code == 200 and mail_account.json()["channel"] == "email"


def test_a_conversation_started_on_a_channel_continues_on_the_avatar(world):
    h, manager = world
    account = slack_account(manager)
    asyncio.run(manager.deliver(adapter(manager, account).normalize(slack_event())))
    channel_thread = h.store.one("SELECT thread_id FROM channel_jobs")["thread_id"]
    assert h.threads.bind("avatar", "pc-session-1") == channel_thread  # Handoff: same conversation.
    assert h.threads.bind("avatar", "pc-session-2", continue_recent=False) != channel_thread


def test_proactive_notices_go_only_to_the_owners_private_conversation():
    from backend.channels.identity import private_conversation

    def job(key, sender):
        return {"thread_key": key, "sender_id": sender}

    assert private_conversation({"channel": "telegram"}, job("42", "42"))
    assert not private_conversation({"channel": "telegram"}, job("-100", "42"))  # A group.
    assert private_conversation({"channel": "whatsapp"}, job("447700900001", "447700900001"))
    assert private_conversation({"channel": "slack"}, job("TTEAM001:DOWNER01:", "UOWNER01"))
    assert not private_conversation({"channel": "slack"}, job("TTEAM001:CTEAM001:1.1", "UOWNER01"))
    assert private_conversation({"channel": "email"}, job("<m1@example.com>", "owner@example.com"))


def test_a_long_channel_result_is_saved_as_a_space_page_with_a_short_reply(world, monkeypatch):
    h, manager = world
    monkeypatch.setenv("PANINI_SPACES", "true")
    space = h.spaces.create_space("Results")
    h.personas.update("panini", default_space_id=space["id"])
    account = slack_account(manager)
    long_answer = "Findings. " + "Detail sentence about the market. " * 150
    h.model_factory().steps[:] = [{"text": long_answer}]

    async def scenario():
        await manager.deliver(adapter(manager, account).normalize(slack_event(text="<@UBOT0001> research")))
        task = h.tasks.claim()
        outcome, text, evidence = await h.run_background(task, h.tasks)
        h.tasks.finish(task["id"], task["lease_id"], outcome, text, evidence)
        manager.reconcile()

    asyncio.run(scenario())
    reply = h.store.one("SELECT text FROM channel_outbox")["text"]
    assert len(reply) < 1000 and "saved in PANINI as the page" in reply
    pages = h.spaces.pages(space["id"])
    assert len(pages) == 1 and "Detail sentence about the market." in h.spaces.read(pages[0]["id"])["content"]
