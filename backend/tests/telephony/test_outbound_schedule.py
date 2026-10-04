import json
from datetime import UTC, datetime

import pytest
from panini_cli.agent.model import ScriptedModel

from backend.harness.runtime import Harness
from backend.harness.store import Store
from backend.panini.contracts import Fault
from backend.policy.engine import Grant
from backend.telephony.outbound import OutboundCalls
from backend.telephony.schedule import CallSchedule

from . import test_outbound
from .test_outbound import BRIEF, Connection, approve

world = test_outbound.world


@pytest.fixture
def anyio_backend():
    return "asyncio"


def later(now, seconds=3600):
    return {"mode": "at", "at": datetime.fromtimestamp(now + seconds, UTC).isoformat()}


@pytest.mark.parametrize(
    "schedule",
    [
        {"mode": "at"},
        {"mode": "next_window", "at": "2026-10-05T09:00:00Z"},
        {"mode": "at", "at": "2026-10-05T09:00:00"},
        {"mode": "at", "at": "not-a-date"},
        {"mode": "at", "at": "2026-10-03T09:00:00Z"},
        {"mode": "at", "at": "2026-10-20T09:00:00Z"},
        {"mode": "at", "at": "2026-10-05T02:00:00Z"},
        {"mode": "at", "at": "2026-10-05T09:00:00Z", "retry": True},
    ],
)
def test_schedule_rejects_missing_zone_bad_dates_hours_and_unbounded_times(schedule):
    now = datetime(2026, 10, 4, 8, tzinfo=UTC).timestamp()
    with pytest.raises(ValueError):
        CallSchedule.model_validate(schedule).resolve("+442079460018", now)


def test_next_window_and_local_cutoff():
    now = datetime(2026, 10, 4, 22, tzinfo=UTC).timestamp()
    plan = CallSchedule(mode="next_window").resolve("+442079460018", now)
    assert plan["at_utc"] == "2026-10-05T08:00:00+00:00"
    assert plan["latest_start"] - plan["not_before"] == 900
    at = datetime(2026, 10, 5, 18, 58, tzinfo=UTC).timestamp()
    cutoff = CallSchedule.model_validate(later(at, 0)).resolve("+442079460018", at)
    assert cutoff["latest_start"] - cutoff["not_before"] == 120


@pytest.mark.anyio
async def test_schedule_waits_then_dispatches_exactly_once(world):
    h, service, observed, now = world
    row = service.draft(BRIEF, schedule=later(now[0]))
    review = h.interrupts.get(row["interrupt_id"])
    assert review["action"]["schedule"] == row["schedule"]
    assert review["preview"]["schedule"] == row["schedule"]
    approve(h, row)
    await service.tick()
    await service.work
    assert service.get(row["id"])["state"] == "scheduled" and not observed
    await service.dial(row["id"])
    assert not observed
    now[0] += 3600
    await service.tick()
    await service.work
    assert [item[0] for item in observed] == ["create"]
    await service.end()
    await service.tick()
    assert sum(item[0] == "create" for item in observed) == 1


@pytest.mark.anyio
async def test_outside_hours_explicit_next_window_waits_and_requires_approval(world):
    h, service, observed, now = world
    now[0] = datetime(2026, 10, 5, 3, tzinfo=UTC).timestamp()
    row = service.draft(BRIEF, schedule={"mode": "next_window"})
    await service.tick()
    assert service.work is None and not observed
    approve(h, row)
    await service.tick()
    await service.work
    assert not observed and service.get(row["id"])["state"] == "scheduled"
    now[0] = row["schedule"]["not_before"]
    await service.tick()
    await service.work
    assert observed[0][0] == "create"


@pytest.mark.anyio
async def test_schedule_edit_invalidates_old_approval(world):
    h, service, observed, now = world
    row = service.draft(BRIEF, schedule=later(now[0]))
    old = h.interrupts.get(row["interrupt_id"], include_secret=True)
    updated = service.draft(BRIEF, identifier=row["id"], schedule=later(now[0], 7200))
    assert updated["digest"] != row["digest"] and updated["schedule"] != row["schedule"]
    assert h.interrupts.get(old["id"])["status"] == "cancelled"
    approve(h, updated)
    await service.tick()
    await service.work
    with pytest.raises(Fault):
        service.draft(BRIEF, identifier=row["id"], schedule=later(now[0], 3600))
    assert not observed


@pytest.mark.anyio
@pytest.mark.parametrize(
    "change", ["tamper", "flag", "pause", "block", "policy", "stronger", "config", "missed"]
)
async def test_scheduled_revocation_and_missed_window_never_dial(world, monkeypatch, change):
    h, service, observed, now = world
    row = service.draft(BRIEF, schedule=later(now[0]))
    approve(h, row)
    await service.tick()
    await service.work
    if change == "tamper":
        h.store.execute(
            "UPDATE outbound_briefs SET schedule=? WHERE id=?",
            (json.dumps({**row["schedule"], "not_before": now[0]}), row["id"]),
        )
    elif change == "flag":
        monkeypatch.setenv("PANINI_OUTBOUND_CALLS", "false")
    elif change == "pause":
        h.policy.pause(True)
    elif change == "block":
        service.block(BRIEF["number"])
    elif change in {"policy", "stronger"}:
        h.policy.put_grant(
            Grant(
                "changed-call-policy",
                tools=("place_call",),
                levels={"communicate": "deny" if change == "policy" else "verify"},
            )
        )
    elif change == "config":
        service.configure({**service.settings()["config"], "owner_number": "+14155552674"})
    else:
        now[0] = row["schedule"]["latest_start"]
    await service.tick()
    assert not observed and service.get(row["id"])["state"] == "cancelled"
    monkeypatch.setenv("PANINI_OUTBOUND_CALLS", "true")
    await service.tick()
    assert not observed


