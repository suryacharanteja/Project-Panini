"""Verified ingress -> accepted SIP session -> bounded, authenticated backend work."""

import asyncio
import contextlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from uuid import uuid4

import httpx
from panini.conversation import control_intent

from .. import v2flags
from ..panini.contracts import Fault
from .callers import Callers, from_headers
from .connection import PhoneConnection
from .prompts import PHONE_INSTRUCTIONS

SESSION_ID = re.compile(r"^[a-zA-Z0-9_-]{1,150}$")


def limit(key, default, maximum):
    try:
        value = int(os.getenv(key, str(default)))
    except ValueError:
        value = default
    return min(maximum, max(1, value))


@dataclass
class Call:
    id: str
    caller: dict
    thread: str
    started: float
    connection: object = None
    verified: bool = False
    closing: bool = False
    attempts: int = 0
    digits: str = ""
    requests: int = 0
    work: asyncio.Task | None = None
    timer: asyncio.Task | None = None
    verification_timer: asyncio.Task | None = None
    watcher: asyncio.Task | None = None
    verify_task: asyncio.Task | None = None
    pending: set = field(default_factory=set)
    transcript: list = field(default_factory=list)
    transcript_chars: int = 0
    receipt: str | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class CallService:
    def __init__(self, harness, *, provider=None, connection_factory=PhoneConnection, clock=time.time):
        self.h, self.store = harness, harness.store
        self.callers = Callers(self.store)
        self.provider = provider or self._provider
        self.connection_factory, self.clock = connection_factory, clock
        self.active = None
        self.lock = asyncio.Lock()
        self._registry = None

    async def _provider(self, session_id, action, body=None):
        if not SESSION_ID.fullmatch(session_id):
            raise ValueError("Invalid call ID.")
        async with httpx.AsyncClient(timeout=20) as client:
            response = (
                await client.post(
                    f"https://api.openai.com/v1/live/sessions/{session_id}/{action}",
                    headers={"Authorization": f"Bearer {os.getenv('OPENAI_API_KEY', '')}"},
                    json=body,
                )
                if body is not None
                else await client.post(
                    f"https://api.openai.com/v1/live/sessions/{session_id}/{action}",
                    headers={"Authorization": f"Bearer {os.getenv('OPENAI_API_KEY', '')}"},
                )
            )
        if response.status_code != 200:
            # Provider bodies may contain phone metadata or credentials.
            raise RuntimeError(f"Phone provider rejected {action} ({response.status_code}).")

    def status(self):
        return {
            "enabled": v2flags.enabled("PANINI_PHONE_LINE"),
            "active": self.active.id if self.active else None,
            "callers": self.callers.list(),
            "calls": self.store.all("SELECT * FROM calls ORDER BY started DESC LIMIT 50"),
            "setup": "Route a TLS SIP number to your OpenAI project and configure its signed Live webhook in Remote access. Red-team qualification is future development.",
        }

    def monitor(self):
        call = self.active
        return {
            "active": None
            if call is None
            else {
                "id": call.id,
                "verified": call.verified,
                "persona": self.h.personas.presentation(call.caller["persona_id"]),
                "number": call.caller["number"],
                "transcript": call.transcript[-100:],
            },
            "calls": self.store.all(
                "SELECT id,persona_id,state,verified,started,ended,duration_s,reason,receipt_id FROM calls ORDER BY started DESC LIMIT 20"
            ),
        }

    def request_verification(self):
        call = self.active
        if call and not call.verified and (call.verify_task is None or call.verify_task.done()):
            call.verify_task = asyncio.create_task(self.verify_on_phone(call))
            call.verify_task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)

    async def transfer(self):
        call = self.active
        if call is None or not call.verified or call.caller["kind"] != "owner":
            raise Fault("CALL_UNAVAILABLE", "An owner call must be verified before transfer.")
        self.authorized(call)
        await self.provider(call.id, "refer", {"target_uri": "tel:" + call.caller["number"]})

    async def webhook(self, hook_id, headers, body):
        try:
            event = json.loads(body)
        except (ValueError, TypeError):
            return {"status": 400, "body": "Invalid call event."}
        if not isinstance(event, dict) or not isinstance(event.get("data", {}), dict):
            return {"status": 400, "body": "Invalid call event."}
        if event.get("type") not in {"live.transport.incoming", "live.call.incoming"}:
            return {"status": 202, "body": "ignored"}
        data = event.get("data", {})
        if event["type"] == "live.transport.incoming" and data.get("type") != "sip":
            return {"status": 202, "body": "ignored"}
        identifier = data.get("session_id", "")
        if not isinstance(identifier, str) or not SESSION_ID.fullmatch(identifier):
            return {"status": 400, "body": "Invalid call."}
        await self.incoming(identifier, data.get("sip_headers", []))
        return {"status": 200, "body": ""}

    async def incoming(self, identifier, headers):
        async with self.lock:
            if self.store.one("SELECT id FROM calls WHERE id=?", (identifier,)):
                return  # Decision dedupe also covers different webhook delivery IDs.
            number = from_headers(headers)
            caller = self.callers.get(number) if number else None
            now = self.clock()
            day = now - now % 86400
            daily = self.store.one(
                "SELECT COUNT(*) AS n,COALESCE(SUM(duration_s),0) AS seconds FROM calls WHERE started>=? AND state NOT IN ('rejected','blocked')",
                (day,),
            )
            reason, code = "", 486
            if not v2flags.enabled("PANINI_PHONE_LINE"):
                reason = "Phone line is off."
            elif not caller or caller["kind"] == "blocked":
                reason, code = "Caller is not permitted.", 603
            elif self.active:
                reason = "Another call is active."
            elif (
                daily["n"] >= limit("PANINI_CALLS_PER_DAY", 20, 1000)
                or daily["seconds"] >= limit("PANINI_CALL_MINUTES_PER_DAY", 120, 1440) * 60
            ):
                reason = "Daily call limit reached."
            profile = None
            if not reason:
                try:
                    persona = self.h.personas.get(caller["persona_id"])
                    if caller["persona_id"] != "panini" and not v2flags.enabled("PANINI_PERSONAS"):
                        raise KeyError()
                    profile = {"voice": persona["voice"] or os.getenv("VOICE_NAME", "marin")}
                except KeyError:
                    reason = "Caller persona is unavailable."
            thread = (
                self.h.threads.bind("phone_line", identifier, title="Phone call", continue_recent=False)
                if not reason
                else None
            )
            self.store.execute(
                "INSERT INTO calls(id,number,persona_id,state,started,thread_id,reason) VALUES (?,?,?,?,?,?,?)",
                (
                    identifier,
                    number or "unknown",
                    caller["persona_id"] if caller else "panini",
                    "rejected" if reason else "accepting",
                    now,
                    thread,
                    reason,
                ),
            )
            if reason:
                await self.provider(identifier, "reject", {"status_code": code})
                return
            call = Call(identifier, caller, thread, now)
            self.active = call
            call.connection = self.connection_factory(
                os.getenv("OPENAI_API_KEY", ""),
                lambda e: self.event(call, e),
                lambda text, did=None: self.request(call, text, did),
                on_digits=lambda d: self.digits(call, d),
                model=os.getenv("VOICE_MODEL", "gpt-live-1"),
                voice=profile["voice"],
            )
        try:
            await self.provider(
                identifier,
                "accept",
                {
                    "session": {
                        "type": "live",
                        "model": os.getenv("VOICE_MODEL", "gpt-live-1"),
                        "instructions": PHONE_INSTRUCTIONS,
                        "audio": {"output": {"voice": profile["voice"]}},
                        "delegation": {"type": "client"},
                        "store": False,
                    }
                },
            )
            await call.connection.attach(identifier)
            if call.closing:
                await self.provider(identifier, "hangup")
                await call.connection.close()
                return
            self.store.execute("UPDATE calls SET state='verification_pending' WHERE id=?", (identifier,))
            remaining = limit("PANINI_CALL_MINUTES_PER_DAY", 120, 1440) * 60 - daily["seconds"]
            call.timer = asyncio.create_task(
                self.expire(
                    call,
                    min(limit("PANINI_CALL_MAX_MINUTES", 30, 120) * 60, remaining),
                    "Call time limit reached.",
                )
            )
            call.verification_timer = asyncio.create_task(
                self.expire(call, 60, "Caller verification timed out.")
            )
            call.watcher = asyncio.create_task(self.watch(call))
            await call.connection.speak(
                "Hi, this is PANINI, an AI assistant. Enter your PIN on the keypad followed by #. Then listen."
            )
        except BaseException:
            await self.end(call, "Phone connection failed.", state="failed")
            raise

    async def expire(self, call, seconds, reason):
        await asyncio.sleep(seconds)
        await self.end(call, reason)

    async def watch(self, call):
        while not call.closing:
            await asyncio.sleep(0.5)
            try:
                self.authorized(call)
            except (Fault, KeyError):
                await self.end(call, "Caller access or phone settings changed.")
                return

    def authorized(self, call):
        if call.closing or self.active is not call or not v2flags.enabled("PANINI_PHONE_LINE"):
            raise Fault("CALL_CLOSED", "The phone call is no longer active.")
        current = self.callers.get(call.caller["number"])
        if (
            not current
            or current["kind"] != call.caller["kind"]
            or current["persona_id"] != call.caller["persona_id"]
            or current["pin_hash"] != call.caller["pin_hash"]
        ):
            raise Fault("CALLER_REMOVED", "Caller access changed. Call again after updating setup.")
        self.h.personas.get(call.caller["persona_id"])
        if call.caller["persona_id"] != "panini" and not v2flags.enabled("PANINI_PERSONAS"):
            raise Fault("PERSONA_UNAVAILABLE", "Personas are disabled.")

    async def digits(self, call, digit):
        if call.closing or call.verified or digit not in "0123456789*#" or len(digit) != 1:
            return
        if digit == "*":
            call.digits = ""
        elif digit != "#":
            call.digits += digit
            if len(call.digits) > 8:
                call.digits = "invalid"
        else:
            entered, call.digits = call.digits, ""
            call.attempts += 1
            try:
                self.authorized(call)
                verified = await asyncio.to_thread(self.callers.verify, call.caller, entered)
                self.authorized(call)
            except (Fault, KeyError):
                verified = False
            if verified:
                await self.verified(call)
            elif call.attempts >= 3:
                asyncio.create_task(self.end(call, "Caller verification failed."))
            else:
                await call.connection.speak("PIN not accepted. Try again using the keypad, then #.")

    async def verified(self, call):
        self.authorized(call)
        call.verified = True
        if call.verification_timer:
            call.verification_timer.cancel()
        self.store.execute("UPDATE calls SET verified=1,state='active' WHERE id=?", (call.id,))
        await call.connection.speak("Caller verified by the backend. How can I help?")

    async def verify_on_phone(self, call):
        from ..interrupts import InterruptSpec

        self.authorized(call)
        if call.verified:
            return
        view = self.h.interrupts.open(
            InterruptSpec(
                kind="approval",
                reason="caller_verification",
                title="Verify phone caller",
                message=f"Is this you calling from {call.caller['number']}? Approve only if you are on this call.",
                action={"call_id": call.id, "number": call.caller["number"]},
                level="tap",
                ttl_s=60,
            ),
            run_id=call.id,
            thread_id=call.thread,
            owner_principal={"surface": "phone_line"},
        )
        call.pending.add(view["id"])
        try:
            result = await self.h.interrupts.wait(view["id"], 60)
            if (
                result["status"] == "resolved"
                and result.get("decision", {}).get("payload", {}).get("approved") is True
            ):
                await self.verified(call)
        finally:
            call.pending.discard(view["id"])

    async def event(self, call, event):
        if event.get("type") in {"call.closed", "disconnected"}:
            asyncio.create_task(self.end(call, "Call ended.", usage=event.get("usage")))
        elif event.get("type") == "transcript" and call.verified and call.transcript_chars < 200_000:
            text = str(event.get("delta", ""))[:4000]
            call.transcript.append(
                {
                    "role": event.get("role"),
                    "text": text,
                    "start_ms": event.get("start_ms"),
                    "end_ms": event.get("end_ms"),
                }
            )
            call.transcript_chars += len(text)

    async def request(self, call, text, identifier=None):
        if call.closing:
            return
        if not call.verified:
            await call.connection.speak(
                "Please verify using the keypad PIN followed by #.", delegation_id=identifier
            )
            return
        control = control_intent(text)
        if control == "end":
            asyncio.create_task(self.end(call, "Caller ended the call."))
            return
        if control == "stop":
            if call.work:
                call.work.cancel()
            await call.connection.speak("Stopping the current request.", delegation_id=identifier)
            return
        if call.lock.locked():
            await call.connection.speak(
                "A request is still running. Say stop to cancel it.", delegation_id=identifier
            )
            return
        async with call.lock:
            try:
                self.authorized(call)
                identifier = identifier or uuid4().hex
                inserted = self.store.execute(
                    "INSERT OR IGNORE INTO call_requests(call_id,delegation_id,state) VALUES (?,?,'running')",
                    (call.id, identifier),
                ).rowcount
                if not inserted:
                    return
                if call.requests >= limit("PANINI_CALL_MAX_RUNS", 12, 100):
                    raise Fault("CALL_LIMIT", "The call's request limit is reached.")
                call.requests += 1
                call.work = asyncio.create_task(self.compute(call, text))
                result = await call.work
                self.store.execute(
                    "UPDATE call_requests SET state='finished',result=? WHERE call_id=? AND delegation_id=?",
                    (json.dumps(result), call.id, identifier),
                )
                if not call.closing:
                    await call.connection.speak(
                        f"Backend outcome: {result['outcome']}. {result['message']}", delegation_id=identifier
                    )
            except asyncio.CancelledError:
                self.store.execute(
                    "UPDATE call_requests SET state='cancelled' WHERE call_id=? AND state='running'",
                    (call.id,),
                )
            except (Fault, KeyError) as exc:
                self.store.execute(
                    "UPDATE call_requests SET state='failed' WHERE call_id=? AND state='running'", (call.id,)
                )
                await call.connection.speak(
                    getattr(exc, "message", "Caller access is unavailable."), delegation_id=identifier
                )
            except Exception:
                self.store.execute(
                    "UPDATE call_requests SET state='failed' WHERE call_id=? AND state='running'", (call.id,)
                )
                await call.connection.speak(
                    "The backend request failed. No successful outcome is confirmed.",
                    delegation_id=identifier,
                )
            finally:
                call.work = None
                for iid in list(call.pending):
                    self.h.interrupts.cancel(iid, "Phone request ended")
                call.pending.clear()

    async def compute(self, call, text):
        from ..agui.runner import ConsoleContext
        from ..interrupts import InterruptSpec
        from ..ledger.hook import LedgerHook
        from ..policy.hook import PolicyHook
        from ..registry.registry import Registry

        run_id = uuid4().hex

        async def confirm(title, detail):
            self.authorized(call)
            decision = getattr(agent.tool_context(), "decision", None)
            level = getattr(getattr(decision, "level", None), "value", "tap")
            if level not in {"tap", "verify", "local"}:
                level = "tap"
            view = self.h.interrupts.open(
                InterruptSpec(
                    kind="approval",
                    reason="phone_action",
                    title=title,
                    message="Approve this phone-call action on your paired phone or PC.",
                    preview={"detail": str(detail)[:3000]},
                    action={"title": title, "detail": detail},
                    level=level,
                    ttl_s=60,
                ),
                run_id=run_id,
                thread_id=call.thread,
                owner_principal={"surface": "phone_line"},
            )
            call.pending.add(view["id"])
            await call.connection.speak(
                "I've sent an approval to your phone or PC. Speech and keypad input cannot approve it."
            )
            resolved = await self.h.interrupts.wait(view["id"], 60)
            self.authorized(call)
            return (
                resolved["status"] == "resolved"
                and resolved.get("decision", {}).get("payload", {}).get("approved") is True
            )

        context = ConsoleContext(run_id, confirm, lambda *args, **kwargs: None, asyncio.Event())
        chosen = tuple(c for c in ("search", "code") if c in self.h.adapters)

        async def delegate(capability, instruction):
            self.authorized(call)
            if capability not in chosen:
                return {
                    "outcome": "blocked",
                    "message": "This capability is unavailable on the phone line.",
                    "results": [],
                }
            adapter = self.h.adapters[capability]
            context.task.capability = capability
            try:
                outcome = await adapter.run(context, instruction, None)
                return {
                    "outcome": outcome.outcome,
                    "message": outcome.message,
                    "results": outcome.results,
                    "evidence_ids": outcome.evidence_ids,
                }
            finally:
                await adapter.release(context.task.task_id)

        agent = self.h.agent(
            delegate=delegate,
            capabilities=chosen,
            confirm=confirm,
            surface="phone_line",
            private=call.caller["kind"] == "owner",
            persona_id=call.caller["persona_id"],
            deadline_seconds=300,
        )
        if self._registry is None:
            self._registry = Registry(
                self.h.registry.runtime, sources=self.h.registry.sources, state=self.store
            )
            self._registry.hooks = [LedgerHook(), PolicyHook(mode="enforce", policy=self.h.policy)]
        agent.registry = self._registry
        agent.run_id, agent.thread_id = run_id, call.thread
        result = await agent.run(text[:5000], session_id=call.thread)
        return {"outcome": result.outcome, "message": result.message}

    async def end(self, call=None, reason="Ended on the PC.", *, state="ended", usage=None):
        call = call or self.active
        if call is None or call.closing:
            return
        call.closing, call.digits = True, ""
        for task in (call.timer, call.verification_timer, call.watcher, call.verify_task, call.work):
            if task and task is not asyncio.current_task():
                task.cancel()
        if call.work:
            await asyncio.gather(call.work, return_exceptions=True)
        for iid in list(call.pending):
            self.h.interrupts.cancel(iid, "Phone call ended")
        try:
            with contextlib.suppress(Exception):
                await self.provider(call.id, "hangup")
            await call.connection.close()
        finally:
            try:
                self.finalize(call, state, reason, usage)
            finally:
                if self.active is call:
                    self.active = None

    def finalize(self, call, state, reason, usage):
        duration = max(0, self.clock() - call.started)
        self.store.execute(
            "UPDATE calls SET state='failed',ended=?,duration_s=?,reason='Call ended; receipt finalization incomplete' WHERE id=?",
            (self.clock(), duration, call.id),
        )
        aid = self.h.artifacts.put(
            "transcript",
            json.dumps({"untrusted": True, "messages": call.transcript}).encode(),
            "application/json",
            run_id=call.id,
        )
        receipt = self.h.ledger.begin(
            run_id=call.id,
            thread_id=call.thread,
            persona_id=call.caller["persona_id"],
            tool="phone_call",
            capability="telephony",
            hand="sip",
            args={"call_id": call.id},
            effect="communicate",
            principal={"surface": "phone_line", "verified": call.verified},
        )
        self.h.ledger.end(
            receipt["id"],
            outcome=state,
            data={"reason": reason, "duration_s": duration, "verified": call.verified},
            artifact_ids=[aid],
            dispatched=True,
        )
        self.store.execute(
            "UPDATE calls SET state=?,ended=?,duration_s=?,reason=?,usage=?,transcript_artifact_id=?,receipt_id=? WHERE id=?",
            (
                state,
                self.clock(),
                duration,
                reason[:200],
                json.dumps(usage or getattr(call.connection, "usage", None)),
                aid,
                receipt["id"],
                call.id,
            ),
        )

    async def recover(self):
        for row in self.store.all(
            "SELECT id FROM calls WHERE state IN ('accepting','verification_pending','active')"
        ):
            with contextlib.suppress(Exception):
                await self.provider(row["id"], "hangup")
            self.store.execute(
                "UPDATE calls SET state='failed',ended=?,reason='PANINI restarted; call finalization incomplete' WHERE id=?",
                (self.clock(), row["id"]),
            )

    async def close(self):
        await self.end(reason="PANINI stopped.")


def runtime(app):
    service = getattr(app.state, "phone_line", None)
    if service is None:
        service = CallService(app.state.harness)
        app.state.phone_line = service
        from ..fabric.runtime import runtime as fabric_runtime

        fabric_runtime(app).ingress.on("openai", service.webhook)
    return service
