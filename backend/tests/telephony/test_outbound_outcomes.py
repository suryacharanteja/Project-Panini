import asyncio
import json
from pathlib import Path

import pytest
from panini_cli.agent.model import ModelTurn, OpenAIModel, ScriptedModel

from backend.harness.runtime import Harness
from backend.harness.store import Store
from backend.ledger.chain import verify
from backend.panini.contracts import Fault
from backend.telephony.brief import CallBrief
from backend.telephony.outbound import OutboundCalls
from backend.telephony.outcomes import utterances, validate_proposal

from . import test_outbound
from .test_outbound import BRIEF, approve

world = test_outbound.world
QUOTE = "Your appointment is confirmed for Thursday afternoon next week."


@pytest.fixture
def anyio_backend():
    return "asyncio"


def proposal():
    return {
        "suggested_status": "achieved",
        "findings": [
            {
                "kind": "outcome",
                "text": "Callee reports a Thursday afternoon appointment next week.",
                "outcome_index": 0,
                "commitment_permission": None,
                "citations": [{"utterance_id": 1, "quote": QUOTE}],
            }
        ],
        "follow_ups": ["review_calendar_entry"],
    }


async def completed(h, service):
    row = service.draft(BRIEF)
    approve(h, row)
    await service.tick()
    await service.work
    call = service.active
    await service.event(call, {"type": "transport.answered"})
    await service.event(
        call, {"type": "transcript", "role": "assistant", "delta": call["brief"].disclosure()}
    )
    for delta in [QUOTE[:25], QUOTE[25:]]:
        await service.event(call, {"type": "transcript", "role": "user", "delta": delta})
    await service.end()
    return service.get(row["id"])


def test_quoted_claims_are_interpretations_and_never_independent_verification():
    turns = utterances(
        [
            {"role": "assistant", "text": "Disclosure."},
            {"role": "user", "text": QUOTE[:10]},
            {"role": "user", "text": QUOTE[10:]},
        ]
    )
    result = validate_proposal(proposal(), CallBrief.model_validate(BRIEF), turns)
    assert result["interpretation_only"] and not result["independently_verified"]
    assert result["review_decision"] is None and result["findings"][0]["citations"][0]["quote"] == QUOTE


@pytest.mark.parametrize(
    "invalid",
    [
        "missing_quote",
        "wrong_speaker",
        "short_quote",
        "bad_id",
        "goal",
        "unknown_permission",
        "commitment",
        "extra_effect",
        "no_findings",
        "contradiction",
    ],
)
def test_invalid_or_incomplete_proposals_cannot_be_reported(invalid):
    data = proposal()
    finding = data["findings"][0]
    if invalid == "missing_quote":
        finding["citations"][0]["quote"] = "A made up appointment confirmation."
    elif invalid == "wrong_speaker":
        finding["citations"][0]["utterance_id"] = 0
    elif invalid == "short_quote":
        finding["citations"][0]["quote"] = "Yes"
    elif invalid == "bad_id":
        finding["citations"][0]["utterance_id"] = 99
    elif invalid == "goal":
        finding["outcome_index"] = 9
    elif invalid == "unknown_permission":
        finding["commitment_permission"] = "pay a deposit"
    elif invalid == "commitment":
        finding["kind"] = "commitment"
        finding["commitment_permission"] = "confirm a reservation"
    elif invalid == "extra_effect":
        data["follow_ups"] = ["send_payment"]
    elif invalid == "no_findings":
        data["findings"] = []
    else:
        finding["kind"] = "unconfirmed"
    with pytest.raises(ValueError):
        validate_proposal(
            data,
            CallBrief.model_validate(BRIEF),
            [{"id": 0, "role": "assistant", "text": QUOTE}, {"id": 1, "role": "user", "text": QUOTE}],
        )


def test_context_and_suggested_status_are_bounded():
    with pytest.raises(ValueError):
        utterances([{"role": "user", "text": "x" * 60001}])
    with pytest.raises(ValueError):
        utterances([{"role": "user" if i % 2 else "assistant", "text": "x"} for i in range(301)])
    data = proposal()
    data["suggested_status"] = "voicemail"
    with pytest.raises(ValueError):
        validate_proposal(
            data,
            CallBrief.model_validate(BRIEF),
            [{"id": 0, "role": "assistant", "text": "Hi"}, {"id": 1, "role": "user", "text": QUOTE}],
        )


