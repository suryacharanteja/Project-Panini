import asyncio
import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from panini_cli.agent.model import ScriptedModel

from backend.capabilities.contracts import Outcome
from backend.fabric.ingress import Ingress
from backend.harness.connectors import Connectors
from backend.harness.runtime import Harness
from backend.harness.store import Store
from backend.interrupts import decided_by
from backend.triggers.rules import TriggerEvent, matches, validate_rule
from backend.triggers.sources.mcp_events import bind, drain_calendar, poll
from backend.triggers.sources.webhooks import handler


@pytest.fixture
def harness(tmp_path, monkeypatch):
    monkeypatch.setenv("PANINI_TRIGGERS", "true")
    monkeypatch.setenv("PANINI_V2_ONE_BRAIN", "true")
    h = Harness(
        Store(tmp_path / "db.sqlite3"),
        model_factory=lambda: ScriptedModel([]),
        connectors=Connectors(path=tmp_path / "connectors.json"),
    )
    yield h
    h.store.close()


def rule(source="os", match=None, **overrides):
    data = {
        "name": "Review new events",
        "persona": "panini",
        "source": source,
        "match": match or {"kind": ["resumed"]},
        "condition": None,
        "action": {"goal": "Summarize this event", "budget_usd": 0.05},
        "cooldown_s": 0,
        "max_per_day": 3,
        "quiet_hours": None,
    }
    return {**data, **overrides}


def approve(h, review_id):
    view = h.interrupts.get(review_id, include_secret=True)
    return h.interrupts.resolve(
        review_id,
        nonce=view["nonce"],
        action_digest=view["action_digest"],
        status="resolved",
        payload={"approved": True},
        decided_by=decided_by("console", "local"),
    )


def test_review_dedupe_limits_and_rollback(harness, monkeypatch):
    h = harness
    proposed = h.triggers.propose(rule())
    event = TriggerEvent("os", "resumed", {"state": "resumed"}, h.triggers.clock(), "e1", False)
    assert asyncio.run(h.triggers.emit(event))["queued"] == []
    approve(h, proposed["review_id"])
    assert h.triggers.get(proposed["id"])["status"] == "active"
    assert (
        len(
            asyncio.run(
                h.triggers.emit(
                    TriggerEvent("os", "resumed", {"state": "resumed"}, h.triggers.clock(), "e2", False)
                )
            )["queued"]
        )
        == 1
    )
    assert asyncio.run(h.triggers.emit(event))["status"] == "duplicate"
    for index in range(3, 6):
        asyncio.run(
            h.triggers.emit(
                TriggerEvent("os", "resumed", {"state": "resumed"}, h.triggers.clock(), f"e{index}", False)
            )
        )
    assert h.store.one("SELECT count(*) n FROM trigger_firings")["n"] == 3
    monkeypatch.setenv("PANINI_TRIGGERS", "false")
    h.triggers.cancel_pending()
    assert h.store.one("SELECT count(*) n FROM tasks WHERE status='queued'")["n"] == 0
    assert asyncio.run(h.triggers.emit(event))["status"] == "disabled"


def test_quiet_hour_queues_reserve_the_due_day(harness):
    h = harness
    base = datetime(2026, 10, 5, 23, tzinfo=UTC).timestamp()
    h.triggers.clock = lambda: base
    proposed = h.triggers.propose(
        rule(
            max_per_day=1,
            quiet_hours={"start": 22, "end": 7, "timezone": "UTC"},
        )
    )
    approve(h, proposed["review_id"])
    first = asyncio.run(
        h.triggers.emit(TriggerEvent("os", "resumed", {"state": "resumed"}, base, "quiet-1", False))
    )
    assert len(first["queued"]) == 1
    due = h.store.one("SELECT due FROM trigger_firings")["due"]
    assert due == datetime(2026, 10, 6, 7, tzinfo=UTC).timestamp()
    h.triggers.clock = lambda: base + 3600
    second = asyncio.run(
        h.triggers.emit(TriggerEvent("os", "resumed", {"state": "resumed"}, base + 3600, "quiet-2", False))
    )
    assert second["queued"] == []


