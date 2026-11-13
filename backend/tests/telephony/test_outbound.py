import asyncio
import json
from datetime import UTC, datetime

import httpx
import pytest
from fastapi.testclient import TestClient
from panini_cli.agent.model import ScriptedModel

from backend.app import create_app
from backend.harness.runtime import Harness
from backend.harness.store import Store
from backend.interrupts import digest
from backend.ledger.chain import verify
from backend.panini.contracts import Fault
from backend.policy.engine import Grant
from backend.telephony.brief import EXCLUSIONS, CallBrief
from backend.telephony.numbers import calling_window, validate
from backend.telephony.outbound import CHECKLIST, OutboundCalls, OutboundConnection, trunk_url

BRIEF = {
    "callee_name": "Dental test",
    "number": "+14155552671",
    "number_source": "owner",
    "purpose": "Ask for appointment times",
    "owner_first_name": "Alex",
    "may_disclose": ["First name Alex"],
    "acceptable_outcomes": ["An afternoon appointment next week"],
}


class Secrets(dict):
    def set(self, key, value):
        self[key] = value


class Connection:
    def __init__(self, _key, on_event, on_request):
        self.on_event, self.on_request = on_event, on_request
        self.finalized = asyncio.Event()
        self.spoken, self.sent, self.closed = [], [], False

    async def attach(self, session):
        self.session = session

    async def speak(self, text, **kwargs):
        self.spoken.append(text)

    async def send(self, kind, **kwargs):
        self.sent.append((kind, kwargs))

    async def close(self):
        self.closed = True


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def world(tmp_path, monkeypatch):
    monkeypatch.setenv("PANINI_OUTBOUND_CALLS", "true")
    monkeypatch.setenv("PANINI_PERSONAS", "false")
    monkeypatch.setenv("PANINI_CHANNELS", "")
    h = Harness(
        Store(tmp_path / "h.sqlite3"), model_factory=lambda: ScriptedModel([{"text": "No call action"}])
    )
    now = [datetime(2026, 10, 4, 16, tzinfo=UTC).timestamp()]
    h.interrupts.clock = lambda: now[0]
    observed = []

    async def provider(action, session_id=None, body=None):
        observed.append((action, session_id, body))
        return {"session": {"id": "live_test_" + str(len(observed))}} if action == "create" else None

    service = h.outbound = OutboundCalls(
        h, provider=provider, connection_factory=Connection, secrets=Secrets(), clock=lambda: now[0]
    )
    service.configure(
        {
            "provider_url": "sips:sip.provider.example:5061",
            "caller_number": "+14155552672",
            "owner_number": "+14155552673",
        },
        username="test-user",
        password="test-password",
        checklist=dict.fromkeys(CHECKLIST, True),
    )
    yield h, service, observed, now
    await h.close()


def approve(h, row, *, surface="phone_app", method="tap", approved=True):
    view = h.interrupts.get(row["interrupt_id"], include_secret=True)
    return h.interrupts.resolve(
        view["id"],
        nonce=view["nonce"],
        action_digest=view["action_digest"],
        status="resolved",
        payload={"approved": approved},
        decided_by={"surface": surface, "method": method, "principal_kind": "owner"},
    )


def approve_urgent_grant(h, review_id):
    view = h.interrupts.get(review_id, include_secret=True)
    return h.interrupts.resolve(
        review_id,
        nonce=view["nonce"],
        action_digest=view["action_digest"],
        status="resolved",
        payload={"approved": True},
        decided_by={"surface": "console", "method": "local", "principal_kind": "owner"},
    )


