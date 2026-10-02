"""Durable interrupts: approvals, questions, choices, reviews and takeovers any authorized surface can
resolve (docs/panini-enhancements/lld/V2-05-interrupts.md).

Rules: the first valid decision wins (atomic UPDATE ... WHERE status='open'); a decision must echo the nonce
and the action digest (I3); resolving twice with the same decision is idempotent (restore-before-decide);
expired interrupts refuse; the level decides which surfaces may resolve; background and external principals
can never resolve their own interrupts (I8), and voice/channel text never resolves approvals (I4).
"""

import asyncio
import hashlib
import json
import secrets
import time
from dataclasses import asdict, dataclass, field
from uuid import uuid4

from ..panini.contracts import Fault

KINDS = {"approval", "question", "choice", "review", "takeover"}
LEVELS = ["none", "tap", "verify", "local", "deny"]
# What each resolution method satisfies.
SATISFIES = {"tap": {"tap"}, "passkey": {"tap", "verify"}, "local": {"tap", "verify", "local"}}
NEVER_APPROVE = {"voice", "phone_line", "slack", "telegram", "whatsapp", "email"}


def digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


@dataclass
class InterruptSpec:
    kind: str
    reason: str
    title: str
    message: str
    preview: dict = field(default_factory=dict)
    action: dict | None = None
    response_schema: dict | None = None
    level: str = "tap"
    ttl_s: float = 60.0
    tool_call_id: str | None = None


APPROVAL_SCHEMA = {
    "type": "object",
    "properties": {"approved": {"type": "boolean"}},
    "required": ["approved"],
    "additionalProperties": False,
}


