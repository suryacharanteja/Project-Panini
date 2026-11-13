import asyncio
import copy
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.brain.threads import Threads
from backend.fabric.routing import Continuity, defaults, quiet_until, validate_settings
from backend.harness.proactive import Notices
from backend.harness.sessions import Sessions
from backend.harness.store import Store
from backend.interrupts import Interrupts, InterruptSpec

from .test_presence import pc


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setenv("PANINI_PRESENCE", "true")
    store = Store(tmp_path / "routing.sqlite3")
    now = [1000.0]

    def clock():
        return now[0]

    h = SimpleNamespace(
        store=store,
        interrupts=Interrupts(store, clock=clock),
        notices=Notices(store, clock=clock),
        sessions=Sessions(store),
    )
    h.threads = Threads(store, h.sessions)
    service = Continuity(h, clock=clock)
    h.continuity = service
    service.fabric = SimpleNamespace(
        push=SimpleNamespace(send=AsyncMock(return_value={"sent": 1, "removed": 0}))
    )
    config = defaults()
    config["quiet"]["enabled"] = False
    service.configure(config)
    yield service, now
    store.close()


def approval(service, level="tap", ttl=1000):
    return service.h.interrupts.open(
        InterruptSpec("approval", "tool_call", "Owner decision", "Decide", level=level, ttl_s=ttl), run_id="r"
    )


def tick(service):
    asyncio.run(service.tick())


def test_locked_pc_routes_promptly_without_approval_authority_and_retracts_on_resolution(service):
    continuity, _ = service
    continuity.presence.signal("pc", pc(locked=True))
    view = approval(continuity)
    tick(continuity)
    assert continuity.fabric.push.send.call_args.args[0]["id"] == view["id"]
    assert continuity.h.interrupts.get(view["id"])["status"] == "open"
    full = continuity.h.interrupts.get(view["id"], include_secret=True)
    continuity.h.interrupts.resolve(
        view["id"],
        nonce=full["nonce"],
        action_digest=full["action_digest"],
        status="resolved",
        payload={"approved": True},
        decided_by={"surface": "phone_app", "method": "tap", "principal_kind": "owner"},
    )
    tick(continuity)
    assert continuity.fabric.push.send.call_args.args[0]["type"] == "interrupt_resolved"
    assert any(command["kind"] == "retract" for command in continuity.commands())


def test_near_pc_push_is_cancelled_when_resolved_before_delay(service):
    continuity, now = service
    continuity.presence.signal("pc", pc(130))
    view = approval(continuity)
    tick(continuity)
    assert continuity.fabric.push.send.await_count == 0
    continuity.h.interrupts.expire(view["id"])
    now[0] += 31
    tick(continuity)
    assert all(
        call.args[0]["type"] == "interrupt_resolved" for call in continuity.fabric.push.send.call_args_list
    )
    row = continuity.store.one(
        "SELECT state FROM continuity_deliveries WHERE item_id=? AND route='push'",
        ("interrupt:" + view["id"],),
    )
    assert row["state"] == "cancelled"


def test_quiet_hours_delay_push_but_never_retraction(service):
    continuity, now = service
    now[0] = datetime.fromisoformat("2026-10-05T23:00:00+04:00").timestamp()
    config = defaults()
    config["quiet"]["timezone"] = "Asia/Dubai"
    continuity.configure(config)
    view = approval(continuity, ttl=86400)
    tick(continuity)
    assert continuity.fabric.push.send.await_count == 0
    continuity.h.interrupts.expire(view["id"])
    tick(continuity)
    assert continuity.fabric.push.send.call_args.args[0]["type"] == "interrupt_resolved"


def test_trusted_urgent_responsibility_notice_bypasses_quiet_hours_for_push(service):
    continuity, now = service
    now[0] = datetime.fromisoformat("2026-10-05T23:00:00+04:00").timestamp()
    config = defaults()
    config["quiet"]["timezone"] = "Asia/Dubai"
    continuity.configure(config)
    notice = continuity.h.notices.add(
        "responsibility", "Responsibility paused after failures", "Review it", urgent=True
    )
    tick(continuity)
    assert continuity.fabric.push.send.await_count == 1
    assert continuity.fabric.push.send.call_args.args[0]["id"] == notice["id"]
    assert (
        continuity.store.one("SELECT urgent FROM continuity_items WHERE id=?", ("notice:" + notice["id"],))[
            "urgent"
        ]
        == 1
    )


def test_exact_thread_handoff_persists_without_new_sessions(service):
    continuity, _ = service
    thread = continuity.h.sessions.create("Owner conversation")
    continuity.h.sessions.save(thread, [{"role": "user", "content": "Remember this turn"}])
    result = continuity.handoff(thread, "pc")
    command = continuity.commands()[0]
    assert result["thread_id"] == thread == command["data"]["thread_id"]
    continuity.acknowledge(command["seq"])
    assert continuity.commands() == []
    assert len(continuity.h.sessions.list()) == 1
    with pytest.raises(KeyError):
        continuity.handoff("missing", "pc")