@pytest.mark.anyio
async def test_urgent_owner_grant_calls_once_after_five_minutes_without_item_contents(world, monkeypatch):
    monkeypatch.setenv("PANINI_PRESENCE", "true")
    h, service, observed, now = world
    h.continuity.clock = h.continuity.urgent.clock = lambda: now[0]
    h.continuity.presence.clock = lambda: now[0]
    h.notices.clock = lambda: now[0]
    grant = h.continuity.urgent.propose()
    assert grant["status"] == "proposed"
    approve_urgent_grant(h, grant["review_id"])
    assert h.continuity.urgent.status()["status"] == "active"
    notice = h.notices.add(
        "responsibility", "Responsibility paused after failures", "PRIVATE FAILURE CONTENT", urgent=True
    )
    await h.continuity.tick()
    assert h.store.one("SELECT COUNT(*) AS n FROM continuity_urgent_attempts")["n"] == 0
    now[0] += 301
    await h.continuity.tick()
    attempt = h.store.one("SELECT * FROM continuity_urgent_attempts")
    assert attempt["item_id"] == "notice:" + notice["id"]
    row = service.get(attempt["brief_id"])
    assert row["interrupt_id"] is None and row["standing_grant_id"] == grant["id"]
    assert "PRIVATE FAILURE CONTENT" not in json.dumps(row["brief"])
    await service.tick()
    await service.work
    assert len([action for action, _, _ in observed if action == "create"]) == 1
    await h.continuity.tick()
    assert h.store.one("SELECT COUNT(*) AS n FROM continuity_urgent_attempts")["n"] == 1


@pytest.mark.anyio
async def test_urgent_owner_grant_resolution_and_revocation_prevent_dispatch(world, monkeypatch):
    monkeypatch.setenv("PANINI_PRESENCE", "true")
    h, service, observed, now = world
    h.continuity.clock = h.continuity.urgent.clock = lambda: now[0]
    h.continuity.presence.clock = lambda: now[0]
    h.notices.clock = lambda: now[0]
    grant = h.continuity.urgent.propose()
    approve_urgent_grant(h, grant["review_id"])
    first = h.notices.add("responsibility", "Urgent failure", "Private", urgent=True)
    h.notices.mark(first["id"], "read")
    now[0] += 301
    await h.continuity.tick()
    assert h.store.one("SELECT COUNT(*) AS n FROM continuity_urgent_attempts")["n"] == 0
    second = h.notices.add("responsibility", "Urgent failure 2", "Private", urgent=True)
    now[0] += 301
    await h.continuity.tick()
    assert h.store.one("SELECT COUNT(*) AS n FROM continuity_urgent_attempts")["n"] == 1
    h.continuity.urgent.revoke(grant["id"])
    row = h.store.one("SELECT state FROM outbound_briefs WHERE standing_grant_id=?", (grant["id"],))
    assert row["state"] == "cancelled"
    await service.tick()
    assert observed == []
    assert h.notices.get(second["id"])["status"] == "unread"


@pytest.mark.anyio
async def test_urgent_grant_requires_pc_and_cancels_pending_on_resolution(world, monkeypatch):
    monkeypatch.setenv("PANINI_PRESENCE", "true")
    h, service, observed, now = world
    h.continuity.clock = h.continuity.urgent.clock = lambda: now[0]
    h.continuity.presence.clock = lambda: now[0]
    h.notices.clock = lambda: now[0]
    grant = h.continuity.urgent.propose()
    view = h.interrupts.get(grant["review_id"], include_secret=True)
    with pytest.raises(Fault, match="PC"):
        h.interrupts.resolve(
            view["id"],
            nonce=view["nonce"],
            action_digest=view["action_digest"],
            status="resolved",
            payload={"approved": True},
            decided_by={"surface": "phone_app", "method": "tap", "principal_kind": "owner"},
        )
    approve_urgent_grant(h, grant["review_id"])
    notice = h.notices.add("responsibility", "Urgent failure", "Private", urgent=True)
    now[0] += 301
    await h.continuity.tick()
    attempt = h.store.one("SELECT * FROM continuity_urgent_attempts")
    h.notices.mark(notice["id"], "read")
    assert service.get(attempt["brief_id"])["state"] == "cancelled"
    await service.tick()
    assert observed == []


