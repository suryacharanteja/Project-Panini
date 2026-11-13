import asyncio
from unittest.mock import AsyncMock

import pytest

from backend.telephony.inbound import CallService

from . import test_inbound

world = test_inbound.world
anyio_backend = test_inbound.anyio_backend
HEADERS = test_inbound.HEADERS


@pytest.mark.anyio
async def test_caller_can_end_without_unlocking_owner_access(world):
    service, h = world
    await service.incoming("unverified_end", HEADERS)
    call = service.active
    service.compute = AsyncMock()
    await service.request(call, "Please end this call.")
    await asyncio.sleep(0.02)
    assert service.active is None and not call.verified
    service.compute.assert_not_awaited()
    assert (
        h.store.one("SELECT reason FROM calls WHERE id='unverified_end'")["reason"]
        == "Caller ended the call."
    )


@pytest.mark.anyio
async def test_late_transcript_preserves_ended_state_and_original_artifact(world):
    service, h = world
    await service.incoming("late", HEADERS)
    await test_inbound.pin(service)
    call = service.active
    await service.event(call, {"type": "transcript", "role": "user", "delta": "First fragment"})
    await service.end()
    before = h.store.one("SELECT * FROM calls WHERE id='late'")
    original = h.artifacts.get(before["transcript_artifact_id"])[1]
    await service.event(call, {"type": "transcript", "role": "assistant", "delta": "Late fragment"})
    after = h.store.one("SELECT * FROM calls WHERE id='late'")
    for key in ("state", "ended", "duration_s", "receipt_id", "receipt_synced"):
        assert after[key] == before[key]
    assert after["transcript_artifact_id"] != before["transcript_artifact_id"]
    assert h.artifacts.get(before["transcript_artifact_id"])[1] == original
    assert b"Late fragment" in h.artifacts.get(after["transcript_artifact_id"])[1]
    assert h.store.one("SELECT COUNT(*) AS n FROM receipts")["n"] == 1
    session = h.sessions.get(call.thread)
    assert len(session["items"]) == 1
    assert "Late fragment" not in str(session["items"])


@pytest.mark.anyio
async def test_pause_ends_call_and_receipt_resumes_once_after_service_restart(world):
    service, h = world
    h.policy.pause(True)
    await service.incoming("paused", HEADERS)
    assert service.active is None
    assert service.provider.call_args.args == ("paused", "reject", {"status_code": 486})
    h.policy.pause(False)
    await service.incoming("live", HEADERS)
    await test_inbound.pin(service)
    call = service.active
    h.policy.pause(True)
    await asyncio.sleep(0.65)
    assert service.active is None
    row = h.store.one("SELECT * FROM calls WHERE id='live'")
    assert row["receipt_id"] and not row["receipt_synced"]
    assert h.sessions.get(call.thread)["items"] == []
    await service.close()
    resumed = CallService(h, provider=AsyncMock(), connection_factory=test_inbound.Connection)
    try:
        await resumed.recover()
        h.policy.pause(False)
        await asyncio.sleep(0.65)
        await resumed.recover()
        resumed.sync_receipts()
        items = h.sessions.get(call.thread)["items"]
        assert len(items) == 1 and items[0]["id"] == "phone_receipt_live"
        assert h.store.one("SELECT receipt_synced FROM calls WHERE id='live'")["receipt_synced"]
    finally:
        await resumed.close()


@pytest.mark.anyio
async def test_receipt_delivery_retry_does_not_duplicate_a_saved_message(world, monkeypatch):
    service, h = world
    await service.incoming("sync_crash", HEADERS)
    call = service.active
    original = h.store.execute

    def fail_after_save(sql, params=()):
        if sql.startswith("UPDATE calls SET receipt_synced=1"):
            raise OSError("Simulated crash after session save")
        return original(sql, params)

    monkeypatch.setattr(h.store, "execute", fail_after_save)
    with pytest.raises(OSError):
        await service.end()
    assert service.active is None
    assert len(h.sessions.get(call.thread)["items"]) == 1
    monkeypatch.setattr(h.store, "execute", original)
    await service.recover()
    service.sync_receipts()
    assert len(h.sessions.get(call.thread)["items"]) == 1
    assert h.store.one("SELECT receipt_synced FROM calls WHERE id='sync_crash'")["receipt_synced"]


@pytest.mark.anyio
async def test_hangup_failure_is_visible_without_provider_body(world):
    service, h = world
    await service.incoming("hangup_failure", HEADERS)
    service.provider.side_effect = RuntimeError("Sensitive provider body credential=do-not-display")
    await service.end()
    row = h.store.one("SELECT * FROM calls WHERE id='hangup_failure'")
    assert service.active is None and row["state"] == "failed"
    assert "hangup could not be confirmed" in row["reason"]
    assert "credential" not in str(service.status())
    assert row["receipt_id"]


@pytest.mark.anyio
async def test_slow_cancellation_has_a_deadline_and_blocks_overlapping_work(world, monkeypatch):
    service, h = world
    monkeypatch.setattr("backend.telephony.inbound.WORK_STOP_TIMEOUT_S", 0.01)
    await service.incoming("slow", HEADERS)
    await test_inbound.pin(service)
    call = service.active
    started, release = asyncio.Event(), asyncio.Event()

    async def stubborn(*args):
        started.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            await release.wait()
        return {"outcome": "reply", "message": "Late result"}

    service.compute = stubborn
    request = asyncio.create_task(service.request(call, "A slow request", "slow_work"))
    try:
        await started.wait()
        await asyncio.wait_for(service.end(), 0.5)
        assert service.active is None and service.draining
        row = h.store.one("SELECT * FROM calls WHERE id='slow'")
        assert row["state"] == "failed" and "unconfirmed" in row["reason"]
        await service.incoming("overlap", HEADERS)
        assert service.provider.call_args.args == ("overlap", "reject", {"status_code": 486})
        release.set()
        await request
        assert h.store.one("SELECT state FROM call_requests WHERE call_id='slow'")["state"] == "cancelled"
        assert not service.draining
    finally:
        release.set()
        await request


@pytest.mark.anyio
async def test_provider_closed_session_does_not_require_second_hangup(world):
    service, h = world
    await service.incoming("closed", HEADERS)
    service.active.connection.finalized = asyncio.Event()
    service.active.connection.finalized.set()
    service.provider.reset_mock()
    await service.end()
    service.provider.assert_not_awaited()
    assert h.store.one("SELECT state FROM calls WHERE id='closed'")["state"] == "ended"