@pytest.mark.anyio
async def test_analysis_receipt_links_immutable_call_and_tool_free_context(world):
    h, service, observed, _ = world
    model = ScriptedModel([{"text": json.dumps(proposal())}])
    h.model_factory = lambda: model
    row = await completed(h, service)
    original = h.store.one("SELECT result_digest FROM receipts WHERE id=?", (row["receipt_id"],))
    await service.reports.analyze(row["id"])
    report = service.reports.get(row["id"])
    assert report["state"] == "ready" and report["payload"]["call_receipt_id"] == row["receipt_id"]
    assert report["payload"]["transcript_artifact_id"] == row["transcript_artifact_id"]
    assert report["payload"]["approved_brief_digest"] == row["digest"]
    assert h.store.one("SELECT result_digest FROM receipts WHERE id=?", (row["receipt_id"],)) == original
    assert set(json.loads(model.requests[0]["items"][0]["content"])) == {
        "reviewed_brief",
        "transcript_untrusted",
    }
    assert verify(h.store)["ok"] and [r[0] for r in observed] == ["create", "hangup"]
    await service.reports.analyze(row["id"])
    assert model.count == 1


@pytest.mark.anyio
@pytest.mark.parametrize("accepted", [True, False])
async def test_accepting_or_rejecting_report_never_executes_followups(world, accepted):
    h, service, observed, _ = world
    h.model_factory = lambda: ScriptedModel([{"text": json.dumps(proposal())}])
    row = await completed(h, service)
    await service.reports.analyze(row["id"])
    review = service.reports.review(row["id"])
    assert service.reports.review(row["id"])["id"] == review["id"]
    with pytest.raises(Fault):
        h.interrupts.resolve(
            review["id"],
            nonce=review["nonce"],
            action_digest=review["action_digest"],
            status="resolved",
            payload={"approved": True},
            decided_by={"surface": "phone_line", "method": "tap"},
        )
    h.interrupts.resolve(
        review["id"],
        nonce=review["nonce"],
        action_digest=review["action_digest"],
        status="resolved",
        payload={"approved": accepted},
        decided_by={"surface": "phone_app", "method": "tap"},
    )
    service.reports.reconcile_reviews()
    final = service.reports.get(row["id"])
    assert final["state"] == "reviewed" and final["payload"]["review_decision"] == (
        "accepted" if accepted else "rejected"
    )
    assert not final["payload"]["independently_verified"] and final["review_receipt_id"]
    count = h.store.one("SELECT COUNT(*) n FROM receipts")["n"]
    service.reports.reconcile_reviews()
    assert h.store.one("SELECT COUNT(*) n FROM receipts")["n"] == count
    assert verify(h.store)["ok"] and [r[0] for r in observed] == ["create", "hangup"]


@pytest.mark.anyio
async def test_changed_transcript_and_report_digest_refuse_stale_review(world):
    h, service, _, _ = world
    h.model_factory = lambda: ScriptedModel([{"text": json.dumps(proposal())}])
    row = await completed(h, service)
    await service.reports.analyze(row["id"])
    review = service.reports.review(row["id"])
    h.interrupts.resolve(
        review["id"],
        nonce=review["nonce"],
        action_digest=review["action_digest"],
        status="resolved",
        payload={"approved": True},
        decided_by={"surface": "console", "method": "local"},
    )
    report = service.reports.get(row["id"])
    changed = {**report["payload"], "suggested_status": "declined"}
    h.store.execute("UPDATE outbound_reports SET payload=? WHERE call_id=?", (json.dumps(changed), row["id"]))
    service.reports.reconcile_reviews()
    assert service.reports.get(row["id"])["state"] == "unavailable"


@pytest.mark.anyio
async def test_missing_tampered_artifact_and_invalid_model_are_unavailable(world):
    h, service, _, _ = world
    row = await completed(h, service)
    model = ScriptedModel([{"text": "not JSON"}])
    h.model_factory = lambda: model
    await service.reports.analyze(row["id"])
    assert service.reports.get(row["id"])["state"] == "unavailable" and model.count == 1
    row2 = await completed(h, service)
    meta, _ = h.artifacts.get(row2["transcript_artifact_id"])
    Path(meta["path"]).write_bytes(b'{"untrusted":true,"messages":[]}')
    await service.reports.analyze(row2["id"])
    assert service.reports.get(row2["id"])["state"] == "unavailable" and model.count == 1