@pytest.mark.parametrize("registry", [False, True])
def test_agent_can_only_propose_reviewed_rule(harness, monkeypatch, registry):
    h = harness
    monkeypatch.setenv("PANINI_V2_REGISTRY", "true" if registry else "false")

    async def delegate(*_args):
        raise AssertionError("A proposal must not run a capability.")

    agent = h.agent(delegate=delegate, capabilities=())
    output, _ = asyncio.run(agent.call("create_trigger", json.dumps({"rule_json": json.dumps(rule())})))
    assert "error" not in output
    assert h.triggers.list()[0]["status"] == "proposed"
    assert h.tasks.list() == []
    monkeypatch.setenv("PANINI_TRIGGERS", "false")
    hidden, _ = asyncio.run(agent.call("list_triggers", "{}"))
    assert "unavailable" in hidden["error"]


def test_filters_and_untrusted_goal(harness, tmp_path):
    folder = tmp_path / "chosen"
    folder.mkdir()
    spec = rule(
        "files",
        {"kind": ["created"], "watch_root": str(folder), "path_glob": str(folder / "*invoice*.pdf")},
        action={"goal": "Read {event.path} and summarize", "budget_usd": 0.05},
    )
    validate_rule(spec)
    assert matches(
        spec, TriggerEvent("files", "created", {"path": str(folder / "invoice.pdf")}, 1, "file1", True)
    )
    assert not matches(
        spec, TriggerEvent("files", "created", {"path": str(tmp_path / "invoice.pdf")}, 1, "file2", True)
    )
    spec["match"]["path_glob"] = str(folder / ".." / "*.pdf")
    with pytest.raises(ValueError):
        validate_rule(spec)
    with pytest.raises(ValueError):
        TriggerEvent("os", "resumed", {"state": "resumed", "unknown": "x"}, 1, "x", False)


