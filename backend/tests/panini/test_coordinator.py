import asyncio
from uuid import uuid4

import pytest

from backend.capabilities.contracts import Outcome, UnavailableAdapter
from backend.panini.contracts import ConfirmationDecision, Fault, Turn
from backend.panini.coordinator import Coordinator
from backend.panini.events import EventHub
from backend.panini.settings import Settings


@pytest.fixture
def anyio_backend():
    return "asyncio"


class FixtureAdapter(UnavailableAdapter):
    def __init__(self, hold=False, confirm=False, verified=True):
        super().__init__("search", "")
        self.hold, self.confirm, self.verified = hold, confirm, verified
        self.calls = 0
        self.entered = asyncio.Event()
        self.released = 0

    async def availability(self):
        return {"capability": "search", "state": "ready", "reason": ""}

    async def run(self, context, instruction, target):
        self.calls += 1
        self.entered.set()
        if self.confirm:
            await context.confirm({"action": "fixture"}, "Fixture", "Submit local fixture", "Synthetic")
        if self.hold:
            await asyncio.Event().wait()
        evidence = (
            context.record_evidence("fixture", "Fixture verified.", {"fixture": True})
            if self.verified
            else None
        )
        return Outcome("verified_complete", "Fixture verified.", evidence_ids=[evidence] if evidence else [])

    async def release(self, task_id):
        self.released += 1


async def setup(adapter=None, **options):
    settings = Settings(enabled=True, bootstrap_token="x" * 40, **options)
    c = Coordinator(settings, {"search": adapter or FixtureAdapter()})
    token, p = c.auth.issue("controller")
    view = await c.create_session(p, uuid4())
    s = c.get(view["session_id"])
    lease = await c.acquire(s, p)
    return c, p, s, lease["lease_epoch"]


def turn(**values):
    return Turn(command_id=uuid4(), text="Find fixture sources", capability_hint="search", **values)


@pytest.mark.anyio
async def test_only_one_controller_can_acquire_and_create_session():
    c, p, s, epoch = await setup()
    _, other = c.auth.issue("controller")
    with pytest.raises(Fault, match="Another PANINI"):
        await c.acquire(s, other)
    with pytest.raises(Fault, match="already exists"):
        await c.create_session(other, uuid4())
    await c.close()


@pytest.mark.anyio
async def test_duplicate_command_does_not_reexecute_and_payload_reuse_rejected():
    adapter = FixtureAdapter(hold=True)
    c, p, s, epoch = await setup(adapter)
    payload = turn()
    first = await c.submit(s, p, epoch, payload)
    assert await c.submit(s, p, epoch, payload) == first
    with pytest.raises(Fault, match="different text"):
        await c.submit(s, p, epoch, payload.model_copy(update={"text": "Different"}))
    await adapter.entered.wait()
    assert adapter.calls == 1
    await c.stop(s)
    assert adapter.released == 1 and s.active is None
    await c.close()


@pytest.mark.anyio
async def test_immediate_stop_before_coroutine_starts_releases_slot():
    c, p, s, epoch = await setup()
    await c.submit(s, p, epoch, turn())
    await c.stop(s)
    assert s.active is None
    assert not s.unsafe
    await c.close()


@pytest.mark.anyio
async def test_stop_during_planning_prevents_late_adapter_action():
    adapter = FixtureAdapter()
    c, p, s, epoch = await setup(adapter)
    entered = asyncio.Event()

    async def route(*_):
        entered.set()
        await asyncio.Event().wait()

    c.route = route
    await c.submit(s, p, epoch, turn())
    await entered.wait()
    await c.stop(s)
    assert adapter.calls == 0
    assert s.last_task.outcome == "cancelled"
    await c.close()


@pytest.mark.anyio
async def test_expired_lease_cannot_be_renewed_and_stops_work():
    adapter = FixtureAdapter(hold=True)
    c, p, s, epoch = await setup(adapter)
    await c.submit(s, p, epoch, turn())
    await adapter.entered.wait()
    s.expires = c.clock() - 1
    with pytest.raises(Fault, match="disconnected"):
        c.renew(s, p, epoch, 1)
    await c.expire(s)
    assert s.state == "suspended" and s.active is None
    acquired = await c.acquire(s, p)
    assert acquired["lease_epoch"] > epoch
    with pytest.raises(Fault):
        c.authorize(s, p, epoch)
    await c.close()


@pytest.mark.anyio
async def test_observer_disconnect_does_not_own_session_or_task():
    adapter = FixtureAdapter(hold=True)
    c, p, s, epoch = await setup(adapter)
    sub, _ = s.hub.subscribe(0)
    await c.submit(s, p, epoch, turn())
    await adapter.entered.wait()
    s.hub.unsubscribe(sub)
    assert s.active and s.state == "ready" and adapter.released == 0
    await c.close()


