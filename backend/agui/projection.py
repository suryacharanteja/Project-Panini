"""PANINI run events → AG-UI events (docs/panini-enhancements/lld/V2-07-agui-adapter.md §5.3).

Arguments and results are the ledger's redacted views, never raw values. Interrupt nonces and action digests
travel in interrupt metadata because a resume must echo them (I3).
"""

import json
from datetime import UTC, datetime
from uuid import uuid4

from ag_ui.core import (
    CustomEvent,
    Interrupt,
    RunErrorEvent,
    RunFinishedEvent,
    RunFinishedInterruptOutcome,
    RunFinishedSuccessOutcome,
    RunStartedEvent,
    StateDeltaEvent,
    StateSnapshotEvent,
    TextMessageContentEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
    ToolCallArgsEvent,
    ToolCallEndEvent,
    ToolCallResultEvent,
    ToolCallStartEvent,
)

from ..ledger.redact import redact_args, redact_result

TEXT_CHUNK = 400


class Projector:
    def __init__(self, thread_id: str, run_id: str, parent_run_id: str | None = None):
        self.thread_id, self.run_id, self.parent_run_id = thread_id, run_id, parent_run_id
        self.plan_sent = False
        self.message_id = None

    def started(self):
        return [
            RunStartedEvent(thread_id=self.thread_id, run_id=self.run_id, parent_run_id=self.parent_run_id)
        ]

    def text(self, text: str):
        if not text:
            return []
        message_id = uuid4().hex
        events = [TextMessageStartEvent(message_id=message_id, role="assistant")]
        events += [
            TextMessageContentEvent(message_id=message_id, delta=text[i : i + TEXT_CHUNK])
            for i in range(0, len(text), TEXT_CHUNK)
        ]
        return events + [TextMessageEndEvent(message_id=message_id)]

    def tool_started(self, call_id: str, name: str, args):
        view = redact_args(args) if isinstance(args, dict) else {}
        return [
            ToolCallStartEvent(tool_call_id=call_id, tool_call_name=name),
            ToolCallArgsEvent(tool_call_id=call_id, delta=json.dumps(view, ensure_ascii=False, default=str)),
            ToolCallEndEvent(tool_call_id=call_id),
        ]

    def tool_finished(self, call_id: str, output):
        text = output if isinstance(output, str) else json.dumps(output, ensure_ascii=False, default=str)
        return [
            ToolCallResultEvent(message_id=uuid4().hex, tool_call_id=call_id, content=redact_result(text))
        ]

    def plan(self, plan: list, interrupts=()):
        steps = [
            {"step": str(s.get("step", ""))[:300], "status": str(s.get("status", ""))} for s in plan or []
        ]
        if not self.plan_sent:
            self.plan_sent = True
            return [StateSnapshotEvent(snapshot={"plan": steps, "interrupts": list(interrupts)})]
        return [StateDeltaEvent(delta=[{"op": "replace", "path": "/plan", "value": steps}])]

    def receipt(self, receipt: dict):
        return [
            CustomEvent(
                name="panini.receipt",
                value={k: receipt.get(k) for k in ("id", "tool", "effect", "outcome")},
            )
        ]

    def activity(self, kind: str, message: str):
        return [CustomEvent(name="panini.activity", value={"kind": str(kind), "message": str(message)[:500]})]

    def interrupted(self, views: list[dict]):
        interrupts = [_interrupt(view) for view in views]
        return [
            RunFinishedEvent(
                thread_id=self.thread_id,
                run_id=self.run_id,
                outcome=RunFinishedInterruptOutcome(interrupts=interrupts),
            )
        ]

    def finished(self, outcome: str, message: str, evidence=()):
        return [
            RunFinishedEvent(
                thread_id=self.thread_id,
                run_id=self.run_id,
                outcome=RunFinishedSuccessOutcome(),
                result={"outcome": outcome, "message": message[:4000], "evidenceIds": list(evidence)[:20]},
            )
        ]

    def error(self, message: str, code: str = "RUN_FAILED"):
        return [RunErrorEvent(message=message[:500], code=code)]


def _interrupt(view: dict) -> Interrupt:
    expires = datetime.fromtimestamp(view["expires_at"], UTC).isoformat() if view.get("expires_at") else None
    metadata = {
        "kind": view.get("kind"),
        "title": view.get("title"),
        "preview": view.get("preview") or {},
        "level": view.get("level"),
        "nonce": view.get("nonce"),
        "actionDigest": view.get("action_digest"),
    }
    return Interrupt(
        id=view["id"],
        reason=view.get("reason") or "approval",
        message=view.get("message"),
        tool_call_id=view.get("tool_call_id"),
        response_schema=view.get("response_schema"),
        expires_at=expires,
        metadata=metadata,
    )