@pytest.mark.anyio
async def test_urgent_grant_waits_for_away_state_and_caps_daily_attempts(world, monkeypatch):
    monkeypatch.setenv("PANINI_PRESENCE", "true")
    h, service, _, now = world
    h.continuity.clock = h.continuity.urgent.clock = lambda: now[0]
    h.continuity.presence.clock = lambda: now[0]
    h.notices.clock = lambda: now[0]
    grant = h.continuity.urgent.propose()
    approve_urgent_grant(h, grant["review_id"])
    h.notices.add("responsibility", "Failure one", "Private", urgent=True)
    h.notices.add("responsibility", "Failure two", "Private", urgent=True)
    now[0] += 301
    h.continuity.presence.signal(
        "pc", {"idle_s": 0, "locked": False, "suspended": False, "avatar_visible": True}
    )
    await h.continuity.tick()
    assert h.store.one("SELECT COUNT(*) AS n FROM continuity_urgent_attempts")["n"] == 0
    now[0] += 31
    await h.continuity.tick()
    assert h.store.one("SELECT COUNT(*) AS n FROM continuity_urgent_attempts")["n"] == 1
    service.update(h.store.one("SELECT brief_id FROM continuity_urgent_attempts")["brief_id"], "ended")
    now[0] += 301
    await h.continuity.tick()
    assert h.store.one("SELECT COUNT(*) AS n FROM continuity_urgent_attempts")["n"] == 1


@pytest.mark.anyio
async def test_rotating_trunk_revokes_urgent_grant_even_at_same_clock_tick(world, monkeypatch):
    monkeypatch.setenv("PANINI_PRESENCE", "true")
    h, service, observed, now = world
    h.continuity.clock = h.continuity.urgent.clock = lambda: now[0]
    h.continuity.presence.clock = lambda: now[0]
    h.notices.clock = lambda: now[0]
    grant = h.continuity.urgent.propose()
    approve_urgent_grant(h, grant["review_id"])
    h.notices.add("responsibility", "Failure", "Private", urgent=True)
    now[0] += 301
    await h.continuity.tick()
    assert h.store.one("SELECT COUNT(*) AS n FROM continuity_urgent_attempts")["n"] == 1
    old = service.settings()["config"]
    service.configure(
        old,
        username="new-test-user",
        password="new-test-password",
        checklist=dict.fromkeys(CHECKLIST, True),
    )
    assert (
        h.store.one("SELECT status FROM continuity_urgent_grants WHERE id=?", (grant["id"],))["status"]
        == "revoked"
    )
    assert (
        h.store.one("SELECT state FROM outbound_briefs WHERE standing_grant_id=?", (grant["id"],))["state"]
        == "cancelled"
    )
    await service.tick()
    assert observed == []


@pytest.mark.anyio
async def test_expired_or_private_urgent_grant_cannot_queue_owner_calls(world, monkeypatch):
    monkeypatch.setenv("PANINI_PRESENCE", "true")
    h, _, observed, now = world
    h.continuity.clock = h.continuity.urgent.clock = lambda: now[0]
    h.continuity.presence.clock = lambda: now[0]
    h.notices.clock = lambda: now[0]
    grant = h.continuity.urgent.propose()
    approve_urgent_grant(h, grant["review_id"])
    h.notices.add("responsibility", "First failure", "Private", urgent=True)
    monkeypatch.setenv("PANINI_LOCAL_MODE", "private")
    now[0] += 301
    await h.continuity.tick()
    assert h.store.one("SELECT COUNT(*) AS n FROM continuity_urgent_attempts")["n"] == 0
    monkeypatch.setenv("PANINI_LOCAL_MODE", "off")
    now[0] = grant["expires"] + 1
    await h.continuity.tick()
    assert h.continuity.urgent.status()["status"] == "unavailable"
    assert h.store.one("SELECT COUNT(*) AS n FROM continuity_urgent_attempts")["n"] == 0
    assert observed == []


