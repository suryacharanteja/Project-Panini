"""Owner-only permanent deletion through a local, durable confirmation and destroy receipt."""

import json
import secrets
from uuid import uuid4

from .. import v2flags
from ..interrupts import InterruptSpec, decided_by
from .store import SpacesError


def check(h, page_id, revision):
    if not v2flags.enabled("PANINI_V2_INTERRUPTS") or h.policy.paused():
        raise SpacesError(
            "DESTROY_UNAVAILABLE", "Permanent deletion requires interrupts and an unpaused PC.", 409
        )
    page = h.spaces.read(page_id, deleted=True)
    if page["deleted_at"] is None:
        raise SpacesError("TRASH_FIRST", "Move this page to trash first.", 409)
    if page["revision"] != revision:
        raise SpacesError("PAGE_CHANGED", "Page changed. Review it again.", 409)
    if h.store.one("SELECT id FROM pages WHERE parent_id=?", (page_id,)):
        raise SpacesError("PAGE_HAS_CHILDREN", "Move or remove every subpage first.", 409)
    return page


def request(h, page_id, expected_revision, confirmation):
    with h.spaces.transaction():
        page = check(h, page_id, expected_revision)
        if confirmation != page["title"]:
            raise SpacesError(
                "CONFIRMATION_REQUIRED", "Type the exact page title to request permanent deletion."
            )
        view = h.interrupts.open(
            InterruptSpec(
                "approval",
                "page_destroy",
                "Permanently delete page",
                f"Permanently delete {page['title']} and its retained revisions. This cannot be undone.",
                preview={
                    "effect": "destroy",
                    "page_id": page_id,
                    "title": page["title"],
                    "revision": expected_revision,
                },
                action={"effect": "destroy", "page_id": page_id, "revision": expected_revision},
                response_schema={
                    "type": "object",
                    "properties": {"approved": {"type": "boolean"}},
                    "required": ["approved"],
                    "additionalProperties": False,
                },
                level="local",
                ttl_s=300,
            ),
            run_id="owner-spaces-destroy-" + uuid4().hex,
        )
        h.store.execute(
            "INSERT INTO page_destroy VALUES(?,?,?,NULL)", (view["id"], page_id, expected_revision)
        )
        return h.interrupts.get(view["id"], include_secret=True)


def confirm(h, interrupt_id, nonce, action_digest, approved):
    with h.spaces.transaction():
        row = h.store.one("SELECT * FROM page_destroy WHERE interrupt_id=?", (interrupt_id,))
        if not row:
            raise SpacesError("DESTROY_NOT_FOUND", "Deletion request not found.", 404)
        view = h.interrupts.get(interrupt_id, include_secret=True)
        if not secrets.compare_digest(nonce, view["nonce"]) or action_digest != view["action_digest"]:
            raise SpacesError("INTERRUPT_CHANGED", "Deletion request changed. Review it again.", 409)
        h.interrupts.resolve(
            interrupt_id,
            nonce=nonce,
            action_digest=action_digest,
            status="resolved",
            payload={"approved": approved},
            decided_by=decided_by("console", "local"),
        )
        if row["result"]:
            return json.loads(row["result"])
        if approved:
            page = check(h, row["page_id"], row["expected_revision"])
            receipt = h.ledger.begin(
                run_id=view["run_id"],
                tool="owner_page_destroy",
                capability="spaces",
                hand="owner_console",
                args={"page_id": page["id"], "revision": page["revision"]},
                effect="destroy",
                principal={"kind": "owner", "surface": "console", "verified": True},
            )
            h.ledger.decision(
                receipt["id"],
                {
                    "action": "allow",
                    "effect": "destroy",
                    "level": "local",
                    "reasons": ["Explicit owner console confirmation", "Exact page revision"],
                },
            )
            h.ledger.dispatched(receipt["id"])
            h.store.execute("DELETE FROM page_sources WHERE page_id=?", (page["id"],))
            h.store.execute("DELETE FROM page_revisions WHERE page_id=?", (page["id"],))
            # Remove cached reviewed content as well as document rows. Receipts keep hashes.
            h.store.execute(
                "UPDATE page_reviews SET result=? WHERE json_extract(result,'$.page.id')=?",
                (json.dumps({"saved": False, "reason": "Page permanently deleted."}), page["id"]),
            )
            h.store.execute("DELETE FROM pages WHERE id=?", (page["id"],))
            h.ledger.evidence(
                receipt["id"], "page_destroy", "Page and retained revisions removed.", {"page_id": page["id"]}
            )
            h.ledger.end(receipt["id"], outcome="ok", content="Page permanently deleted.")
            result = {"deleted": True, "receipt_id": receipt["id"]}
        else:
            result = {"deleted": False}
        h.store.execute(
            "UPDATE page_destroy SET result=? WHERE interrupt_id=?", (json.dumps(result), interrupt_id)
        )
        return result
