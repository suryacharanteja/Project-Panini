"""Revocable standing approval for one fixed, bounded owner notification call per urgent item."""

import os
from uuid import uuid4

from ..interrupts import InterruptSpec, digest
from ..panini.contracts import Fault
from ..telephony.brief import CallBrief

GRANT_SECONDS = 7 * 86400
DELAY_SECONDS = 300
MAX_DAILY = 1
MAX_TOTAL = 3


def fixed_brief(number):
    return CallBrief.model_validate(
        {
            "callee_name": "PANINI owner",
            "number": number,
            "number_source": "owner",
            "purpose": "Ask the owner to open PANINI Inbox to review an urgent item.",
            "owner_first_name": "the owner",
            "may_disclose": ["PANINI has an urgent item awaiting review."],
            "acceptable_outcomes": ["The owner opens PANINI Inbox."],
            "may_commit": [],
            "fallback": "end_politely",
            "voicemail": "hang_up",
            "max_minutes": 2,
        }
    )


class UrgentEscalation:
    def __init__(self, continuity):
        self.continuity = continuity
        self.h, self.store, self.clock = continuity.h, continuity.store, continuity.clock
        self.h.interrupts.listeners.append(self.review_decided)

    def _config(self):
        settings = self.h.outbound.settings()
        return digest({"config": settings["config"], "signed_at": settings["signed_at"]})

    @staticmethod
    def _action(number, config_digest, expires):
        return {
            "type": "urgent_owner_call_grant",
            "number": number,
            "brief": fixed_brief(number).model_dump(),
            "config_digest": config_digest,
            "expires": expires,
            "delay_seconds": DELAY_SECONDS,
            "max_per_day": MAX_DAILY,
            "max_total": MAX_TOTAL,
        }

    def status(self):
        row = self.store.one(
            "SELECT * FROM continuity_urgent_grants WHERE status IN ('active','proposed') ORDER BY created DESC LIMIT 1"
        )
        if not row:
            return {"status": "off"}
        return {
            "id": row["id"],
            "status": "active"
            if self.valid(row)
            else row["status"]
            if row["status"] == "proposed"
            else "unavailable",
            "expires": row["expires"],
            "number": row["number"],
            "review_id": row["review_id"] if row["status"] == "proposed" else None,
            "max_per_day": MAX_DAILY,
            "max_total": MAX_TOTAL,
            "delay_seconds": DELAY_SECONDS,
        }

    def propose(self):
        if not self.continuity.enabled or not self.h.outbound.enabled():
            raise ValueError("Enable continuity and the acknowledged outbound trunk before opting in.")
        if os.getenv("PANINI_LOCAL_MODE", "off").strip().lower() == "private":
            raise ValueError("Owner calls are unavailable in private mode.")
        if self.store.one(
            "SELECT id FROM continuity_urgent_grants WHERE status IN ('active','proposed') AND expires>?",
            (self.clock(),),
        ):
            raise ValueError("Revoke the existing urgent call grant before creating another.")
        number = self.h.outbound.settings()["config"].get("owner_number")
        if not number:
            raise ValueError("Configure an owner number in Phone settings.")
        brief = fixed_brief(number)
        self.h.outbound.check(brief, hours=False)
        now, identifier = self.clock(), uuid4().hex
        expires, config_digest = now + GRANT_SECONDS, self._config()
        action = self._action(number, config_digest, expires)
        with self.store.lock:
            self.store.execute("BEGIN IMMEDIATE")
            try:
                review = self.h.interrupts.open(
                    InterruptSpec(
                        "approval",
                        "urgent_owner_call_grant",
                        "Allow urgent calls to your number",
                        "For seven days, PANINI may call this exact number at most once per day and three times total, five minutes after an unresolved urgent responsibility failure. Calls obey current policy and calling hours. Revoke anytime in Presence.",
                        preview=action,
                        action=action,
                        level="local",
                        ttl_s=600,
                    ),
                    run_id="urgent-grant:" + identifier,
                    owner_principal={"surface": "console"},
                )
                self.store.execute(
                    "INSERT INTO continuity_urgent_grants(id,review_id,scope_digest,config_digest,number,created,expires,status) VALUES (?,?,?,?,?,?,?,'proposed')",
                    (identifier, review["id"], digest(action), config_digest, number, now, expires),
                )
                self.store.execute("COMMIT")
            except BaseException:
                self.store.execute("ROLLBACK")
                raise
        return self.status()

    def review_decided(self, event, view):
        if view.get("reason") != "urgent_owner_call_grant" or event not in {
            "interrupt.resolved",
            "interrupt.expired",
        }:
            return
        row = self.store.one("SELECT * FROM continuity_urgent_grants WHERE review_id=?", (view["id"],))
        if not row or row["status"] != "proposed":
            return
        approved = (
            event == "interrupt.resolved"
            and view["status"] == "resolved"
            and (view.get("decision") or {}).get("payload", {}).get("approved") is True
            and (view.get("decided_by") or {}).get("method") == "local"
            and (view.get("decided_by") or {}).get("surface") in {"console", "avatar"}
            and digest(view.get("action")) == row["scope_digest"]
            and view.get("action_digest") == row["scope_digest"]
            and self._config() == row["config_digest"]
            and self.clock() < row["expires"]
        )
        self.store.execute(
            "UPDATE continuity_urgent_grants SET status=? WHERE id=? AND status='proposed'",
            ("active" if approved else "revoked", row["id"]),
        )

    def valid(self, row):
        if (
            not row
            or row["status"] != "active"
            or not self.continuity.enabled
            or not self.h.outbound.enabled()
            or self.clock() >= row["expires"]
            or os.getenv("PANINI_LOCAL_MODE", "off").strip().lower() == "private"
            or self._config() != row["config_digest"]
            or self.h.outbound.settings()["config"].get("owner_number") != row["number"]
        ):
            return False
        review = self.h.interrupts.get(row["review_id"])
        return (
            review["status"] == "resolved"
            and (review.get("decision") or {}).get("payload", {}).get("approved") is True
            and (review.get("decided_by") or {}).get("method") == "local"
            and (review.get("decided_by") or {}).get("surface") in {"console", "avatar"}
            and review.get("action_digest") == row["scope_digest"]
            and digest(review.get("action")) == row["scope_digest"]
            and review.get("action") == self._action(row["number"], row["config_digest"], row["expires"])
        )

    def revoke(self, identifier):
        row = self.store.one("SELECT * FROM continuity_urgent_grants WHERE id=?", (identifier,))
        if not row:
            raise KeyError("Unknown urgent grant.")
        self.store.execute("UPDATE continuity_urgent_grants SET status='revoked' WHERE id=?", (identifier,))
        if row["status"] == "proposed":
            self.h.interrupts.cancel(row["review_id"], "Urgent calling was revoked.")
        for pending in self.store.all(
            "SELECT id FROM outbound_briefs WHERE standing_grant_id=? AND state IN ('review','scheduled')",
            (identifier,),
        ):
            self.h.outbound.cancel_pending(pending["id"], "Urgent calling was revoked.")
        creating = self.store.one(
            "SELECT id FROM outbound_briefs WHERE standing_grant_id=? AND state='creating'",
            (identifier,),
        )
        if creating and self.h.outbound.work and not self.h.outbound.work.done():
            self.h.outbound.work.cancel()  # Ambiguous provider creation is recorded as unknown, never retried.
        return self.status()

    def authorize(self, call, brief):
        row = self.store.one(
            "SELECT * FROM continuity_urgent_grants WHERE id=?", (call["standing_grant_id"],)
        )
        item = self.store.one("SELECT * FROM continuity_items WHERE id=?", (call["urgent_item_id"],))
        attempt = self.store.one(
            "SELECT * FROM continuity_urgent_attempts WHERE item_id=?", (call["urgent_item_id"],)
        )
        if (
            not self.valid(row)
            or not item
            or item["status"] != "open"
            or not item["urgent"]
            or item["expires"] <= self.clock()
            or item["created"] + DELAY_SECONDS > self.clock()
            or self.continuity.presence_state()["state"] not in {"away", "away_with_phone"}
            or not attempt
            or attempt["grant_id"] != row["id"]
            or attempt["brief_id"] != call["id"]
            or brief.model_dump() != fixed_brief(row["number"]).model_dump()
            or call["digest"]
            != digest(
                {
                    "type": "standing_urgent_call",
                    "grant_id": row["id"],
                    "item_id": item["id"],
                    "brief": brief.model_dump(),
                }
            )
        ):
            raise Fault("URGENT_GRANT_CHANGED", "The urgent call grant or item changed.", 403)
        return self.h.outbound.check(brief, hours=True, surface=call["surface"])["decision"]

    def cancel_item(self, identifier):
        attempt = self.store.one(
            "SELECT brief_id FROM continuity_urgent_attempts WHERE item_id=?", (identifier,)
        )
        if attempt:
            call = self.h.outbound.get(attempt["brief_id"])
            if call["state"] in {"review", "scheduled"}:
                self.h.outbound.cancel_pending(call["id"], "Urgent item was resolved.")
            elif call["state"] == "creating" and self.h.outbound.work and not self.h.outbound.work.done():
                self.h.outbound.work.cancel()

    def tick(self):
        if self.continuity.presence_state()["state"] not in {"away", "away_with_phone"}:
            return
        row = self.store.one(
            "SELECT * FROM continuity_urgent_grants WHERE status='active' ORDER BY created DESC LIMIT 1"
        )
        if (
            not self.valid(row)
            or self.h.outbound.active
            or (self.h.outbound.work and not self.h.outbound.work.done())
        ):
            return
        now = self.clock()
        day = now - now % 86400
        if (
            self.store.one(
                "SELECT COUNT(*) AS n FROM continuity_urgent_attempts WHERE grant_id=? AND created>=?",
                (row["id"], day),
            )["n"]
            >= MAX_DAILY
        ):
            return
        if (
            self.store.one(
                "SELECT COUNT(*) AS n FROM continuity_urgent_attempts WHERE grant_id=?", (row["id"],)
            )["n"]
            >= MAX_TOTAL
        ):
            return
        item = self.store.one(
            "SELECT i.* FROM continuity_items i LEFT JOIN continuity_urgent_attempts a ON a.item_id=i.id "
            "WHERE i.urgent=1 AND i.status='open' AND i.created<=? AND i.expires>? AND a.item_id IS NULL "
            "ORDER BY i.created LIMIT 1",
            (now - DELAY_SECONDS, now),
        )
        if not item:
            return
        brief = fixed_brief(row["number"])
        try:
            self.h.outbound.check(brief, hours=True)
        except Fault:
            return
        with self.store.lock:
            self.store.execute("BEGIN IMMEDIATE")
            try:
                thread = item["thread_id"] or self.h.threads.bind(
                    "urgent", item["id"], title="Urgent PANINI review", continue_recent=False
                )
                call = self.h.outbound.draft_standing(brief, thread, row["id"], item["id"])
                self.store.execute(
                    "INSERT INTO continuity_urgent_attempts(item_id,grant_id,brief_id,created) VALUES (?,?,?,?)",
                    (item["id"], row["id"], call["id"], now),
                )
                self.store.execute("COMMIT")
            except BaseException:
                self.store.execute("ROLLBACK")
                raise
