import asyncio
import copy
import json

import pytest
from panini_cli.agent.model import ModelTurn, ScriptedModel

from backend.harness.runtime import Harness
from backend.harness.store import Store
from backend.ledger.chain import verify
from backend.policy.engine import Grant
from backend.telephony.brief import AvailabilityCheck, CallBrief, tool_schema
from backend.telephony.outbound import OutboundCalls
from backend.telephony.reads import compatible, projection

from . import test_outbound
from .test_outbound import BRIEF, approve

world = test_outbound.world
WINDOW = {
    "server": "calendar",
    "tool": "free_busy",
    "start": "2026-10-08T20:00:00Z",
    "end": "2026-10-08T21:00:00Z",
}
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {"start": {"type": "string"}, "end": {"type": "string"}},
    "required": ["start", "end"],
}
TOOL = {"name": "free_busy", "read_only": True, "input_schema": SCHEMA, "annotations": {"readOnlyHint": True}}


@pytest.fixture
def anyio_backend():
    return "asyncio"


class Calendar:
    name = "calendar"

    def __init__(self):
        self.spec = {"command": "isolated-fake", "background_read_only": ["free_busy"]}
        self.tools = [copy.deepcopy(TOOL)]
        self.lock = asyncio.Lock()
        self.seen = []
        self.result = {"start": WINDOW["start"], "end": WINDOW["end"], "complete": True, "busy": []}
        self.entered, self.release = asyncio.Event(), None

    async def start(self):
        pass

    async def request(self, method, parameters):
        self.seen.append((method, parameters))
        self.entered.set()
        if self.release:
            await self.release.wait()
        return {"structuredContent": self.result}

    async def close(self):
        pass


async def started(h, service, calendar=None, data=None):
    if calendar:
        h.connectors.servers = {"calendar": calendar}
    row = service.draft(data or {**BRIEF, "availability_checks": [WINDOW]})
    approve(h, row)
    await service.tick()
    await service.work
    call = service.active
    await service.event(call, {"type": "transport.answered"})
    await service.event(
        call, {"type": "transcript", "role": "assistant", "delta": call["brief"].disclosure()}
    )
    return call


def selector(kind="availability", index=0):
    return ScriptedModel([{"text": json.dumps({"kind": kind, "index": index})}])


def test_legacy_briefs_keep_exact_serialization_and_strict_nested_function_schema():
    brief = CallBrief.model_validate(BRIEF)
    assert "availability_checks" not in brief.model_dump()
    schema = tool_schema(CallBrief)
    assert "$ref" not in json.dumps(schema) and "$defs" not in schema
    nested = schema["properties"]["availability_checks"]["items"]
    assert set(nested["required"]) == set(nested["properties"]) and nested["additionalProperties"] is False
    assert "availability_checks" in schema["required"]


@pytest.mark.parametrize(
    "change",
    [
        {"end": "2026-10-08T20:00:00Z"},
        {"end": "2026-10-09T01:00:00Z"},
        {"start": "2026-10-08T20:00:00"},
        {"server": "../../secret"},
    ],
)
def test_reviewed_windows_reject_invalid_or_unbounded_scope(change):
    with pytest.raises(ValueError):
        AvailabilityCheck.model_validate({**WINDOW, **change})


@pytest.mark.parametrize(
    "invalid",
    [
        "incomplete",
        "mismatch",
        "event_detail",
        "private_data",
        "bad_interval",
        "error",
        "overflow",
        "not_json",
    ],
)
def test_calendar_projection_refuses_unknown_or_private_results(invalid):
    check = AvailabilityCheck.model_validate(WINDOW)
    data = {"start": check.start, "end": check.end, "complete": True, "busy": []}
    raw = {"structuredContent": data}
    if invalid == "incomplete":
        data["complete"] = False
    elif invalid == "mismatch":
        data["end"] = "2026-10-08T22:00:00Z"
    elif invalid == "event_detail":
        data["busy"] = [{"start": check.start, "end": check.end, "title": "Private meeting"}]
    elif invalid == "private_data":
        data["owner_email"] = "secret@example.test"
    elif invalid == "bad_interval":
        data["busy"] = [{"start": check.end, "end": check.start}]
    elif invalid == "error":
        raw["isError"] = True
    elif invalid == "overflow":
        raw["extra"] = "x" * 20001
    else:
        raw = {"content": [{"type": "text", "text": "not JSON"}]}
    with pytest.raises(ValueError):
        projection(raw, check)


