"""Restore-before-decide page writes. The decision and CAS save are replay safe."""

import difflib
import json

from .. import v2flags
from ..interrupts import InterruptSpec, Parked, digest
from .store import Spaces, SpacesError


def content_for(operation, page):
    if operation["kind"] != "edit":
        return operation.get("content", "")
    if ("content" in operation) == ("patch" in operation):
        raise SpacesError("INVALID_PATCH", "Supply replacement content or one exact patch, never both.")
    if "content" in operation:
        return operation["content"]
    patch = operation["patch"]
    if not isinstance(patch, dict) or set(patch) != {"find", "replace"}:
        raise SpacesError("INVALID_PATCH", "A patch contains find and replace text.")
    for key in ("find", "replace"):
        Spaces._text(patch[key], 20000, "Patch " + key, key == "find")
    if page["content"].count(patch["find"]) != 1:
        raise SpacesError("INVALID_PATCH", "The patch find text must occur exactly once.", 409)
    return page["content"].replace(patch["find"], patch["replace"], 1)


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
            page = h.spaces.read(operation["page_id"], persona_id) if operation["kind"] != "create" else None
            content = content_for(operation, page)
            h.spaces._text(content, 20000, "Review draft")
            preview = {**operation}
            if operation["kind"] in {"create", "edit"}:
                preview["content"] = content
                preview.pop("patch", None)
            if page:
                preview["page_title"] = page["title"]
                if operation["kind"] == "edit":
                    preview["previous_content"] = page["content"][:20000]
                    preview["previous_truncated"] = len(page["content"]) > 20000
                    diff = "\n".join(
                        difflib.unified_diff(
                            page["content"].splitlines(),
                            content.splitlines(),
                            fromfile="previous",
                            tofile="proposed",
                            lineterm="",
                        )
                    )
                    preview["diff"] = diff[:12000]
                    preview["diff_truncated"] = len(diff) > 12000
            view = h.interrupts.open(
                InterruptSpec(
                    "review",
                    "page_draft",
                    {"move": "Review page move", "delete": "Review moving page to trash"}.get(
                        operation["kind"], "Review page draft"
                    ),
                    {
                        "move": "Approve to move this page to its proposed parent.",
                        "delete": "Approve to move this page to trash. It remains restorable for 30 days.",
                    }.get(operation["kind"], "Approve to save this draft to your notebook."),
                    preview=preview,
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
            current = h.spaces.read(operation["page_id"], persona_id) if operation["kind"] == "edit" else None
            content = payload.get("content", content_for(operation, current))
            if operation["kind"] == "create":
                page = h.spaces.create(
                    operation["space_id"],
                    operation["title"],
                    content,
                    parent_id=operation.get("parent_id"),
                    persona_id=persona_id,
                    source_thread_id=thread_id,
                )
            else:
                page = h.spaces.edit(
                    operation["page_id"],
                    operation["expected_revision"],
                    persona_id=persona_id,
                    title=operation.get("title") if operation["kind"] == "edit" else None,
                    content=content if operation["kind"] == "edit" else None,
                    move=operation["kind"] == "move",
                    trash=operation["kind"] == "delete",
                    parent_id=operation.get("parent_id"),
                )
            result = {
                "saved": True,
                "page": page,
                "page_id": page["id"],
                "url": page["url"],
                "interrupt_id": decision["id"],
            }
        h.store.execute(
            "UPDATE page_reviews SET result=? WHERE interrupt_id=?", (json.dumps(result), decision["id"])
        )
        return result


def _authorize(h, operation, persona_id, replay=False):
    h.personas.get(persona_id)
    spec = h.registry.resolve("pages_" + operation["kind"], {})
    if not h.personas.allows(persona_id, spec):
        raise SpacesError("SPACE_FORBIDDEN", "Persona no longer has permission to write pages.", 403)
    if operation["kind"] == "create":
        h.spaces.authorize(operation["space_id"], persona_id)
    else:
        page = h.spaces.read(
            operation["page_id"], persona_id, deleted=replay and operation["kind"] == "delete"
        )
        if not replay and page["revision"] != operation["expected_revision"]:
            raise SpacesError("PAGE_CHANGED", "Page changed; read the latest revision before editing.", 409)
