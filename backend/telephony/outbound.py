"""Owner-reviewed outbound SIP calls. Every dispatch is single-use; ambiguous creation is never retried."""

import asyncio
import contextlib
import ipaddress
import json
import os
import re
import time
from uuid import uuid4

import httpx
from panini.live import LiveConnection

from .. import v2flags
from ..channels.base import safe_failure
from ..core_contracts import ApprovalLevel, EffectClass, Principal, ToolSpec, stricter
from ..harness.secrets_store import SecretStore
from ..interrupts import InterruptSpec, digest
from ..interrupts.service import APPROVAL_SCHEMA, SATISFIES
from ..panini.contracts import Fault
from .brief import CallBrief
from .inbound import SESSION_ID, limit
from .numbers import calling_window, validate

CHECKLIST = (
    "personal_tasks_only",
    "ai_disclosure",
    "local_rules_and_no_recording",
    "calling_hours_and_blocks",
    "approve_every_brief",
)
TERMINAL = {"ended", "failed", "unknown", "cancelled"}


def trunk_url(value):
    match = re.fullmatch(
        r"sips:(\[[0-9a-fA-F:]+\]|[A-Za-z0-9.-]+)(?::([0-9]{1,5}))?(?:;transport=tcp)?", value
    )
    if not match:
        raise ValueError("Use sips:public-provider-host[:port], without credentials or URI parameters.")
    host = match[1].strip("[]")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if "." not in host or host.lower().endswith((".local", ".localhost", ".internal")) or ".." in host:
            raise ValueError("The SIP provider must be a public hostname.") from None
    else:
        if not address.is_global:
            raise ValueError("The SIP provider must be public.")
    if match[2] and not 1 <= int(match[2]) <= 65535:
        raise ValueError("Invalid SIP port.")
    return value


class OutboundConnection(LiveConnection):
    async def handle_event(self, event):
        if str(event.get("type", "")).startswith("transport."):
            await self.on_event(event)
            return
        await super().handle_event(event)
        if event.get("type") == "session.closed":
            await self.on_event({"type": "call.closed", "usage": self.usage})