@pytest.mark.parametrize(
    "busy,expected",
    [
        ([], "free"),
        ([{"start": "2026-10-08T19:00:00Z", "end": "2026-10-08T20:30:00Z"}], "busy"),
        ([{"start": "2026-10-08T19:00:00Z", "end": "2026-10-08T20:00:00Z"}], "free"),
    ],
)
def test_calendar_overlap_uses_half_open_reviewed_window(busy, expected):
    check = AvailabilityCheck.model_validate(WINDOW)
    result = projection(
        {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {"start": check.start, "end": check.end, "complete": True, "busy": busy}
                    ),
                }
            ]
        },
        check,
    )
    assert result["availability"] == expected and not result["booking_verified"]


@pytest.mark.anyio
async def test_lookup_is_tool_free_scoped_unverified_and_durably_deduplicated(world):
    h, service, observed, _ = world
    calendar = Calendar()
    call = await started(h, service, calendar)
    captured = []

    class Model:
        async def respond(self, instructions, items, tools, **options):
            captured.append((items, tools, options))
            return ModelTurn([], '{"kind":"availability","index":0}', [])

    h.model_factory = lambda: Model()
    h.agent = lambda **kwargs: pytest.fail("Owner agent must never be constructed")
    await service.request(call, "Is this exact reviewed slot free?", "read-1")
    await service.request(call, "Replay", "read-1")
    assert len(captured) == 1 and captured[0][1] == [] and captured[0][2]["max_output_tokens"] == 300
    context = json.loads(captured[0][0][0]["content"])
    assert set(context) == {"callee_question_untrusted", "facts", "purpose", "windows"}
    assert "server" not in json.dumps(context)
    assert calendar.seen == [
        ("tools/call", {"name": "free_busy", "arguments": {"start": WINDOW["start"], "end": WINDOW["end"]}})
    ]
    assert "free" in call["connection"].spoken[0] and "not a reservation" in call["connection"].spoken[0]
    read = h.store.one("SELECT * FROM outbound_reads WHERE call_id=?", (call["id"],))
    assert read["state"] == "finished" and json.loads(read["result"])["availability"] == "free"
    receipt = h.store.one("SELECT principal FROM receipts WHERE id=?", (read["receipt_id"],))
    assert json.loads(receipt["principal"])["kind"] == "external_agent"
    assert service.get(call["id"])["lookup_receipt_ids"] == [read["receipt_id"]]
    assert [action[0] for action in observed] == ["create"] and verify(h.store)["ok"]


@pytest.mark.anyio
async def test_fact_lookup_returns_exact_reviewed_fact_without_connector(world):
    h, service, _, _ = world
    call = await started(h, service, data=BRIEF)
    model = selector("fact", 0)
    h.model_factory = lambda: model
    assert await service.reads.answer(call, "First name?", "fact")
    assert call["connection"].spoken == ["Reviewed owner fact: First name Alex"]


@pytest.mark.anyio
@pytest.mark.parametrize("invalid", ["unknown", "owner_index", "write", "negative", "excluded"])
async def test_invalid_selector_never_speaks_new_facts_or_invokes_tools(world, invalid):
    h, service, _, _ = world
    calendar = Calendar()
    call = await started(h, service, calendar)
    kind, index = "availability", 99
    if invalid == "owner_index":
        kind, index = "owner", 0
    elif invalid == "write":
        kind, index = "send_payment", 0
    elif invalid == "negative":
        index = -1
    elif invalid == "excluded":
        call = None
        await service.end()
        call = await started(h, service, data={**BRIEF, "may_disclose": ["My password is secret"]})
        kind, index = "fact", 0
    h.model_factory = lambda: selector(kind, index)
    assert not await service.reads.answer(call, "A question", "bad")
    assert not calendar.seen and call["connection"].spoken == []


@pytest.mark.anyio
@pytest.mark.parametrize("change", ["grant", "metadata", "config", "policy", "stale"])
async def test_revoked_changed_or_stale_source_is_not_called(world, change):
    h, service, _, now = world
    calendar = Calendar()
    call = await started(h, service, calendar)
    model = selector()
    h.model_factory = lambda: model
    if change == "grant":
        calendar.spec["background_read_only"] = []
    elif change == "metadata":
        calendar.tools[0]["read_only"] = False
    elif change == "config":
        calendar.spec["command"] = "replacement"
    elif change == "policy":
        h.policy.put_grant(
            Grant(
                "deny-read",
                subject="surface:phone_line",
                tools=("mcp_calendar__free_busy",),
                levels={"observe": "deny"},
            )
        )
    else:
        now[0] += 5 * 86400
    assert not await service.reads.answer(call, "Is it free?", "revoked")
    assert not calendar.seen and not call["connection"].spoken