@pytest.mark.anyio
async def test_pause_and_daily_interpretation_limit(world, monkeypatch):
    h, service, _, _ = world
    monkeypatch.setenv("PANINI_OUTBOUND_PER_DAY", "2")
    monkeypatch.setattr(service.reports, "tick", lambda: None)
    row = await completed(h, service)
    second = await completed(h, service)
    monkeypatch.setenv("PANINI_OUTBOUND_PER_DAY", "1")
    h.policy.pause(True)
    await service.reports.analyze(row["id"])
    assert service.reports.get(row["id"])["state"] == "pending"
    h.policy.pause(False)
    model = ScriptedModel([{"text": json.dumps(proposal())}])
    h.model_factory = lambda: model
    await service.reports.analyze(row["id"])
    # Duplicate queueing is idempotent and cannot spend another turn.
    service.reports.enqueue(row["id"])
    await service.reports.analyze(row["id"])
    assert model.count == 1
    await service.reports.analyze(second["id"])
    assert service.reports.get(second["id"])["state"] == "unavailable" and model.count == 1


@pytest.mark.anyio
async def test_running_interpretation_restart_is_not_retried(world):
    h, service, observed, _ = world
    row = await completed(h, service)
    h.store.execute("UPDATE outbound_reports SET state='running' WHERE call_id=?", (row["id"],))
    reopened = Harness(
        Store(h.store.path), model_factory=lambda: ScriptedModel([{"text": "No automatic retry"}])
    )
    try:
        restarted = OutboundCalls(
            reopened, provider=service.provider, secrets=service.secrets, clock=service.clock
        )
        restarted.reports.recover()
        assert restarted.reports.get(row["id"])["state"] == "unavailable"
        await restarted.reports.analyze(row["id"])
        assert [r[0] for r in observed] == ["create", "hangup"]
    finally:
        await reopened.close()


@pytest.mark.anyio
async def test_expired_review_can_be_reopened_without_losing_the_report(world):
    h, service, _, now = world
    h.model_factory = lambda: ScriptedModel([{"text": json.dumps(proposal())}])
    row = await completed(h, service)
    await service.reports.analyze(row["id"])
    first = service.reports.review(row["id"])
    now[0] += 3601
    second = service.reports.review(row["id"])
    assert first["id"] != second["id"]
    assert h.interrupts.get(first["id"])["status"] == "expired"
    assert service.reports.get(row["id"])["review_interrupt_id"] == second["id"]


@pytest.mark.anyio
@pytest.mark.parametrize("interruption", ["cancel", "timeout", "pause", "source"])
async def test_interrupted_or_changed_analysis_is_not_retried(world, monkeypatch, interruption):
    from backend.telephony import outcomes

    h, service, _, _ = world
    row = await completed(h, service)
    entered, release = asyncio.Event(), asyncio.Event()

    class WaitingModel:
        count = 0

        async def respond(self, instructions, items, tools, **kwargs):
            self.count += 1
            assert tools == [] and kwargs["max_output_tokens"] == 3000
            entered.set()
            await release.wait()
            return ModelTurn([], json.dumps(proposal()), [])

    model = WaitingModel()
    h.model_factory = lambda: model
    if interruption == "timeout":
        monkeypatch.setattr(outcomes, "ANALYSIS_TIMEOUT_S", 0.02)
    task = asyncio.create_task(service.reports.analyze(row["id"]))
    await entered.wait()
    if interruption == "cancel":
        task.cancel()
    elif interruption == "pause":
        h.policy.pause(True)
        release.set()
    elif interruption == "source":
        meta, _ = h.artifacts.get(row["transcript_artifact_id"])
        Path(meta["path"]).write_text("changed")
        release.set()
    await asyncio.gather(task, return_exceptions=True)
    report = service.reports.get(row["id"])
    assert report["state"] == "unavailable"
    assert (
        h.store.one("SELECT outcome FROM receipts WHERE id=?", (report["receipt_id"],))["outcome"]
        == "partial"
    )
    h.policy.pause(False)
    await service.reports.analyze(row["id"])
    assert model.count == 1 and verify(h.store)["ok"]


@pytest.mark.anyio
async def test_model_wrapper_forwards_strict_schema_and_bounded_output_without_network():
    from backend.telephony.outcomes import Proposal

    # Construct without an API client: this inspects the exact outgoing request, never the network.
    model = object.__new__(OpenAIModel)
    model.model, model.effort, model.reasoning = "test-model", None, False
    requests = []

    async def stream(request, on_text):
        requests.append(request)
        return ModelTurn([], "{}", [])

    model._stream = stream
    schema = Proposal.model_json_schema()
    for definition in [schema, *schema["$defs"].values()]:
        assert definition["additionalProperties"] is False
        assert set(definition["required"]) == set(definition["properties"])
    await model.respond("Bounded interpretation", [], [], output_schema=schema, max_output_tokens=3000)
    assert requests[0]["max_output_tokens"] == 3000
    assert requests[0]["tools"] == [] and requests[0]["store"] is False
    assert requests[0]["text"]["format"]["strict"] is True