@pytest.mark.anyio
async def test_continuity_owner_call_uses_fixed_configured_facts_and_waits_for_exact_approval(
    world, monkeypatch
):
    monkeypatch.setenv("PANINI_PRESENCE", "true")
    h, service, observed, _ = world
    thread = h.sessions.create("Private conversation contents are not disclosed")
    requested = h.continuity.handoff(thread, "call", surface="phone_app")
    row = service.get(requested["brief_id"])
    assert requested["status"] == "review_required"
    assert row["brief"]["number"] == service.settings()["config"]["owner_number"]
    assert row["brief"]["may_commit"] == [] and row["brief"]["max_minutes"] == 2
    assert row["brief"]["voicemail"] == "hang_up"
    assert "Private conversation" not in json.dumps(row["brief"])
    assert row["thread_id"] == thread
    await service.tick()
    assert observed == []
    with pytest.raises(Fault):
        service.approval(row, CallBrief.model_validate(row["brief"]))
    approve(h, row)
    service.approval(service.get(row["id"]), CallBrief.model_validate(row["brief"]))
    # The test qualifies approval binding, not a real SIP call.
    assert observed == []


@pytest.mark.parametrize(
    "number",
    [
        "911",
        "112",
        "999",
        "000",
        "100",
        "108",
        "+91112",
        "+19005551234",
        "+448701234567",
        "+18005551234",
        "+123456789",
        "tel:+14155552671",
    ],
)
def test_blocked_number_classes(number):
    with pytest.raises(ValueError):
        validate(number)


@pytest.mark.parametrize(
    "number,region", [("+14155552671", "US"), ("+442079460018", "GB"), ("+971501234567", "AE")]
)
def test_valid_geographic_numbers(number, region):
    assert validate(number)["region"] == region
    if region == "GB":
        with pytest.raises(ValueError):
            validate("+4402079460018")


def test_calling_window_and_next_window():
    at = datetime(2026, 10, 4, 8, tzinfo=UTC).timestamp()
    assert calling_window("+442079460018", at)["allowed"]
    late = calling_window("+442079460018", at + 11 * 3600)
    assert not late["allowed"] and late["next_at"] > at + 11 * 3600


def test_brief_exclusions_cannot_be_removed_and_payments_are_rejected():
    assert set(EXCLUSIONS).issubset(CallBrief(**BRIEF, must_not_disclose=[]).must_not_disclose)
    with pytest.raises(ValueError):
        CallBrief(**BRIEF, may_commit=["pay a deposit"])
    with pytest.raises(ValueError):
        CallBrief(**BRIEF, arbitrary="replace rules")


@pytest.mark.parametrize(
    "url",
    [
        "sip:provider.example",
        "sips:user:password@provider.example",
        "sips:127.0.0.1",
        "sips:10.0.0.1",
        "sips:localhost",
        "sips:host.local",
        "sips:provider.example/path",
        "sips:provider.example:99999",
    ],
)
def test_trunk_endpoint_blocks_credentials_and_local_destinations(url):
    with pytest.raises(ValueError):
        trunk_url(url)


@pytest.mark.anyio
async def test_checklist_required_and_secrets_are_write_only(world):
    h, service, _, _ = world
    config = {**service.settings()["config"], "caller_number": "+14155552674"}
    service.configure(config)
    assert not service.enabled()
    with pytest.raises(Fault):
        service.draft(BRIEF)
    with pytest.raises(ValueError):
        service.configure(config, checklist={"personal_tasks_only": True})
    assert "test-password" not in json.dumps(service.status())
    assert "test-password" not in h.store.one("SELECT config FROM outbound_settings")["config"]


@pytest.mark.anyio
async def test_unapproved_direct_dispatch_is_refused(world):
    _, service, observed, _ = world
    row = service.draft(BRIEF)
    await service.dial(row["id"])
    assert not observed and service.get(row["id"])["state"] == "cancelled"


@pytest.mark.anyio
async def test_setup_change_cancels_an_already_approved_brief(world):
    h, service, observed, _ = world
    row = service.draft(BRIEF)
    approve(h, row)
    service.configure(
        {**service.settings()["config"], "owner_number": "+442079460018"},
        checklist=dict.fromkeys(CHECKLIST, True),
    )
    await service.tick()
    assert not observed and service.get(row["id"])["state"] == "cancelled"


@pytest.mark.anyio
async def test_policy_is_rechecked_after_approval(world):
    h, service, observed, _ = world
    row = service.draft(BRIEF)
    approve(h, row)
    h.policy.put_grant(Grant("deny-calls", tools=("place_call",), levels={"communicate": "deny"}))
    await service.tick()
    await service.work
    assert not observed and service.get(row["id"])["state"] == "cancelled"
    with pytest.raises(Fault):
        service.draft(BRIEF)