def test_signed_webhook_and_own_file_write(harness, tmp_path):
    h = harness
    proposal = h.triggers.propose(rule("webhook", {"kind": ["received"], "hook": "invoices"}))
    approve(h, proposal["review_id"])
    ingress = Ingress(
        h.store,
        {"generic:invoices": {"kind": "generic", "path_secret": "hidden-path", "secret": "signing-key"}},
        clock=lambda: 1000,
    )
    ingress.on("generic", handler(h.triggers))
    body = json.dumps({"fields": {"event": "new", "password": "must-redact"}}).encode()
    stamp = "1000"
    signature = hmac.new(b"signing-key", stamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    headers = {
        "X-PANINI-Timestamp": stamp,
        "X-PANINI-Delivery": "delivery-1",
        "X-PANINI-Signature": signature,
    }

    async def deliver(hdr):
        return await ingress(
            {
                "path": "/generic:invoices/hidden-path",
                "method": "POST",
                "body": base64.b64encode(body).decode(),
                "headers": hdr,
            }
        )

    assert asyncio.run(deliver(dict(headers, **{"X-PANINI-Signature": "0" * 64})))["status"] == 401
    assert asyncio.run(deliver(headers))["status"] == 202
    assert asyncio.run(deliver(headers))["body"] == "duplicate"
    stored = h.store.one("SELECT event FROM trigger_events WHERE source='webhook'")["event"]
    assert "must-redact" not in stored and "new" in stored
    own = tmp_path / "own.txt"
    h.triggers.mark_own_write(own, "receipt")
    assert (
        asyncio.run(
            h.triggers.emit(
                TriggerEvent("files", "created", {"path": str(own)}, h.triggers.clock(), "own", True)
            )
        )["status"]
        == "self_write"
    )


def test_signed_delivery_can_retry_after_internal_failure(harness):
    ingress = Ingress(
        harness.store,
        {"generic:retry": {"kind": "generic", "path_secret": "path", "secret": "key"}},
        clock=lambda: 1000,
    )
    calls = 0

    async def flaky(_hook, _headers, _body):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("Temporary database failure")
        return {"status": 202, "body": "accepted"}

    ingress.on("generic", flaky)
    body = b'{"fields":{}}'
    headers = {
        "X-PANINI-Timestamp": "1000",
        "X-PANINI-Delivery": "retry-1",
        "X-PANINI-Signature": hmac.new(b"key", b"1000." + body, hashlib.sha256).hexdigest(),
    }

    async def deliver():
        return await ingress(
            {
                "path": "/generic:retry/path",
                "method": "POST",
                "body": base64.b64encode(body).decode(),
                "headers": headers,
            }
        )

    with pytest.raises(RuntimeError):
        asyncio.run(deliver())
    assert asyncio.run(deliver())["status"] == 202
    assert calls == 2


def test_responsibility_report_and_failure_pause(harness):
    h = harness
    spec = {
        "title": "Prepare my day",
        "persona_id": "panini",
        "charter": "Check my planned work.",
        "success_criteria": "A clear summary",
        "cadence_minutes": 5,
        "trigger_ids": [],
        "report": "daily",
        "escalation": "Tell me about blockers.",
        "space_id": None,
        "budget_usd": 0.05,
    }
    item = h.triggers.responsibilities.propose(spec)
    approve(h, item["review_id"])
    assert h.triggers.responsibilities.get(item["id"])["status"] == "active"
    h.store.execute("UPDATE responsibilities SET next_report=? WHERE id=?", (0.1, item["id"]))
    h.triggers.responsibilities._report(h.triggers.responsibilities.get(item["id"]))
    current = h.triggers.responsibilities.get(item["id"])
    assert h.spaces.read(current["report_page_id"])["title"].startswith("Prepare my day")
    assert h.store.one("SELECT count(*) n FROM pages")["n"] == 1
    h.triggers.responsibilities._report(current)
    assert h.store.one("SELECT count(*) n FROM pages")["n"] == 1
    for index in range(5):
        h.triggers.responsibilities.observe(item["id"], f"failed{index}", "failed")
        if index == 0:
            assert h.triggers.responsibilities.get(item["id"])["next_check"] >= h.triggers.clock() + 590
    assert h.triggers.responsibilities.get(item["id"])["status"] == "paused"


def test_schedule_journal_skips_trigger_origin(harness):
    h = harness
    proposal = h.triggers.propose(rule("schedule", {"kind": ["completed"]}))
    approve(h, proposal["review_id"])
    user = h.tasks.submit("Check the site", origin="user", every_minutes=5)
    trigger = h.tasks.submit(
        "Another trigger", origin="trigger:1", kind="trigger", persona_id="panini", budget_usd=0.05
    )
    h.store.event(user["id"], "completed", "done")
    h.store.event(trigger["id"], "completed", "done")
    h.store.execute("INSERT INTO trigger_meta(key,value) VALUES('schedule_cursor','0')")
    asyncio.run(h.triggers._schedule_events())
    assert h.store.one("SELECT count(*) n FROM trigger_firings")["n"] == 1


def test_bound_read_only_email_poll_and_revocation(harness):
    h = harness
    proposal = h.triggers.propose(rule("email", {"kind": ["received"], "subject_contains": "invoice"}))
    approve(h, proposal["review_id"])

    class ReadOnlyServer:
        tools = [{"name": "list_new_mail", "read_only": True}]
        spec = {"background_read_only": ["list_new_mail"]}
        calls = 0

        async def call(self, tool_name, arguments):
            assert tool_name == "list_new_mail"
            self.calls += 1
            return {
                "is_error": False,
                "text": json.dumps(
                    {
                        "items": [
                            {"id": "message-1", "from": "vendor@example.test", "subject": "October invoice"},
                            *(
                                [
                                    {
                                        "id": "message-2",
                                        "from": "vendor@example.test",
                                        "subject": "November invoice",
                                    }
                                ]
                                if self.calls > 1
                                else []
                            ),
                        ],
                        "cursor": "next-page",
                    }
                ),
            }

    server = ReadOnlyServer()
    h.connectors.servers["mail"] = server
    bind(h.triggers, "email", "mail", "list_new_mail", {})
    asyncio.run(poll(h.triggers))
    assert h.store.one("SELECT count(*) n FROM trigger_firings")["n"] == 0
    h.store.execute("UPDATE trigger_source_bindings SET last_polled=0,last_attempt=0 WHERE source='email'")
    asyncio.run(poll(h.triggers))
    assert h.store.one("SELECT count(*) n FROM trigger_firings")["n"] == 1
    assert (
        h.store.one("SELECT cursor FROM trigger_source_bindings WHERE source='email'")["cursor"]
        == "next-page"
    )
    server.spec["background_read_only"] = []
    h.store.execute("UPDATE trigger_source_bindings SET last_polled=0,last_attempt=0 WHERE source='email'")
    asyncio.run(poll(h.triggers))
    assert (
        "removed"
        in h.store.one("SELECT last_error FROM trigger_source_bindings WHERE source='email'")["last_error"]
    )
    assert h.store.one("SELECT count(*) n FROM trigger_firings")["n"] == 1


def test_email_initial_failure_does_not_turn_old_mail_into_new_mail(harness):
    h = harness
    proposal = h.triggers.propose(rule("email", {"kind": ["received"]}))
    approve(h, proposal["review_id"])

    class Mail:
        tools = [{"name": "new", "read_only": True}]
        spec = {"background_read_only": ["new"]}
        calls = 0

        async def call(self, _tool, _arguments):
            self.calls += 1
            if self.calls == 1:
                return {"is_error": True, "text": "Temporary outage"}
            return {"is_error": False, "text": json.dumps({"items": [{"id": "old-mail"}]})}

    h.connectors.servers["mail"] = Mail()
    bind(h.triggers, "email", "mail", "new", {})
    asyncio.run(poll(h.triggers))
    assert (
        h.store.one("SELECT last_polled FROM trigger_source_bindings WHERE source='email'")["last_polled"]
        is None
    )
    h.store.execute("UPDATE trigger_source_bindings SET last_attempt=0 WHERE source='email'")
    asyncio.run(poll(h.triggers))
    assert h.store.one("SELECT count(*) n FROM trigger_firings")["n"] == 0


def test_calendar_poll_window_and_dedupe(harness):
    h = harness
    now = h.triggers.clock()
    proposal = h.triggers.propose(rule("calendar", {"kind": ["starting_soon"], "title_contains": "planning"}))
    approve(h, proposal["review_id"])

    class Calendar:
        tools = [{"name": "upcoming", "read_only": True}]
        spec = {"background_read_only": ["upcoming"]}
        calls = 0
        items = [
            {
                "id": "meeting-1",
                "title": "Planning review",
                "start": datetime.fromtimestamp(now + 300, UTC).isoformat(),
            }
        ]

        async def call(self, tool_name, arguments):
            self.calls += 1
            return {"is_error": False, "text": json.dumps({"items": self.items})}

    provider = Calendar()
    h.connectors.servers["calendar"] = provider
    bind(h.triggers, "calendar", "calendar", "upcoming", {})
    asyncio.run(poll(h.triggers))
    assert provider.calls == 1
    assert h.store.one("SELECT count(*) n FROM trigger_firings")["n"] == 1
    asyncio.run(poll(h.triggers))
    assert provider.calls == 1
    h.store.execute("UPDATE trigger_source_bindings SET last_polled=0,last_attempt=0 WHERE source='calendar'")
    asyncio.run(poll(h.triggers))
    assert provider.calls == 2
    assert h.store.one("SELECT count(*) n FROM trigger_firings")["n"] == 1
    provider.items = [
        {
            "id": "meeting-2",
            "title": "Planning review",
            "start": datetime.fromtimestamp(now + 1200, UTC).isoformat(),
        }
    ]
    h.store.execute("UPDATE trigger_source_bindings SET last_polled=0,last_attempt=0 WHERE source='calendar'")
    asyncio.run(poll(h.triggers))
    assert h.store.one("SELECT count(*) n FROM trigger_calendar_pending")["n"] == 1
    assert h.store.one("SELECT count(*) n FROM trigger_firings")["n"] == 1
    h.triggers.clock = lambda: now + 650
    asyncio.run(drain_calendar(h.triggers))
    assert h.store.one("SELECT count(*) n FROM trigger_calendar_pending")["n"] == 0
    assert h.store.one("SELECT count(*) n FROM trigger_firings")["n"] == 2


def test_console_routes_require_auth_and_review(harness):
    from backend.app import create_app

    h = harness
    app = create_app(harness_factory=lambda: h, console_token="t" * 32)
    headers = {"x-panini-console": "t" * 32}
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.get("/api/harness/triggers").status_code == 401
        response = client.post("/api/harness/triggers", json=rule(), headers=headers)
        assert response.status_code == 200
        rule_id = response.json()["id"]
        assert client.get("/api/harness/triggers", headers=headers).json()["rules"][0]["status"] == "proposed"
        assert client.post(f"/api/harness/triggers/{rule_id}/pause", headers=headers).status_code == 200
        assert h.triggers.get(rule_id)["status"] == "paused"
        assert (
            client.post("/api/harness/triggers/hooks", json={"name": "orders"}, headers=headers).status_code
            == 200
        )
        assert (
            client.post("/api/harness/triggers/hooks", json={"name": "orders"}, headers=headers).status_code
            == 422
        )


def test_actual_folder_watcher_queues_within_five_seconds(harness, tmp_path):
    h = harness
    folder = tmp_path / "downloads"
    folder.mkdir()
    proposal = h.triggers.propose(
        rule(
            "files",
            {"kind": ["created"], "watch_root": str(folder), "path_glob": str(folder / "*invoice*.pdf")},
        )
    )
    approve(h, proposal["review_id"])

    async def exercise():
        h.triggers.start()
        for _ in range(30):
            if h.triggers.file_watchers:
                break
            await asyncio.sleep(0.05)
        await asyncio.sleep(0.2)
        (folder / "october-invoice.pdf").write_bytes(b"fixture")
        for _ in range(90):
            if h.store.one("SELECT count(*) n FROM trigger_firings")["n"]:
                break
            await asyncio.sleep(0.05)
        assert h.store.one("SELECT count(*) n FROM trigger_firings")["n"] == 1
        await h.triggers.close()

    asyncio.run(exercise())


def test_invoice_event_runs_worker_then_creates_report_and_notice(harness, tmp_path):
    h = harness
    folder = tmp_path / "invoices"
    folder.mkdir()
    proposal = h.triggers.propose(
        rule("files", {"kind": ["created"], "watch_root": str(folder), "path_glob": str(folder / "*.pdf")})
    )
    approve(h, proposal["review_id"])
    responsibility = h.triggers.responsibilities.propose(
        {
            "title": "File invoices",
            "persona_id": "panini",
            "charter": "Check new invoices.",
            "success_criteria": "Summarize invoice metadata",
            "cadence_minutes": None,
            "trigger_ids": [proposal["id"]],
            "report": "weekly",
            "escalation": "Ask before payment.",
            "space_id": None,
            "budget_usd": 0.05,
            "daily_budget_usd": 0.05,
        }
    )
    approve(h, responsibility["review_id"])

    class Search:
        async def availability(self):
            return {"state": "ready", "reason": ""}

        async def run(self, ctx, instruction, _confirm):
            return Outcome(
                "verified_complete",
                "Fixture checked",
                [],
                [ctx.record_evidence("retrieved", "local-fixture", {})],
            )

        async def release(self, task_id):
            pass

    h.adapters = {"search": Search()}
    h.model_factory = lambda: ScriptedModel(
        [
            {
                "calls": [
                    {
                        "name": "use_capability",
                        "arguments": {"capability": "search", "instruction": "Check the fixture invoice"},
                    }
                ]
            },
            {
                "calls": [
                    {
                        "name": "finish",
                        "arguments": {"outcome": "verified_complete", "message": "Invoice checked."},
                    }
                ]
            },
        ]
    )
    file = folder / "october.pdf"
    file.write_bytes(b"%PDF fixture")
    queued = asyncio.run(
        h.triggers.emit(
            TriggerEvent(
                "files",
                "created",
                {"path": str(file), "name": file.name, "size": file.stat().st_size},
                h.triggers.clock(),
                "invoice-created-1",
                True,
            )
        )
    )["queued"]
    assert len(queued) == 1
    assert asyncio.run(h.worker.run_once())
    assert h.tasks.get(queued[0])["status"] == "completed"
    assert (
        asyncio.run(
            h.triggers.emit(
                TriggerEvent(
                    "files",
                    "created",
                    {"path": str(file), "name": file.name, "size": file.stat().st_size},
                    h.triggers.clock(),
                    "invoice-created-2",
                    True,
                )
            )
        )["queued"]
        == []
    )
    h.triggers.reconcile()
    assert h.triggers.responsibilities.get(responsibility["id"])["health"]["successes"] == 1
    h.store.execute("UPDATE responsibilities SET next_report=? WHERE id=?", (0.1, responsibility["id"]))
    h.triggers.responsibilities._report(h.triggers.responsibilities.get(responsibility["id"]))
    current = h.triggers.responsibilities.get(responsibility["id"])
    assert "Invoice checked" in h.spaces.read(current["report_page_id"])["content"]
    assert any("report ready" in notice["title"].lower() for notice in h.notices.list())