class Interrupts:
    def __init__(self, store, clock=time.time):
        self.store, self.clock = store, clock
        self.waiters: dict[str, list[asyncio.Future]] = {}
        self.listeners: list = []  # callables(event, interrupt_view)

    # Opening ---------------------------------------------------------------
    def open(
        self, spec: InterruptSpec, *, run_id: str, thread_id=None, task_id=None, owner_principal=None
    ) -> dict:
        if spec.kind not in KINDS:
            raise ValueError(f"Unknown interrupt kind {spec.kind!r}")
        if spec.level not in LEVELS[1:4]:
            raise ValueError(f"Interrupts need a tap, verify or local level, not {spec.level!r}")
        interrupt_id = uuid4().hex
        schema = spec.response_schema or (APPROVAL_SCHEMA if spec.kind == "approval" else None)
        action_digest = digest(spec.action) if spec.action is not None else None
        now = self.clock()
        self.store.execute(
            "INSERT INTO interrupts(id, run_id, thread_id, task_id, kind, reason, title, message, preview, action, "
            "action_digest, response_schema, level, nonce, tool_call_id, created_at, expires_at, opened_by) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                interrupt_id,
                run_id,
                thread_id,
                task_id,
                spec.kind,
                spec.reason,
                spec.title[:200],
                spec.message[:2000],
                json.dumps(spec.preview, ensure_ascii=False, default=str)[:20000],
                json.dumps(spec.action, default=str) if spec.action is not None else None,
                action_digest,
                json.dumps(schema) if schema else None,
                spec.level,
                secrets.token_urlsafe(32),
                spec.tool_call_id,
                now,
                now + spec.ttl_s,
                json.dumps(owner_principal or {}),
            ),
        )
        view = self.get(interrupt_id, include_secret=True)
        self._emit("interrupt.opened", view)
        return view

    # Reading ---------------------------------------------------------------
    def get(self, interrupt_id: str, *, include_secret: bool = False) -> dict:
        row = self.store.one("SELECT * FROM interrupts WHERE id=?", (interrupt_id,))
        if row is None:
            raise Fault("INTERRUPT_UNKNOWN", "This request is no longer available.", 404)
        return self._view(row, include_secret)

    def list(
        self, status: str | None = "open", limit: int = 100, levels: set[str] | None = None
    ) -> list[dict]:
        self.expire_overdue()
        rows = self.store.all(
            "SELECT * FROM interrupts"
            + (" WHERE status=?" if status else "")
            + " ORDER BY created_at DESC LIMIT ?",
            ((status, limit) if status else (limit,)),
        )
        views = [self._view(r, include_secret=True) for r in rows]
        return [v for v in views if levels is None or v["level"] in levels]

    def _view(self, row, include_secret):
        view = dict(row)
        for key in ("preview", "action", "response_schema", "decision", "decided_by", "opened_by"):
            if view.get(key):
                view[key] = json.loads(view[key])
        if not include_secret:
            view.pop("nonce", None)
        return view

    # Waiting ---------------------------------------------------------------
    async def wait(self, interrupt_id: str, timeout: float) -> dict:
        future = asyncio.get_running_loop().create_future()
        self.waiters.setdefault(interrupt_id, []).append(future)
        try:
            current = self.get(interrupt_id)
            if current["status"] != "open":
                return current
            return await asyncio.wait_for(future, timeout)
        except TimeoutError:
            self.expire(interrupt_id)
            raise Fault("INTERRUPT_EXPIRED", "The request expired. Nothing was done.", 410) from None
        finally:
            futures = self.waiters.get(interrupt_id, [])
            if future in futures:
                futures.remove(future)

    # Resolving -------------------------------------------------------------
    def resolve(
        self,
        interrupt_id: str,
        *,
        nonce: str,
        action_digest: str | None,
        status: str,
        payload: dict | None,
        decided_by: dict,
    ) -> dict:
        current = self.get(interrupt_id, include_secret=True)
        decision = {"status": status, "payload": payload or {}}
        if current["status"] in {"resolved", "cancelled"}:
            if current.get("decision") == decision:
                return self._public(current)  # Idempotent replay of the same decision.
            raise Fault("DECISION_ALREADY_MADE", "Someone already answered this request.", 409)
        if current["status"] == "expired" or current["expires_at"] <= self.clock():
            self.expire(interrupt_id)
            raise Fault("INTERRUPT_EXPIRED", "The request expired. Nothing was done.", 410)
        if not secrets.compare_digest(str(nonce or ""), current["nonce"]) or (
            current["action_digest"] and action_digest != current["action_digest"]
        ):
            raise Fault("INTERRUPT_CHANGED", "The request changed. Review it again.", 409)
        self._check_authority(current, decided_by)
        if status not in {"resolved", "cancelled"}:
            raise Fault("INVALID_DECISION", "Answer with resolved or cancelled.", 422)
        if status == "resolved":
            self._check_payload(current, payload or {})
        changed = self.store.execute(
            "UPDATE interrupts SET status=?, decision=?, decided_by=?, decided_at=? WHERE id=? AND status='open'",
            (status, json.dumps(decision), json.dumps(decided_by), self.clock(), interrupt_id),
        ).rowcount
        if not changed:
            return self.resolve(
                interrupt_id,
                nonce=nonce,
                action_digest=action_digest,
                status=status,
                payload=payload,
                decided_by=decided_by,
            )
        view = self.get(interrupt_id)
        self._wake(interrupt_id, view)
        self._emit("interrupt.resolved", view)
        return view

    def _check_authority(self, current, decided_by):
        surface, method = decided_by.get("surface", ""), decided_by.get("method", "tap")
        kind = decided_by.get("principal_kind", "owner")
        if kind in {"system", "external_agent", "mcp_client", "channel_user"}:
            raise Fault("NOT_ALLOWED", "Only the owner can answer this request.", 403)  # I8
        if current["kind"] in {"approval", "review", "takeover"} and (
            surface in NEVER_APPROVE or method == "voice"
        ):
            raise Fault(
                "NOT_ALLOWED", "Approvals happen on a paired device, never by voice or chat.", 403
            )  # I4
        if current["level"] not in SATISFIES.get(method, set()):
            needs = {"verify": "a passkey on your phone or the PC", "local": "the PC"}.get(
                current["level"], "a paired device"
            )
            raise Fault("NEEDS_STRONGER_APPROVAL", f"This request needs {needs}.", 403)

    @staticmethod
    def _check_payload(current, payload):
        schema = current.get("response_schema")
        if not schema:
            return
        from jsonschema import Draft202012Validator

        errors = list(Draft202012Validator(schema).iter_errors(payload))
        if errors:
            raise Fault("INVALID_DECISION", f"Invalid answer: {errors[0].message}"[:200], 422)

    def cancel(self, interrupt_id: str, reason: str):
        decision = {"status": "cancelled", "payload": {"reason": reason[:200]}}
        changed = self.store.execute(
            "UPDATE interrupts SET status='cancelled', decision=?, decided_at=? WHERE id=? AND status='open'",
            (json.dumps(decision), self.clock(), interrupt_id),
        ).rowcount
        if changed:
            view = self.get(interrupt_id)
            self._wake(interrupt_id, view)
            self._emit("interrupt.resolved", view)

    def expire(self, interrupt_id: str):
        changed = self.store.execute(
            "UPDATE interrupts SET status='expired', decided_at=? WHERE id=? AND status='open'",
            (self.clock(), interrupt_id),
        ).rowcount
        if changed:
            view = self.get(interrupt_id)
            self._wake(interrupt_id, view)
            self._emit("interrupt.expired", view)

    def expire_overdue(self):
        for row in self.store.all(
            "SELECT id FROM interrupts WHERE status='open' AND expires_at <= ?", (self.clock(),)
        ):
            self.expire(row["id"])

    def cancel_foreground_after_restart(self):
        """Foreground runs died with the previous process; their interrupts cannot be answered any more."""
        for row in self.store.all("SELECT id FROM interrupts WHERE status='open' AND task_id IS NULL"):
            self.cancel(row["id"], "PANINI restarted")

    # Plumbing --------------------------------------------------------------
    def _wake(self, interrupt_id, view):
        for future in self.waiters.pop(interrupt_id, []):
            if not future.done():
                future.set_result(view)

    def _emit(self, event, view):
        for listener in list(self.listeners):
            try:
                listener(event, self._public(view))
            except Exception:  # noqa: BLE001 -- A broken listener never blocks a decision.
                pass

    @staticmethod
    def _public(view):
        return {k: v for k, v in view.items() if k != "nonce"}


def decided_by(surface: str, method: str, *, principal_kind="owner", device_id=None, signature=None) -> dict:
    return {
        k: v
        for k, v in asdict(_DecidedBy(surface, method, principal_kind, device_id, signature)).items()
        if v
    }


@dataclass
class _DecidedBy:
    surface: str
    method: str
    principal_kind: str = "owner"
    device_id: str | None = None
    signature: str | None = None