@pytest.mark.anyio
async def test_pending_call_cancellation_and_expiry_are_terminal(world):
    h, service, observed, now = world
    row = service.draft(BRIEF, schedule=later(now[0]))
    await service.control(row["id"], "cancel")
    assert h.interrupts.get(row["interrupt_id"])["status"] == "cancelled"
    assert service.get(row["id"])["state"] == "cancelled"
    with pytest.raises(Fault):
        approve(h, row)
    row2 = service.draft(BRIEF, schedule=later(now[0]))
    now[0] += 3601
    h.interrupts.expire_overdue()
    await service.tick()
    assert service.get(row2["id"])["state"] == "cancelled" and not observed


@pytest.mark.anyio
async def test_schedule_survives_actual_store_reopen_but_missed_calls_do_not_replay(world):
    h, service, observed, now = world
    row = service.draft(BRIEF, schedule=later(now[0]))
    approve(h, row)
    await service.tick()
    await service.work
    reopened = Harness(Store(h.store.path), model_factory=lambda: ScriptedModel([{"text": "No effects"}]))
    resumed = reopened.outbound = OutboundCalls(
        reopened,
        provider=service.provider,
        connection_factory=Connection,
        secrets=service.secrets,
        clock=service.clock,
    )
    try:
        await resumed.recover()
        await resumed.tick()
        assert not observed and resumed.get(row["id"])["state"] == "scheduled"
        now[0] = row["schedule"]["not_before"]
        await resumed.tick()
        await resumed.work
        assert observed[0][0] == "create"
        await resumed.end()
        # Persist an approved future call, then reopen after its deadline.
        row2 = resumed.draft(BRIEF, schedule=later(now[0]))
        approve(reopened, row2)
        await resumed.tick()
        await resumed.work
        now[0] = row2["schedule"]["latest_start"] + 1
        await resumed.recover()
        await resumed.tick()
        assert resumed.get(row2["id"])["state"] == "cancelled"
        assert sum(item[0] == "create" for item in observed) == 1
    finally:
        await reopened.close()


@pytest.mark.anyio
async def test_busy_call_does_not_defer_schedule_past_deadline(world):
    h, service, observed, now = world
    first = service.draft(BRIEF)
    approve(h, first)
    await service.tick()
    await service.work
    scheduled = service.draft(BRIEF, schedule=later(now[0], 60))
    approve(h, scheduled)
    # Dial only promotes it once the active line is free; its original deadline still applies.
    now[0] = scheduled["schedule"]["latest_start"] + 1
    await service.end()
    await service.tick()
    await service.work
    assert service.get(scheduled["id"])["state"] == "cancelled"
    assert sum(item[0] == "create" for item in observed) == 1


@pytest.mark.anyio
async def test_owner_agent_schedules_through_review_and_channel_has_no_call_tool(world):
    h, service, observed, now = world

    async def delegate(*args, **kwargs):
        raise AssertionError("No desktop or connector effect is needed to draft a call.")

    agent = h.agent(delegate=delegate)
    import jsonschema

    from backend.brain.control_tools import place_call

    tool = next(item for item in agent.tools() if item["name"] == "place_call")
    from backend.telephony.brief import CallBrief

    arguments = {"brief": CallBrief.model_validate(BRIEF).model_dump(), "schedule": {**later(now[0])}}
    jsonschema.validate(arguments, tool["parameters"])
    for model in (
        tool["parameters"]["properties"]["brief"],
        tool["parameters"]["properties"]["schedule"]["anyOf"][0],
    ):
        assert set(model["properties"]) == set(model["required"])
    result = place_call(agent, BRIEF, arguments["schedule"])
    assert result["status"] == "review" and result["schedule"]["not_before"] == now[0] + 3600
    assert service.get(result["call_brief_id"])["state"] == "review" and not observed
    assert not any(
        item["name"] == "place_call" for item in h.agent(delegate=delegate, surface="telegram").tools()
    )


@pytest.mark.anyio
async def test_vault_failure_cannot_reuse_old_approval_after_setup_change(world, monkeypatch):
    h, service, observed, now = world
    row = service.draft(BRIEF, schedule=later(now[0]))
    approve(h, row)
    await service.tick()
    await service.work

    def failed_write(*args):
        raise OSError("Simulated credential persistence failure.")

    monkeypatch.setattr(service.secrets, "set", failed_write)
    with pytest.raises(OSError):
        service.configure(
            service.settings()["config"],
            username="replacement",
            password="test-replacement-password",
            checklist=dict.fromkeys(test_outbound.CHECKLIST, True),
        )
    assert not service.enabled() and service.get(row["id"])["state"] == "cancelled"
    now[0] = row["schedule"]["not_before"]
    await service.tick()
    assert not observed