@pytest.mark.anyio
@pytest.mark.parametrize("change", ["grant", "pause", "config", "end", "timeout"])
async def test_inflight_revocation_end_and_timeout_discard_results_without_retry(world, monkeypatch, change):
    from backend.telephony import reads

    h, service, _, _ = world
    calendar = Calendar()
    calendar.release = asyncio.Event()
    call = await started(h, service, calendar)
    model = selector()
    h.model_factory = lambda: model
    if change == "timeout":
        monkeypatch.setattr(reads, "TIMEOUT_S", 0.02)
    task = asyncio.create_task(service.reads.answer(call, "Is it free?", "inflight"))
    await calendar.entered.wait()
    if change == "grant":
        calendar.spec["background_read_only"] = []
    elif change == "pause":
        h.policy.pause(True)
    elif change == "config":
        calendar.spec["command"] = "new-provider"
    elif change == "end":
        await service.end()
    if change != "timeout":
        calendar.release.set()
    await asyncio.gather(task, return_exceptions=True)
    assert not call["connection"].spoken
    record = h.store.one("SELECT * FROM outbound_reads WHERE call_id=?", (call["id"],))
    assert record["state"] == "unavailable" and verify(h.store)["ok"]
    h.policy.pause(False)
    assert await service.reads.answer(call, "Replay", "inflight") is (change != "end")
    assert model.count == 1 and len(calendar.seen) == 1


@pytest.mark.anyio
async def test_grant_required_before_review_and_scope_change_replaces_approval(world):
    h, service, _, _ = world
    calendar = Calendar()
    h.connectors.servers = {"calendar": calendar}
    calendar.spec["background_read_only"] = []
    with pytest.raises(ValueError):
        service.draft({**BRIEF, "availability_checks": [WINDOW]})
    calendar.spec["background_read_only"] = ["free_busy"]
    row = service.draft({**BRIEF, "availability_checks": [WINDOW]})
    first = h.interrupts.get(row["interrupt_id"])
    assert (
        first["preview"]["brief"]["availability_checks"][0]["source_digest"]
        == service.reads.sources()[0]["source_digest"]
    )
    changed = service.draft(
        {**BRIEF, "availability_checks": [{**WINDOW, "end": "2026-10-08T22:00:00Z"}]}, identifier=row["id"]
    )
    assert changed["digest"] != row["digest"] and h.interrupts.get(first["id"])["status"] == "cancelled"
    assert "calendar" not in CallBrief.model_validate(changed["brief"]).instructions()


@pytest.mark.anyio
async def test_running_lookup_sqlite_reopen_preserves_original_receipt_and_never_retries(world):
    h, service, _, _ = world
    call = await started(h, service, Calendar())
    h.model_factory = lambda: selector()
    await service.reads.answer(call, "Is it free?", "done")
    record = h.store.one("SELECT * FROM outbound_reads WHERE call_id=?", (call["id"],))
    original = h.store.one("SELECT result_digest FROM receipts WHERE id=?", (record["receipt_id"],))
    h.store.execute("UPDATE outbound_reads SET state='running' WHERE call_id=?", (call["id"],))
    reopened = Harness(Store(h.store.path), model_factory=lambda: pytest.fail("No retry"))
    try:
        restarted = OutboundCalls(
            reopened, provider=service.provider, secrets=service.secrets, clock=service.clock
        )
        restarted.reads.recover()
        assert (
            reopened.store.one("SELECT state FROM outbound_reads WHERE call_id=?", (call["id"],))["state"]
            == "unavailable"
        )
        assert (
            reopened.store.one("SELECT result_digest FROM receipts WHERE id=?", (record["receipt_id"],))
            == original
        )
        assert verify(reopened.store)["ok"]
    finally:
        await reopened.close()


def test_unknown_or_writable_mcp_schema_is_never_eligible():
    assert compatible(TOOL)
    for change in [
        {"read_only": False},
        {"annotations": {"destructiveHint": True}},
        {"input_schema": None},
        {"input_schema": {"type": "object"}},
    ]:
        assert not compatible({**TOOL, **change})