@pytest.mark.anyio
async def test_stronger_policy_after_approval_refuses_stale_tap(world):
    h, service, observed, _ = world
    row = service.draft(BRIEF)
    approve(h, row)
    h.policy.put_grant(Grant("verify-calls", tools=("place_call",), levels={"communicate": "verify"}))
    await service.tick()
    await service.work
    assert not observed and service.get(row["id"])["state"] == "cancelled"


@pytest.mark.anyio
async def test_changed_stored_brief_cannot_reuse_old_approval(world):
    h, service, observed, _ = world
    row = service.draft(BRIEF)
    approve(h, row)
    changed = {**row["brief"], "purpose": "Changed after approval"}
    h.store.execute("UPDATE outbound_briefs SET brief=? WHERE id=?", (json.dumps(changed), row["id"]))
    await service.tick()
    await service.work
    assert not observed and service.get(row["id"])["state"] == "cancelled"


@pytest.mark.anyio
async def test_attach_failure_hangs_up_known_session(world):
    h, service, observed, _ = world

    class BrokenConnection(Connection):
        async def attach(self, session):
            raise RuntimeError("provider body must not leak")

    service.connection_factory = BrokenConnection
    row = service.draft(BRIEF)
    approve(h, row)
    await service.tick()
    await service.work
    assert not service.active and [item[0] for item in observed] == ["create", "hangup"]
    assert service.get(row["id"])["receipt_id"] and "provider body" not in str(service.status())


@pytest.mark.anyio
async def test_cancel_during_creation_retains_unknown_without_retry(world):
    h, service, observed, _ = world
    started = asyncio.Event()

    async def pending(*args, **kwargs):
        observed.append(("pending-create", None, None))
        started.set()
        await asyncio.sleep(30)

    service.provider = pending
    row = service.draft(BRIEF)
    approve(h, row)
    await service.tick()
    await started.wait()
    await service.close()
    await service.tick()
    assert service.get(row["id"])["state"] == "unknown" and len(observed) == 1


@pytest.mark.anyio
async def test_flag_off_cancels_review_and_ends_active_call(world, monkeypatch):
    h, service, observed, _ = world
    row = service.draft(BRIEF)
    approve(h, row)
    await service.tick()
    await service.work
    monkeypatch.setenv("PANINI_OUTBOUND_CALLS", "false")
    await service.tick()
    assert not service.active and observed[-1][0] == "hangup"
    monkeypatch.setenv("PANINI_OUTBOUND_CALLS", "true")
    pending = service.draft(BRIEF)
    monkeypatch.setenv("PANINI_OUTBOUND_CALLS", "false")
    await service.tick()
    assert service.get(pending["id"])["state"] == "cancelled"


@pytest.mark.anyio
async def test_edit_replaces_nonce_digest_and_stronger_review(world):
    h, service, observed, _ = world
    row = service.draft(BRIEF)
    old = h.interrupts.get(row["interrupt_id"], include_secret=True)
    changed = service.draft(
        {**BRIEF, "purpose": "Ask a different question", "number_source": "web"}, identifier=row["id"]
    )
    review = h.interrupts.get(changed["interrupt_id"], include_secret=True)
    assert (
        review["nonce"] != old["nonce"]
        and review["action_digest"] != old["action_digest"]
        and review["level"] == "verify"
    )
    assert h.interrupts.get(old["id"])["status"] == "cancelled"
    for surface in ("phone_line", "telegram"):
        with pytest.raises(Fault):
            approve(h, changed, surface=surface, method="local")
    with pytest.raises(Fault):
        approve(h, changed)
    approve(h, changed, surface="console", method="local")
    await service.tick()
    await service.work
    assert [item[0] for item in observed] == ["create"]


