import asyncio

import pytest

from backend.ledger.chain import verify
from backend.telephony.outbound import OutboundConnection

from . import test_outbound
from .test_outbound import BRIEF, Connection, approve

world = test_outbound.world


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def start(h, service):
    row = service.draft(BRIEF)
    approve(h, row)
    await service.tick()
    await service.work
    return row, service.active


@pytest.mark.anyio
async def test_final_usage_is_exact_cumulative_and_receipt_linked(world):
    h, service, observed, _ = world
    row, call = await start(h, service)
    for seconds in (12, 15, 13):
        await service.event(call, {"type": "session.usage.updated", "usage": {"seconds": seconds}})
    assert call["usage_seconds"] == 15
    await service.event(call, {"type": "call.closed", "usage": {"seconds": 17}, "reason": "remote_hangup"})
    await asyncio.sleep(0.01)
    final = service.get(row["id"])["outcome"]
    assert final["final_usage_seconds"] == 17 and final["finalization_confirmed"] is True
    assert final["usage_seconds"] == 17 and final["cost_usd"] is None and not final["cost_confirmed"]
    assert final["commitments"] == [] and final["status"] == "partial"
    assert not any(item[0] == "hangup" for item in observed) and verify(h.store)["ok"]


@pytest.mark.anyio
async def test_final_usage_received_while_end_is_draining_is_preserved(world):
    h, service, _, _ = world

    class Draining(Connection):
        async def close(self):
            await self.on_event({"type": "call.closed", "usage": {"seconds": 21.5}})
            await super().close()

    service.connection_factory = Draining
    row, _ = await start(h, service)
    await service.end(reason="Owner ended.")
    outcome = service.get(row["id"])["outcome"]
    assert outcome["final_usage_seconds"] == 21.5 and outcome["finalization_confirmed"]


@pytest.mark.anyio
async def test_disconnect_retains_observed_usage_but_does_not_claim_finalization(world):
    h, service, _, _ = world
    row, call = await start(h, service)
    await service.event(call, {"type": "session.usage.updated", "usage": {"seconds": 12}})
    await service.event(call, {"type": "disconnected"})
    await asyncio.sleep(0.01)
    final = service.get(row["id"])["outcome"]
    assert final["status"] == "failed" and final["usage_seconds"] == 12
    assert final["final_usage_seconds"] is None and not final["finalization_confirmed"]


@pytest.mark.anyio
@pytest.mark.parametrize("seconds", [True, "15", -1, float("nan"), float("inf"), 7201])
async def test_malformed_final_usage_never_becomes_accounting_evidence(world, seconds):
    h, service, _, _ = world
    row, call = await start(h, service)
    await service.event(call, {"type": "call.closed", "usage": {"seconds": seconds}})
    await asyncio.sleep(0.01)
    final = service.get(row["id"])["outcome"]
    assert final["finalization_confirmed"] and final["final_usage_seconds"] is None
    assert final["cost_usd"] is None


@pytest.mark.anyio
async def test_keypad_notifications_are_deduplicated_and_digits_not_saved(world):
    h, service, _, _ = world
    row, call = await start(h, service)
    for _ in range(2):
        await service.event(
            call,
            {
                "type": "transport.dtmf.send",
                "event_id": "sent-1",
                "session_id": call["session"],
                "event": "2",
            },
        )
    await service.event(
        call,
        {
            "type": "transport.dtmf.received",
            "event_id": "other-session",
            "session_id": "live_wrong",
            "event": "9",
        },
    )
    await service.event(call, {"type": "transport.dtmf.received", "event_id": "invalid-key"})
    await service.event(call, {"type": "transport.dtmf.received", "event_id": "received-1", "event": "#"})
    await service.end()
    assert service.get(row["id"])["outcome"]["keypad_events"] == {"sent": 1, "received": 1}
    assert not call["connection"].sent  # Notifications do not imply a supported command to send tones.


@pytest.mark.anyio
async def test_failure_code_only_is_retained_and_ringing_timeout_classified(world):
    h, service, _, now = world
    row, call = await start(h, service)
    await service.event(
        call,
        {
            "type": "transport.failed",
            "error": {"code": "provider_busy", "message": "private-provider-payload"},
        },
    )
    await asyncio.sleep(0.01)
    final = service.get(row["id"])["outcome"]
    assert final["provider_error_code"] == "provider_busy" and "private-provider-payload" not in str(
        service.status()
    )
    row2, _ = await start(h, service)
    now[0] += 181
    await service.tick()
    assert service.get(row2["id"])["outcome"]["status"] == "no_answer"


@pytest.mark.anyio
async def test_sideband_adapter_forwards_usage_and_close():
    events = []

    async def callback(event):
        events.append(event)

    connection = OutboundConnection("fake", on_event=callback, on_request=callback)
    await connection.handle_event({"type": "session.usage.updated", "usage": {"seconds": 3}})
    await connection.handle_event(
        {"type": "session.closed", "usage": {"seconds": 4}, "reason": "remote_hangup"}
    )
    assert events == [
        {"type": "session.usage.updated", "usage": {"seconds": 3}},
        {"type": "call.closed", "usage": {"seconds": 4}, "reason": "remote_hangup"},
    ]
    assert connection.finalized.is_set()