class OutboundCalls:
    def __init__(
        self, h, *, provider=None, connection_factory=OutboundConnection, secrets=None, clock=time.time
    ):
        self.h, self.store, self.clock = h, h.store, clock
        self.secrets = (
            secrets if secrets is not None else SecretStore(self.store.path.parent / "outbound-secrets.bin")
        )
        self.provider, self.connection_factory = provider or self._provider, connection_factory
        self.live_provider = provider is None
        self.loop, self.work, self.active, self.inbound = None, None, None, None
        self.lock = asyncio.Lock()

    def settings(self):
        row = self.store.one("SELECT * FROM outbound_settings WHERE id=1")
        return {
            "config": json.loads(row["config"]) if row else {},
            "checklist_signed": bool(row and row["signed_at"]),
            "signed_at": row["signed_at"] if row else None,
            "credentials_saved": bool(self.secrets.get("trunk")),
        }

    def configure(self, config, *, username=None, password=None, checklist=None):
        if set(config) != {"provider_url", "caller_number", "owner_number"}:
            raise ValueError("Configure the SIP provider, caller number and owner takeover number.")
        config = {**config, "provider_url": trunk_url(config["provider_url"])}
        validate(config["caller_number"])
        validate(config["owner_number"])
        if self.active or self.work and not self.work.done():
            raise Fault("CALL_BUSY", "End the current call before changing its trunk.", 409)
        if username is not None or password is not None:
            if (
                not isinstance(username, str)
                or not isinstance(password, str)
                or not username.strip()
                or not password
            ):
                raise ValueError("Provide both the SIP username and password.")
            if (
                len(username.encode()) > 256
                or len(password.encode()) > 4096
                or any(c in username + password for c in "\r\n\x00")
            ):
                raise ValueError("Invalid SIP credentials.")
        if checklist is not None and (
            set(checklist) != set(CHECKLIST) or any(checklist[k] is not True for k in CHECKLIST)
        ):
            raise ValueError("The owner must acknowledge every outbound checklist item.")
        old = self.settings()
        # Changing the trunk or identity requires the owner to acknowledge it again.
        changed = old["config"] != config or username is not None
        signed = self.clock() if checklist is not None else (old["signed_at"] if not changed else None)
        if username is not None:
            self.secrets.set("trunk", {"username": username, "password": password})
        self.store.execute(
            "INSERT INTO outbound_settings VALUES (1,?,?,?) ON CONFLICT(id) DO UPDATE SET config=excluded.config,checklist=excluded.checklist,signed_at=excluded.signed_at",
            (json.dumps(config), json.dumps(dict.fromkeys(CHECKLIST, True)) if signed else None, signed),
        )
        if changed:
            for row in self.store.all("SELECT id,interrupt_id FROM outbound_briefs WHERE state='review'"):
                self.h.interrupts.cancel(row["interrupt_id"], "Outbound setup changed; review a new brief.")
                self.update(row["id"], "cancelled", "Outbound setup changed; review a new brief.")
        return self.settings()

    def enabled(self):
        settings = self.settings()
        return (
            v2flags.enabled("PANINI_OUTBOUND_CALLS")
            and settings["checklist_signed"]
            and settings["credentials_saved"]
        )

    def get(self, identifier):
        row = self.store.one("SELECT * FROM outbound_briefs WHERE id=?", (identifier,))
        if not row:
            raise Fault("CALL_UNKNOWN", "This call brief is unavailable.", 404)
        for key in ("brief", "outcome"):
            if row[key]:
                row[key] = json.loads(row[key])
        return row

    def status(self):
        return {
            **self.settings(),
            "flag": v2flags.enabled("PANINI_OUTBOUND_CALLS"),
            "enabled": bool(self.enabled()),
            "active": self.monitor(),
            "briefs": [
                self.get(r["id"])
                for r in self.store.all("SELECT id FROM outbound_briefs ORDER BY created DESC LIMIT 50")
            ],
            "blocked": self.store.all("SELECT * FROM outbound_do_not_call ORDER BY created DESC LIMIT 100"),
        }

    def monitor(self):
        call = self.active
        if not call:
            return None
        return {
            "id": call["id"],
            "number": call["brief"].number,
            "direction": "outbound",
            "verified": True,
            "transcript": call["transcript"][-100:],
            "state": self.get(call["id"])["state"],
        }

    def block(self, number, reason="Do not call requested by the owner."):
        validate(number)
        self.store.execute(
            "INSERT INTO outbound_do_not_call VALUES (?,?,?) ON CONFLICT(number) DO UPDATE SET reason=excluded.reason",
            (number, reason[:200], self.clock()),
        )

    def check(self, brief, *, hours=True, surface="avatar"):
        if not self.enabled():
            raise Fault(
                "OUTBOUND_DISABLED",
                "Outbound calls need the owner checklist, saved trunk credentials and the outbound flag.",
                409,
            )
        if self.h.policy.paused():
            raise Fault("PANINI_PAUSED", "PANINI is paused.", 409)
        if self.live_provider and not os.getenv("OPENAI_API_KEY", "").strip():
            raise Fault(
                "CALL_PROVIDER_KEY", "Configure the OpenAI API key before preparing outbound calls.", 409
            )
        if self.store.one("SELECT number FROM outbound_do_not_call WHERE number=?", (brief.number,)):
            raise Fault("DO_NOT_CALL", "This number is blocked from outbound calls.", 403)
        spec = ToolSpec(
            "place_call",
            "telephony",
            "sip",
            "Outbound call",
            "Place one owner-reviewed call",
            {},
            EffectClass.TRANSACT if brief.may_commit else EffectClass.COMMUNICATE,
        )
        decision = self.h.policy.decide(
            Principal(kind="owner", surface=surface, verified=True),
            spec,
            brief.model_dump(),
            taint={brief.number_source} if brief.number_source in {"web", "page", "email"} else set(),
        )
        if decision.action == "deny":
            raise Fault("CALL_POLICY", "Current policy blocks this outbound call.", 403)
        window = calling_window(brief.number, self.clock())
        if hours and not window["allowed"]:
            raise Fault(
                "CALLING_HOURS",
                f"Outside the 09:00–20:00 calling window. Next permitted UTC timestamp: {window['next_at']}. Review a new call then.",
                409,
            )
        return {**window, "decision": decision}

    def draft(self, data, *, thread_id=None, identifier=None, surface="avatar"):
        brief = CallBrief.model_validate(data)
        if identifier:
            surface = self.get(identifier)["surface"]
        policy = self.check(brief, hours=False, surface=surface)["decision"]
        if identifier:
            old = self.get(identifier)
            if old["state"] != "review":
                raise Fault("CALL_CHANGED", "Only an unapproved brief can be edited.", 409)
            current = self.h.interrupts.get(old["interrupt_id"])
            if current["status"] != "open":
                raise Fault("CALL_CHANGED", "The brief has already been answered.", 409)
        else:
            identifier = uuid4().hex
            thread_id = thread_id or self.h.threads.bind("outbound", identifier, title=brief.purpose[:80])
            old = None
        level = (
            "verify"
            if brief.number_source not in {"owner", "contact"}
            or validate(brief.number)["region"]
            != validate(self.settings()["config"]["owner_number"])["region"]
            else "tap"
        )
        now = self.clock()
        level = str(stricter(ApprovalLevel(level), policy.level))
        # Editing, old-review cancellation and replacement digest commit atomically.
        with self.store.lock:
            self.store.execute("BEGIN IMMEDIATE")
            try:
                if old:
                    if self.h.interrupts.get(old["interrupt_id"])["status"] != "open":
                        raise Fault("CALL_CHANGED", "The brief has already been answered.", 409)
                    self.h.interrupts.cancel(old["interrupt_id"], "Brief edited; review the replacement.")
                    thread_id = old["thread_id"]
                action = {"call_brief_id": identifier, "brief": brief.model_dump(), "surface": surface}
                review = self.h.interrupts.open(
                    InterruptSpec(
                        "review",
                        "outbound_call",
                        "Review outbound call",
                        "Review the whole brief. Editing replaces this approval. Approving places one call within calling hours.",
                        preview={
                            "brief": brief.model_dump(),
                            "effect": "transact" if brief.may_commit else "communicate",
                        },
                        action=action,
                        response_schema=APPROVAL_SCHEMA,
                        level=level,
                        ttl_s=3600,
                    ),
                    run_id=identifier,
                    thread_id=thread_id,
                    owner_principal={"surface": "avatar"},
                )
                self.store.execute(
                    "INSERT INTO outbound_briefs(id,brief,digest,interrupt_id,thread_id,created,updated,surface) VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET brief=excluded.brief,digest=excluded.digest,interrupt_id=excluded.interrupt_id,updated=excluded.updated",
                    (
                        identifier,
                        json.dumps(brief.model_dump()),
                        digest(action),
                        review["id"],
                        thread_id,
                        now,
                        now,
                        surface,
                    ),
                )
                self.store.execute("COMMIT")
            except BaseException:
                self.store.execute("ROLLBACK")
                raise
        return self.get(identifier)

    async def _provider(self, action, session_id=None, body=None):
        if session_id and not SESSION_ID.fullmatch(session_id):
            raise ValueError("Invalid session ID.")
        suffix = f"/{session_id}/{action}" if session_id else ""
        async with httpx.AsyncClient(timeout=25, follow_redirects=False) as client:
            response = await client.post(
                "https://api.openai.com/v1/live/sessions" + suffix,
                headers={"Authorization": f"Bearer {os.getenv('OPENAI_API_KEY', '')}"},
                **({"json": body} if body is not None else {}),
            )
        if response.status_code not in {200, 201}:
            raise RuntimeError("The call provider rejected the request.")
        return response.json() if action == "create" else None

    async def tick(self):
        if self.active:
            call = self.active
            try:
                self.check(call["brief"], hours=False, surface=call["surface"])
                if (
                    call.get("transfer_requested") is not None
                    and self.clock() - call["transfer_requested"] >= 30
                ):
                    raise Fault(
                        "TRANSFER_UNCONFIRMED", "Transfer completion was not confirmed within thirty seconds."
                    )
                if self.clock() - call["started"] > 180 and call["answered"] is None:
                    raise Fault("RING_TIMEOUT", "The callee did not answer.")
                if call["answered"] is not None:
                    if not call["disclosed"] and self.clock() - call["answered"] >= 10:
                        raise Fault(
                            "DISCLOSURE_MISSING", "AI disclosure was not confirmed within ten seconds."
                        )
                    if self.clock() - call["answered"] >= call["brief"].max_minutes * 60:
                        raise Fault("CALL_LIMIT", "The approved call duration was reached.")
            except Fault as error:
                await self.end(reason=error.message)
            return
        if self.work and not self.work.done():
            if not self.enabled() or self.h.policy.paused():
                self.work.cancel()
            return
        for row in self.store.all("SELECT id FROM outbound_briefs WHERE state='review' ORDER BY created"):
            brief = self.get(row["id"])
            answer = self.h.interrupts.get(brief["interrupt_id"])
            if answer["status"] == "open":
                if not self.enabled():
                    self.h.interrupts.cancel(answer["id"], "Outbound calls disabled.")
                    self.update(brief["id"], "cancelled", "Outbound calls disabled.")
                continue
            if (
                answer["status"] != "resolved"
                or answer.get("decision", {}).get("payload", {}).get("approved") is not True
            ):
                self.update(brief["id"], "cancelled", "The call was not approved.")
                continue
            if answer["action_digest"] != brief["digest"] or digest(answer["action"]) != brief["digest"]:
                self.update(brief["id"], "cancelled", "The approved brief changed.")
                continue
            self.work = asyncio.create_task(self.dial(brief["id"]))
            self.work.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
            break

    def update(self, identifier, state, reason=""):
        self.store.execute(
            "UPDATE outbound_briefs SET state=?,reason=?,updated=? WHERE id=?",
            (state, reason[:300], self.clock(), identifier),
        )

    async def dial(self, identifier):
        async with self.lock:
            row = self.get(identifier)
            if row["state"] != "review" or self.active:
                return
            receipt = None
            try:
                brief = CallBrief.model_validate(row["brief"])
                answer = self.h.interrupts.get(row["interrupt_id"])
                if (
                    answer["status"] != "resolved"
                    or answer.get("decision", {}).get("payload", {}).get("approved") is not True
                    or answer["action_digest"] != row["digest"]
                    or digest(answer["action"]) != row["digest"]
                    or digest(
                        {"call_brief_id": identifier, "brief": brief.model_dump(), "surface": row["surface"]}
                    )
                    != row["digest"]
                ):
                    raise Fault("APPROVAL_REQUIRED", "The exact call brief needs owner approval.", 403)
                policy = self.check(brief, surface=row["surface"])["decision"]
                if policy.level not in {ApprovalLevel.NONE, ApprovalLevel.TAP} and str(
                    policy.level
                ) not in SATISFIES.get(answer.get("decided_by", {}).get("method", "tap"), set()):
                    raise Fault(
                        "APPROVAL_CHANGED",
                        "Current policy needs a stronger approval. Review a new brief.",
                        403,
                    )
                if self.inbound and self.inbound.active:
                    raise Fault("CALL_BUSY", "The inbound phone line is busy.")
                day = self.clock() - self.clock() % 86400
                with self.store.lock:
                    self.store.execute("BEGIN IMMEDIATE")
                    try:
                        if self.get(identifier)["state"] != "review":
                            self.store.execute("ROLLBACK")
                            return
                        count = self.store.one(
                            "SELECT COUNT(*) AS n FROM outbound_briefs WHERE attempted_at>=?", (day,)
                        )["n"]
                        if count >= limit("PANINI_OUTBOUND_PER_DAY", 5, 20):
                            raise Fault("CALL_LIMIT", "The daily outbound call limit was reached.")
                        receipt = self.h.ledger.begin(
                            run_id=identifier,
                            thread_id=row["thread_id"],
                            tool="place_call",
                            capability="telephony",
                            hand="sip",
                            args={"approved_brief_digest": row["digest"]},
                            effect="transact" if brief.may_commit else "communicate",
                            principal={"kind": "owner", "surface": "avatar", "verified": True},
                        )
                        self.store.execute(
                            "UPDATE outbound_briefs SET state='creating',receipt_id=?,updated=?,attempted_at=? WHERE id=? AND state='review'",
                            (receipt["id"], self.clock(), self.clock(), identifier),
                        )
                        self.h.ledger.decision(
                            receipt["id"],
                            {
                                **policy.view(),
                                "interrupt_id": row["interrupt_id"],
                                "approved_brief_digest": row["digest"],
                            },
                        )
                        self.store.execute("COMMIT")
                    except BaseException:
                        self.store.execute("ROLLBACK")
                        raise
                config = self.settings()["config"]
                auth = self.secrets.get("trunk")
                body = {
                    "session": {
                        "model": os.getenv("PANINI_VOICE_MODEL", "gpt-live-1"),
                        "instructions": brief.instructions(),
                        "audio": {"output": {"voice": "marin"}},
                        "delegation": {"type": "client"},
                        "store": False,
                    },
                    "transport": {
                        "type": "sip",
                        "destination": brief.number,
                        "trunk": {
                            "provider_url": config["provider_url"],
                            "caller_number": config["caller_number"],
                            "auth": {"type": "digest", **auth},
                        },
                    },
                }
                result = await self.provider("create", body=body)
                session = result["session"]["id"]
                if not isinstance(session, str) or not SESSION_ID.fullmatch(session):
                    raise ValueError("Invalid provider session ID.")
                self.store.execute(
                    "UPDATE outbound_briefs SET session_id=? WHERE id=?", (session, identifier)
                )
                call = {
                    "id": identifier,
                    "session": session,
                    "brief": brief,
                    "surface": row["surface"],
                    "thread": row["thread_id"],
                    "receipt": receipt["id"],
                    "started": self.clock(),
                    "answered": None,
                    "disclosed": False,
                    "first_output": "",
                    "transcript": [],
                    "pending": set(),
                    "closing": False,
                    "connection": None,
                    "requests": set(),
                }
                self.active = call
                call["connection"] = self.connection_factory(
                    os.getenv("OPENAI_API_KEY", ""),
                    on_event=lambda event: self.event(call, event),
                    on_request=lambda text, delegation_id=None: self.request(call, text, delegation_id),
                )
                self.update(identifier, "connecting")
                await call["connection"].attach(session)
                self.check(brief, hours=False, surface=row["surface"])
            except asyncio.CancelledError:
                if self.active and self.active["id"] == identifier:
                    await self.end(reason="Outbound dispatch stopped.")
                elif receipt:
                    self.finish_unknown(identifier, receipt["id"])
                raise
            except Exception as error:
                safe_failure(error)
                if self.active and self.active["id"] == identifier:
                    await self.end(reason="Call setup or sideband attachment failed.")
                elif receipt:
                    self.finish_unknown(identifier, receipt["id"])
                else:
                    self.update(
                        identifier, "cancelled", getattr(error, "message", "Call prerequisites were not met.")
                    )

    def finish_unknown(self, identifier, receipt):
        self.update(
            identifier,
            "unknown",
            "Call creation is unconfirmed. No automatic retry; check the provider before reviewing a new call.",
        )
        self.h.ledger.end(
            receipt,
            outcome="unknown",
            data={"approved_brief_digest": self.get(identifier)["digest"]},
            dispatched=True,
        )
        self.h.notices.add(
            "call_receipt",
            "Outbound call needs review",
            "Call creation or finalization is unconfirmed. Check the provider before approving another call; PANINI will not retry it automatically.",
            dedupe="outbound-unknown:" + identifier,
        )

    async def event(self, call, event):
        if call["closing"] or self.active is not call:
            return
        kind = event.get("type")
        if kind == "transport.ringing":
            if call["answered"] is None:
                self.update(call["id"], "ringing")
        elif kind == "transport.answered":
            if call["answered"] is None:
                call["answered"] = self.clock()
                self.update(call["id"], "answered")
        elif kind in {"transport.failed", "call.closed", "disconnected", "error"}:
            if kind == "call.closed":
                call["connection"].finalized.set()
            asyncio.create_task(
                self.end(
                    reason="Provider closed the transferred session; actual owner takeover is unconfirmed."
                    if kind == "call.closed" and call.get("transfer_requested") is not None
                    else "Provider call ended or failed.",
                    state="failed" if kind != "call.closed" else "ended",
                )
            )
        elif kind == "transcript":
            text = str(event.get("delta", ""))[:4000]
            if sum(len(part["text"]) for part in call["transcript"]) < 200_000:
                call["transcript"].append({"role": event.get("role"), "text": text})
            if event.get("role") == "user":
                call["callee_input"] = (call.get("callee_input", "") + text)[-1000:]
                if re.search(
                    r"\b(?:do not call(?: me)? again|don't call(?: me)? again|stop calling|remove my number)\b",
                    " ".join(call["callee_input"].lower().split()),
                ):
                    self.block(call["brief"].number, "Callee requested no further calls.")
                    asyncio.create_task(self.end(reason="Callee requested do-not-call."))
            if event.get("role") == "assistant" and not call["disclosed"]:
                call["first_output"] = (call["first_output"] + text)[:1000]
                expected = " ".join(call["brief"].disclosure().lower().split())
                actual = " ".join(call["first_output"].lower().split())
                if actual.startswith(expected):
                    call["disclosed"] = True
                elif not expected.startswith(actual):
                    asyncio.create_task(
                        self.end(
                            reason="The first spoken output did not match the AI disclosure.", state="failed"
                        )
                    )

    async def request(self, call, text, delegation_id=None):
        if (
            call["closing"]
            or self.active is not call
            or not call["disclosed"]
            or call.get("transfer_requested") is not None
        ):
            return
        identifier = delegation_id or uuid4().hex
        if identifier in call["requests"] or len(call["requests"]) >= 12:
            return
        call["requests"].add(identifier)
        normalized = " ".join(str(text).lower().split())
        if any(
            phrase in normalized
            for phrase in ("do not call", "don't call", "stop calling", "remove my number")
        ):
            self.block(call["brief"].number, "Callee requested no further calls.")
            await call["connection"].speak(
                "I apologize. We will not call this number again.", delegation_id=delegation_id
            )
            await self.end(reason="Callee requested do-not-call.")
            return
        # No memories, connectors or desktop effects are offered to the third-party voice session.
        # Every additional request is held for an owner decision; payments stay forbidden.
        if re.search(r"\b(payment|deposit|card|password|pin|otp)\b", normalized):
            await call["connection"].speak(
                "I cannot provide credentials or agree to payments. Please arrange that directly with the owner.",
                delegation_id=delegation_id,
            )
            return
        await call["connection"].speak(
            "Let me check with the owner — one moment.", delegation_id=delegation_id
        )
        review = self.h.interrupts.open(
            InterruptSpec(
                "approval",
                "outbound_midcall",
                "Call needs your decision",
                "Only approve within the original brief; payments remain prohibited.",
                preview={"callee_request_untrusted": str(text)[:2000], "brief": call["brief"].model_dump()},
                action={"call_id": call["id"], "request": str(text)[:2000]},
                ttl_s=60,
            ),
            run_id=call["id"],
            thread_id=call["thread"],
            owner_principal={"kind": "external_agent", "surface": "phone_line"},
        )
        call["pending"].add(review["id"])
        try:
            answer = await self.h.interrupts.wait(review["id"], 60)
            approved = (
                answer["status"] == "resolved"
                and answer.get("decision", {}).get("payload", {}).get("approved") is True
            )
            if self.active is call and not call["closing"]:
                await call["connection"].speak(
                    "Owner decision: "
                    + (
                        "Continue only within the reviewed brief."
                        if approved
                        else "Declined. Use the reviewed fallback and do not commit."
                    ),
                    delegation_id=delegation_id,
                )
        except Fault:
            if self.active is call and not call["closing"]:
                await call["connection"].speak(
                    "Owner unavailable. Use the reviewed fallback and end politely.",
                    delegation_id=delegation_id,
                )
        finally:
            call["pending"].discard(review["id"])

    async def control(self, identifier, action, text=None):
        call = self.active
        if not call or call["id"] != identifier or call["closing"]:
            raise Fault("CALL_CHANGED", "This call has ended or changed.", 409)
        if action == "end":
            await self.end(reason="Ended by the owner.")
        elif action == "transfer":
            self.check(call["brief"], hours=False, surface=call["surface"])
            if call.get("transfer_requested") is not None:
                raise Fault("TRANSFER_PENDING", "A transfer has already been requested.", 409)
            target = self.settings()["config"]["owner_number"]
            call["transfer_requested"] = self.clock()
            self.update(identifier, "transferring", "Transfer requested; completion remains unconfirmed.")
            try:
                await self.provider("refer", call["session"], {"target_uri": "tel:" + target})
            except Exception as error:
                safe_failure(error)
                await self.end(reason="The transfer request could not be confirmed.", state="failed")
                raise Fault(
                    "TRANSFER_UNCONFIRMED", "The transfer request could not be confirmed.", 502
                ) from None
        elif action == "note":
            self.check(call["brief"], hours=False, surface=call["surface"])
            if not isinstance(text, str) or not 1 <= len(text.strip()) <= 500:
                raise ValueError("Add a note of 1–500 characters.")
            await call["connection"].send(
                "session.instructions.append",
                delegation_id=None,
                content="Owner note; original brief exclusions and no-payment rule still apply: " + text,
            )
        else:
            raise ValueError("Unknown outbound call control.")

    async def end(self, *, reason="PANINI stopped.", state="ended"):
        call = self.active
        if not call or call["closing"]:
            return
        call["closing"] = True
        for iid in list(call["pending"]):
            self.h.interrupts.cancel(iid, "Call ended.")
        try:
            if not getattr(call["connection"], "finalized", asyncio.Event()).is_set():
                try:
                    await asyncio.wait_for(self.provider("hangup", call["session"]), 20)
                except Exception:
                    state, reason = "unknown", "Remote hangup could not be confirmed. " + reason
            if call["connection"]:
                await call["connection"].close()
        finally:
            try:
                transcript = self.h.artifacts.put(
                    "transcript",
                    json.dumps({"untrusted": True, "messages": call["transcript"]}).encode(),
                    "application/json",
                    run_id=call["id"],
                )
                outcome = {
                    "status": "failed" if state != "ended" else "partial",
                    "summary": reason + " Task achievement and commitments remain unconfirmed.",
                    "commitments": [],
                    "follow_ups": [],
                    "duration_s": max(0, self.clock() - call["started"]),
                    "cost_usd": None,
                    "transcript_artifact_id": transcript,
                }
                self.h.ledger.end(
                    call["receipt"],
                    outcome=state,
                    data={
                        **outcome,
                        "approved_brief_digest": self.get(call["id"])["digest"],
                        "disclosure_confirmed": call["disclosed"],
                    },
                    artifact_ids=[transcript],
                    dispatched=True,
                )
                self.store.execute(
                    "UPDATE outbound_briefs SET state=?,reason=?,outcome=?,transcript_artifact_id=?,updated=? WHERE id=?",
                    (state, reason[:300], json.dumps(outcome), transcript, self.clock(), call["id"]),
                )
                self.h.notices.add(
                    "call_receipt",
                    "Outbound call receipt",
                    outcome["summary"],
                    dedupe="outbound:" + call["id"],
                )
            finally:
                self.active = None

    async def recover(self):
        for row in self.store.all(
            "SELECT * FROM outbound_briefs WHERE state IN ('creating','connecting','ringing','answered','transferring')"
        ):
            if row["session_id"]:
                with contextlib.suppress(Exception):
                    await self.provider("hangup", row["session_id"])
            self.finish_unknown(row["id"], row["receipt_id"])

    async def maintain(self):
        await self.recover()
        while True:
            try:
                self.h.interrupts.expire_overdue()
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                safe_failure(error)
            await asyncio.sleep(0.25)

    def start(self):
        if not self.loop:
            self.loop = asyncio.create_task(self.maintain())

    async def close(self):
        if self.loop:
            self.loop.cancel()
            await asyncio.gather(self.loop, return_exceptions=True)
            self.loop = None
        if self.work and not self.work.done():
            self.work.cancel()
            await asyncio.gather(self.work, return_exceptions=True)
        await self.end()