def test_ambiguous_delivery_is_not_replayed_after_restart(service):
    continuity, _ = service
    continuity.fabric.push.send.side_effect = TimeoutError("provider-secret")
    approval(continuity)
    tick(continuity)
    tick(continuity)
    assert continuity.fabric.push.send.await_count == 1
    delivery = continuity.store.one("SELECT * FROM continuity_deliveries WHERE route='push'")
    assert delivery["state"] == "unknown"
    assert "provider-secret" not in delivery["detail"]


def test_resolution_racing_send_still_retracts(service):
    continuity, _ = service
    view = approval(continuity)

    async def send(message):
        if message["type"] == "interrupt":
            continuity.h.interrupts.expire(view["id"])
        return {"sent": 1}

    continuity.fabric.push.send.side_effect = send
    tick(continuity)
    tick(continuity)
    assert continuity.fabric.push.send.call_args.args[0]["type"] == "interrupt_resolved"


def test_flag_off_has_no_new_deliveries_but_clears_old_notifications(service, monkeypatch):
    continuity, _ = service
    first = approval(continuity)
    tick(continuity)
    monkeypatch.setenv("PANINI_PRESENCE", "false")
    second = approval(continuity)
    continuity.h.interrupts.expire(first["id"])
    tick(continuity)
    assert (
        continuity.store.one("SELECT id FROM continuity_items WHERE id=?", ("interrupt:" + second["id"],))
        is None
    )
    assert continuity.fabric.push.send.call_args.args[0]["type"] == "interrupt_resolved"


def test_notices_are_retracted_when_seen_and_duplicates_do_not_route(service):
    continuity, _ = service
    notice = continuity.h.notices.add("watch", "Changed", "Body", dedupe="same")
    assert continuity.h.notices.add("watch", "Changed", "Body", dedupe="same") is None
    tick(continuity)
    continuity.h.notices.mark(notice["id"], "read")
    tick(continuity)
    assert (
        continuity.store.one("SELECT status FROM continuity_items WHERE id=?", ("notice:" + notice["id"],))[
            "status"
        ]
        == "closed"
    )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda c: c["rules"]["tap"].pop("away"),
        lambda c: c["rules"]["tap"].update(away=[["call", 0]]),
        lambda c: c["rules"]["tap"].update(away=[["push", True]]),
        lambda c: c["quiet"].update(start=7, end=7),
        lambda c: c["quiet"].update(timezone="Not/AZone"),
        lambda c: c.update(preferred_channel={"account_id": "any", "thread_key": "-group"}),
    ],
)
def test_invalid_rules_and_untrusted_destinations_cannot_enable_calls_or_routes(service, mutation):
    continuity, _ = service
    config = copy.deepcopy(defaults())
    mutation(config)
    with pytest.raises(ValueError):
        continuity.configure(config)


def test_complete_default_table_and_quiet_hours_across_dst():
    validate_settings(defaults())
    quiet = {"enabled": True, "start": 22, "end": 7, "timezone": "America/New_York"}
    now = datetime.fromisoformat("2026-10-31T23:00:00-04:00").timestamp()
    assert quiet_until(now, quiet) == datetime.fromisoformat("2026-11-01T07:00:00-05:00").timestamp()


def test_legacy_foreground_runs_without_brain_run_id_still_notify_safely(service, monkeypatch):
    continuity, _ = service
    thread = continuity.h.sessions.create("Legacy foreground")
    continuity.foreground_result(thread, None, "Result")
    assert continuity.store.one("SELECT thread_id FROM continuity_items")["thread_id"] == thread
    monkeypatch.setenv("PANINI_PRESENCE", "false")
    continuity.foreground_result(thread, None, "Disabled")
    assert len(continuity.store.all("SELECT id FROM continuity_items")) == 1


def test_pc_command_is_handled_only_after_exact_ack(service):
    continuity, _ = service
    continuity.presence.signal("pc", pc())
    view = approval(continuity)
    tick(continuity)
    row = continuity.store.one(
        "SELECT state FROM continuity_deliveries WHERE item_id=?", ("interrupt:" + view["id"],)
    )
    assert row["state"] == "queued_pc"
    continuity.acknowledge(9999)
    assert len(continuity.commands()) == 1
    continuity.acknowledge(continuity.commands()[0]["seq"])
    assert (
        continuity.store.one(
            "SELECT state FROM continuity_deliveries WHERE item_id=?", ("interrupt:" + view["id"],)
        )["state"]
        == "handled_by_pc"
    )


def test_after_call_notice_is_deferred_once_and_unverified_call_is_not_owner_presence(service):
    continuity, _ = service
    call = SimpleNamespace(verified=False, caller={"kind": "owner"}, closing=False)
    continuity.app = SimpleNamespace(state=SimpleNamespace(phone_line=SimpleNamespace(active=call)))
    assert continuity.presence_state()["state"] == "away"
    call.verified = True
    assert continuity.presence_state()["state"] == "on_call"
    notice = continuity.h.notices.add("watch", "Changed", "Details")
    tick(continuity)
    assert not continuity.commands()
    call.closing = True
    tick(continuity)
    assert len([command for command in continuity.commands() if command["kind"] == "notification"]) == 1
    tick(continuity)
    assert len(continuity.commands()) == 1
    assert (
        continuity.store.one(
            "SELECT state FROM continuity_deliveries WHERE item_id=? AND route='after_call'",
            ("notice:" + notice["id"],),
        )["state"]
        == "sent"
    )
