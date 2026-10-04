"""Restore-before-decide page writes. The decision and CAS save are replay safe."""

import json

from .. import v2flags
from ..interrupts import InterruptSpec, Parked, digest
from .store import SpacesError


def review(h, operation, *, persona_id, run_id, tool_call_id, thread_id=None):
    result = _review(
        h, operation, persona_id=persona_id, run_id=run_id, tool_call_id=tool_call_id, thread_id=thread_id
    )
    if isinstance(result, Parked):
        raise result
    return result


def _review(h, operation, *, persona_id, run_id, tool_call_id, thread_id=None):
    if not v2flags.enabled("PANINI_V2_INTERRUPTS") or not run_id or not tool_call_id:
        raise SpacesError(
            "REVIEW_UNAVAILABLE", "Page writes require durable interrupts and a tool-call identity.", 409
        )
    encoded = json.dumps(operation, sort_keys=True, ensure_ascii=False)
    with h.spaces.transaction():
        row = h.store.one(
            "SELECT * FROM page_reviews WHERE run_id=? AND tool_call_id=?", (run_id, tool_call_id)
        )
        if row and (row["operation"] != encoded or row["persona_id"] != persona_id):
            raise SpacesError("REVIEW_CHANGED", "This tool call already has a different draft.", 409)
        if not row:
            _authorize(h, operation, persona_id)
            content = operation.get("content", "")
            h.spaces._text(content, 20000, "Review draft")
            view = h.interrupts.open(
                InterruptSpec(
                    "review",
                    "page_draft",
                    "Review page draft",
                    "Approve to save this draft to your notebook.",
                    preview=operation,
                    action={
                        "type": "page_write",
                        "draft_digest": digest(operation),
                        "persona_id": persona_id,
                    },
                    response_schema={
                        "type": "object",
                        "properties": {
                            "approved": {"type": "boolean"},
                            "content": {"type": "string", "maxLength": 20000},
                        },
                        "required": ["approved"],
                        "additionalProperties": False,
                    },
                    level="tap",
                    ttl_s=86400,
                    tool_call_id=tool_call_id,
                ),
                run_id=run_id,
                thread_id=thread_id,
            )
            h.store.execute(
                "INSERT INTO page_reviews VALUES(?,?,?,?,?,NULL)",
                (run_id, tool_call_id, view["id"], encoded, persona_id),
            )
            row = h.store.one("SELECT * FROM page_reviews WHERE interrupt_id=?", (view["id"],))
        if row["result"]:
            # Re-check access even for an already-saved review, before returning its content.
            _authorize(h, operation, persona_id, replay=True)
            return json.loads(row["result"])
        decision = h.interrupts.get(row["interrupt_id"])
        if decision["status"] == "open":
            return Parked(decision["id"], decision["title"])
        payload = (decision.get("decision") or {}).get("payload", {})
        if decision["status"] != "resolved" or not payload.get("approved"):
            result = {"saved": False, "reason": "Draft was declined or expired."}
        else:
            if not v2flags.enabled("PANINI_SPACES") or h.policy.paused():
                raise SpacesError("SPACES_PAUSED", "Page saving is disabled or PANINI is paused.", 409)
            _authorize(h, operation, persona_id)
            operation = {**operation, "content": payload.get("content", operation.get("content", ""))}
            if operation["kind"] == "create":
                page = h.spaces.create(
                    operation["space_id"],
                    operation["title"],
                    operation["content"],
                    parent_id=operation.get("parent_id"),
                    persona_id=persona_id,
                    source_thread_id=thread_id,
                )
            else:
                page = h.spaces.edit(
                    operation["page_id"],
                    operation["expected_revision"],
                    persona_id=persona_id,
                    title=operation.get("title"),
                    content=operation.get("content"),
                )
            result = {"saved": True, "page": page, "interrupt_id": decision["id"]}
        h.store.execute(
            "UPDATE page_reviews SET result=? WHERE interrupt_id=?", (json.dumps(result), decision["id"])
        )
        return result


def _authorize(h, operation, persona_id, replay=False):
    h.personas.get(persona_id)
    spec = h.registry.resolve("pages_create" if operation["kind"] == "create" else "pages_edit", {})
    if not h.personas.allows(persona_id, spec):
        raise SpacesError("SPACE_FORBIDDEN", "Persona no longer has permission to write pages.", 403)
    if operation["kind"] == "create":
        h.spaces.authorize(operation["space_id"], persona_id)
    else:
        page = h.spaces.read(operation["page_id"], persona_id)
        if not replay and page["revision"] != operation["expected_revision"]:
            raise SpacesError("PAGE_CHANGED", "Page changed; read the latest revision before editing.", 409)