@pytest.mark.anyio
async def test_lifecycle_disclosure_notes_transfer_and_receipt(world):
    h, service, observed, _ = world
    row = service.draft(BRIEF)
    approve(h, row)
    await service.tick()
    await service.work
    call = service.active
    assert observed[0][2]["transport"]["type"] == "sip"
    assert observed[0][2]["session"]["store"] is False
    await service.event(call, {"type": "transport.ringing"})
    await service.event(call, {"type": "transport.answered"})
    disclosure = call["brief"].disclosure()
    await service.event(call, {"type": "transcript", "role": "assistant", "delta": disclosure[:20]})
    await service.event(call, {"type": "transcript", "role": "assistant", "delta": disclosure[20:]})
    assert call["disclosed"] and service.get(row["id"])["state"] == "answered"
    await service.control(row["id"], "note", "Afternoon slots only")
    assert call["connection"].sent[0][0] == "session.instructions.append"
    await service.control(row["id"], "transfer")
    assert observed[-1] == ("refer", call["session"], {"target_uri": "tel:+14155552673"})
    assert (
        service.active is call
        and not call["connection"].closed
        and service.get(row["id"])["state"] == "transferring"
    )
    with pytest.raises(Fault):
        await service.control(row["id"], "transfer")
    await service.event(call, {"type": "call.closed"})
    await asyncio.sleep(0.01)
    assert not any(item[0] == "hangup" for item in observed)
    final = service.get(row["id"])
    assert final["receipt_id"] and final["transcript_artifact_id"] and final["outcome"]["status"] == "partial"
    assert "unconfirmed" in final["outcome"]["summary"] and verify(h.store)["ok"]
    await service.tick()
    assert sum(item[0] == "create" for item in observed) == 1


@pytest.mark.anyio
async def test_no_disclosure_timeout_hangs_up(world):
    h, service, observed, now = world
    row = service.draft(BRIEF)
    approve(h, row)
    await service.tick()
    await service.work
    await service.event(service.active, {"type": "transport.answered"})
    now[0] += 11
    await service.tick()
    assert service.active is None and observed[-1][0] == "hangup"
    assert "disclosure" in service.get(row["id"])["reason"].lower()


@pytest.mark.anyio
async def test_unconfirmed_transfer_is_bounded_and_hangs_up(world):
    h, service, observed, now = world
    row = service.draft(BRIEF)
    approve(h, row)
    await service.tick()
    await service.work
    call = service.active
    await service.event(call, {"type": "transport.answered"})
    await service.event(
        call, {"type": "transcript", "role": "assistant", "delta": call["brief"].disclosure()}
    )
    await service.control(row["id"], "transfer")
    now[0] += 31
    await service.tick()
    assert not service.active and observed[-1][0] == "hangup"
    assert "Transfer completion" in service.get(row["id"])["reason"]


@pytest.mark.anyio
async def test_incorrect_first_output_ends_immediately(world):
    h, service, observed, _ = world
    row = service.draft(BRIEF)
    approve(h, row)
    await service.tick()
    await service.work
    await service.event(
        service.active, {"type": "transcript", "role": "assistant", "delta": "Hi, I am a human"}
    )
    await asyncio.sleep(0.01)
    assert not service.active and observed[-1][0] == "hangup"


@pytest.mark.anyio
async def test_ambiguous_creation_is_not_retried_and_restart_hangs_up_once(world):
    h, service, observed, _ = world
    row = service.draft(BRIEF)
    approve(h, row)

    async def timeout(*args, **kwargs):
        observed.append(("create_timeout", None, None))
        raise httpx.ReadTimeout("secret provider payload")

    real = service.provider
    service.provider = timeout
    await service.tick()
    await service.work
    await service.tick()
    assert service.get(row["id"])["state"] == "unknown" and len(observed) == 1
    service.provider = real
    row2 = service.draft(BRIEF)
    approve(h, row2)
    await service.tick()
    await service.work
    await service.active["connection"].close()
    service.active = None  # Simulate the process disappearing with its persisted attempt still live.
    reopened = Harness(Store(h.store.path), model_factory=lambda: ScriptedModel([{"text": "No effects"}]))
    restarted = OutboundCalls(reopened, provider=real, secrets=service.secrets, clock=service.clock)
    await restarted.recover()
    assert service.get(row2["id"])["state"] == "unknown" and observed[-1][0] == "hangup"
    before = len(observed)
    await restarted.recover()
    assert len(observed) == before
    await reopened.close()