@pytest.mark.anyio
async def test_unverified_executor_cannot_claim_completed():
    c, p, s, epoch = await setup(FixtureAdapter(verified=False))
    await c.submit(s, p, epoch, turn())
    await s.active.runner
    assert s.last_task.state == "blocked" and s.last_task.outcome == "unknown_effect"
    await c.close()


@pytest.mark.anyio
async def test_confirmation_digest_nonce_and_stop_race():
    adapter = FixtureAdapter(confirm=True)
    c, p, s, epoch = await setup(adapter)
    await c.submit(s, p, epoch, turn())
    await adapter.entered.wait()
    await asyncio.sleep(0)
    item = next(iter(s.confirmations.values()))
    with pytest.raises(Fault, match="changed"):
        c.decide(
            s,
            item["confirmation_id"],
            ConfirmationDecision(
                command_id=uuid4(), nonce="z" * 40, digest=item["digest"], decision="approve"
            ),
        )
    await c.stop(s)
    assert s.last_task.outcome == "cancelled"
    assert not s.confirmations
    await c.close()


def test_event_cursor_overflow_and_projection_are_bounded():
    hub = EventHub("runtime", "session", max_events=3)
    sub, _ = hub.subscribe(0)
    for _ in range(140):
        hub.publish("task.progress", {"message": "fixture"})
    assert sub.overflow and sub not in hub.subscribers
    assert len(hub.history) == 3
    with pytest.raises(Fault, match="snapshot"):
        hub.subscribe(0)
    sub, backlog = hub.subscribe(138)
    assert [e["sequence"] for e in backlog] == [139, 140]
    hub.publish("task.completed", {})
    assert sub.queue.get_nowait()[0]["sequence"] == 141


def test_boot_scoped_tokens_and_one_use_observer_ticket():
    c = Coordinator(Settings(enabled=True, bootstrap_token="x" * 40), {})
    ticket = c.auth.ticket("observer", "session")
    token, p = c.auth.exchange(ticket, "observer")
    with pytest.raises(Fault):
        c.auth.exchange(ticket, "observer")
    with pytest.raises(Fault):
        c.auth.resolve(token, "other-boot")
    with pytest.raises(Fault):
        c.controller_role(p)
    event = {"type": "caption.user", "data": {"text": "private"}, "sequence": 1}
    assert EventHub.project(event, p)["data"] == {"redacted": True}
    assert event["data"]["text"] == "private"


def test_invalid_flag_combination_and_missing_bootstrap_fail_closed():
    with pytest.raises(ValueError):
        Settings(desktop_enabled=True)
    with pytest.raises(ValueError):
        Settings(enabled=True)


@pytest.mark.anyio
async def test_control_retries_share_one_execution_and_release_waiter_locks():
    c, p, s, epoch = await setup()
    command = uuid4()
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def execute():
        nonlocal calls
        calls += 1
        entered.set()
        await release.wait()
        return {"applied": True}

    async def request():
        return await c.control(s, p, epoch, command, "fixture", {}, execute)

    first = asyncio.create_task(request())
    await entered.wait()
    second = asyncio.create_task(request())
    await asyncio.sleep(0)
    release.set()
    assert await first == await second == {"applied": True}
    assert calls == 1 and not s.control_locks
    assert await request() == {"applied": True}
    assert not s.control_locks
    with pytest.raises(Fault):
        await c.control(s, p, epoch + 1, uuid4(), "fixture", {}, execute)
    assert not s.control_locks
    await c.close()


class FailingCleanupAdapter(FixtureAdapter):
    async def session_end(self, session_id):
        raise RuntimeError("socket already closed")


class ManualClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


@pytest.mark.anyio
async def test_watchdog_survives_adapter_cleanup_failure_and_purges_sessions():
    clock = ManualClock()
    settings = Settings(enabled=True, bootstrap_token="x" * 40, lease_seconds=1, retention_seconds=1)
    c = Coordinator(settings, {"search": FailingCleanupAdapter()}, clock=clock)
    _, p = c.auth.issue("controller")
    s = c.get((await c.create_session(p, uuid4()))["session_id"])
    await c.acquire(s, p)
    c.start()
    clock.now = 5  # The controller stopped renewing: suspend and retain.
    await asyncio.sleep(0.7)
    clock.now = 10  # Retention elapsed: end() runs and an adapter raises.
    await asyncio.sleep(0.7)
    assert not c.watchdog.done()
    assert s.session_id not in c.sessions
    await c.close()


@pytest.mark.anyio
async def test_end_completes_every_cleanup_step_when_an_adapter_fails():
    c, p, s, _ = await setup(FailingCleanupAdapter())
    s.selected["browser"] = {"label": "Tab", "target_ref": "ref"}
    assert await c.end(s) == {"status": "closed"}
    assert s.state == "closed" and not s.selected
    assert s.hub.sequence and not s.unsafe
    await c.close()