@pytest.mark.anyio
async def test_pause_and_block_rechecked_after_approval(world):
    h, service, observed, _ = world
    row = service.draft(BRIEF)
    approve(h, row)
    service.block(BRIEF["number"])
    await service.tick()
    await service.work
    assert not observed and service.get(row["id"])["state"] == "cancelled"


@pytest.mark.anyio
async def test_midcall_payment_refusal_and_owner_decision(world):
    h, service, _, _ = world
    row = service.draft(BRIEF)
    approve(h, row)
    await service.tick()
    await service.work
    call = service.active
    call["disclosed"] = True
    await service.request(call, "Pay a deposit", "payment")
    assert "cannot" in call["connection"].spoken[-1]
    work = asyncio.create_task(service.request(call, "May we offer a different slot?", "slot"))
    await asyncio.sleep(0.01)
    view = h.interrupts.get(next(iter(call["pending"])), include_secret=True)
    h.interrupts.resolve(
        view["id"],
        nonce=view["nonce"],
        action_digest=digest(view["action"]),
        status="resolved",
        payload={"approved": True},
        decided_by={"surface": "phone_app", "method": "tap"},
    )
    await work
    assert "within the reviewed brief" in call["connection"].spoken[-1]
    await service.event(call, {"type": "transcript", "role": "user", "delta": "Do not "})
    await service.event(call, {"type": "transcript", "role": "user", "delta": "call me again"})
    await asyncio.sleep(0.01)
    assert (
        not service.active
        and service.store.one("SELECT number FROM outbound_do_not_call")["number"] == BRIEF["number"]
    )


@pytest.mark.anyio
async def test_daily_attempt_limit_and_calling_hours(world, monkeypatch):
    h, service, observed, now = world
    monkeypatch.setenv("PANINI_OUTBOUND_PER_DAY", "1")
    row = service.draft(BRIEF)
    approve(h, row)
    await service.tick()
    await service.work
    await service.end()
    row2 = service.draft(BRIEF)
    approve(h, row2)
    await service.tick()
    await service.work
    assert sum(item[0] == "create" for item in observed) == 1
    now[0] += 3600 * 14
    row3 = service.draft(BRIEF)
    approve(h, row3)
    await service.tick()
    await service.work
    assert sum(item[0] == "create" for item in observed) == 1


@pytest.mark.anyio
async def test_transport_events_reach_lifecycle_callback():
    events = []

    async def callback(event):
        events.append(event)

    connection = OutboundConnection("fake", on_event=callback, on_request=callback)
    await connection.handle_event({"type": "transport.answered"})
    assert events == [{"type": "transport.answered"}]


@pytest.mark.anyio
async def test_console_boundary_and_no_credential_echo(world, monkeypatch):
    h, _, _, _ = world
    monkeypatch.setenv("PANINI_PRESENCE", "true")
    client = TestClient(
        create_app(console_token="owner-test-token", harness_factory=lambda: h), base_url="http://127.0.0.1"
    )
    try:
        assert client.get("/api/harness/outbound").status_code == 401
        response = client.get("/api/harness/outbound", headers={"x-panini-console": "owner-test-token"})
        assert response.status_code == 200 and "test-password" not in response.text
        rejected = client.post(
            "/api/harness/outbound/settings",
            headers={"x-panini-console": "owner-test-token"},
            json={"password": "credential-must-not-echo", "arbitrary": True},
        )
        assert rejected.status_code == 422 and "credential-must-not-echo" not in rejected.text
        assert client.post("/api/harness/presence/urgent-grants").status_code == 401
        proposed = client.post(
            "/api/harness/presence/urgent-grants",
            headers={"x-panini-console": "owner-test-token"},
        )
        assert proposed.status_code == 200 and proposed.json()["status"] == "proposed"
        revoked = client.delete(
            "/api/harness/presence/urgent-grants/" + proposed.json()["id"],
            headers={"x-panini-console": "owner-test-token"},
        )
        assert revoked.status_code == 200 and revoked.json()["status"] == "off"
    finally:
        client.close()
