"""The remote API surface (V2-14 §5.8): what a paired phone can do, over any transport.

Every route requires a fabric session (see server.py); the principal is the verified owner on the phone app.
Decisions on interrupts carry the device's signature over (interrupt_id, nonce, action_digest, status,
payload_digest), stored with the decision. I10: a phone can always pause PANINI.
"""

import hashlib
import json

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from ..routes.outbound import add_outbound
from . import crypto

router = APIRouter(prefix="/api/remote")


def _fabric(request: Request):
    fabric = getattr(request.app.state, "fabric", None)
    if fabric is None:
        raise HTTPException(404, "Remote access is off.")
    try:
        return fabric, fabric.verify(request.headers)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from None


def _harness(request: Request):
    h = getattr(request.app.state, "harness", None)
    if h is None:
        raise HTTPException(404, "The PANINI harness is disabled.")
    return h


add_outbound(router, _fabric)


def payload_digest(payload: dict | None) -> str:
    return hashlib.sha256(
        json.dumps(payload or {}, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def decision_statement(interrupt_id, nonce, action_digest, status, payload) -> bytes:
    return f"{interrupt_id}|{nonce}|{action_digest or ''}|{status}|{payload_digest(payload)}".encode()


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nonce: str
    action_digest: str | None = None
    status: str = Field(pattern="^(resolved|cancelled)$")
    payload: dict | None = None
    signature: str
    ceremony_id: str | None = None
    credential: dict | None = None


class PasskeyBegin(BaseModel):
    model_config = ConfigDict(extra="forbid")
    origin: str = Field(max_length=300)


class PasskeyFinish(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ceremony_id: str = Field(max_length=100)
    credential: dict


class PasskeyDecision(Decision):
    origin: str = Field(max_length=300)


def _passkeys(fabric):
    service = getattr(fabric, "passkeys", None)
    if service is None:
        raise HTTPException(503, "Passkeys are not configured.")
    return service


def _passkey_call(fn, *args, **kwargs):
    import sqlite3

    from webauthn.helpers.exceptions import WebAuthnException

    try:
        return fn(*args, **kwargs)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    except (WebAuthnException, sqlite3.IntegrityError):
        raise HTTPException(403, "Passkey verification failed. Start again.") from None


@router.get("/passkeys")
async def passkeys(request: Request):
    fabric, device_id = _fabric(request)
    return {"passkeys": _passkeys(fabric).list(device_id)}


@router.post("/passkeys/register/options")
async def passkey_options(payload: PasskeyBegin, request: Request):
    fabric, device_id = _fabric(request)
    return _passkey_call(_passkeys(fabric).begin, device_id, payload.origin)


@router.post("/passkeys/register")
async def passkey_register(payload: PasskeyFinish, request: Request):
    fabric, device_id = _fabric(request)
    return _passkey_call(_passkeys(fabric).finish, device_id, payload.ceremony_id, payload.credential)


@router.delete("/passkeys/{credential_id}")
async def passkey_remove(credential_id: str, request: Request):
    fabric, device_id = _fabric(request)
    _passkeys(fabric).remove(device_id, credential_id)
    return {"removed": True}


@router.post("/interrupts/{interrupt_id}/passkey/options")
async def passkey_decision_options(interrupt_id: str, payload: PasskeyDecision, request: Request):
    fabric, device_id = _fabric(request)
    view = _harness(request).interrupts.get(interrupt_id, include_secret=True)
    if (
        view["status"] != "open"
        or view["level"] != "verify"
        or view["nonce"] != payload.nonce
        or view["action_digest"] != payload.action_digest
    ):
        raise HTTPException(409, "The request changed or cannot use a passkey. Review it again.")
    statement = decision_statement(
        interrupt_id, payload.nonce, payload.action_digest, payload.status, payload.payload
    )
    if not crypto.verify(fabric.devices.active(device_id)["sign_pub"], statement, payload.signature):
        raise HTTPException(403, "The decision is not signed by this device.")
    return _passkey_call(_passkeys(fabric).begin, device_id, payload.origin, statement=statement)


class Ask(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=4000)


class Share(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(default="", max_length=300)
    text: str = Field(default="", max_length=8000)
    url: str = Field(default="", max_length=2048)
    note: str = Field(default="", max_length=1000)


class Subscription(BaseModel):
    model_config = ConfigDict(extra="forbid")
    endpoint: str = Field(pattern="^https://")
    p256dh: str
    auth: str


@router.get("/status")
async def status(request: Request):
    _fabric(request)
    from .. import policy

    h = _harness(request)
    return {"paused": policy.current().paused(), **h.status()}


@router.get("/interrupts")
async def interrupts(request: Request):
    _fabric(request)
    return {"interrupts": _harness(request).interrupts.list()}


@router.get("/interrupts/{interrupt_id}")
async def interrupt(interrupt_id: str, request: Request):
    _fabric(request)
    return _harness(request).interrupts.get(interrupt_id, include_secret=True)


@router.post("/interrupts/{interrupt_id}/resolve")
async def resolve(interrupt_id: str, decision: Decision, request: Request):
    fabric, device_id = _fabric(request)
    from ..interrupts import decided_by

    device = fabric.devices.active(device_id)
    statement = decision_statement(
        interrupt_id, decision.nonce, decision.action_digest, decision.status, decision.payload
    )
    if not crypto.verify(device["sign_pub"], statement, decision.signature):
        raise HTTPException(403, "The decision is not signed by this device.")
    view = _harness(request).interrupts.get(interrupt_id, include_secret=True)
    if view["nonce"] != decision.nonce or view["action_digest"] != decision.action_digest:
        raise HTTPException(409, "The request changed. Review it again.")
    if view["level"] == "local":
        raise HTTPException(403, "This request must be approved on the PC.")
    if view["level"] == "verify" and not (decision.credential and decision.ceremony_id):
        raise HTTPException(403, "This request needs a passkey.")
    method = "tap"
    if decision.credential is not None or decision.ceremony_id is not None:
        if not decision.credential or not decision.ceremony_id:
            raise HTTPException(403, "A complete passkey response is required.")
        _passkey_call(
            _passkeys(fabric).finish,
            device_id,
            decision.ceremony_id,
            decision.credential,
            statement=statement,
        )
        method = "passkey"
    # Resolve rechecks the current nonce, digest, expiry, authority and first decision.
    return _harness(request).interrupts.resolve(
        interrupt_id,
        nonce=decision.nonce,
        action_digest=decision.action_digest,
        status=decision.status,
        payload=decision.payload,
        decided_by=decided_by("phone_app", method, device_id=device_id, signature=decision.signature),
    )


@router.post("/pause")
async def pause(request: Request):
    """I10: every remote surface can stop what it can see."""
    _, device_id = _fabric(request)
    from .. import policy

    policy.current().pause(True, by=f"device:{device_id}")
    return {"paused": True}


@router.get("/notices")
async def notices(request: Request):
    _fabric(request)
    return {"notices": _harness(request).notices.list()}


@router.post("/push/subscribe")
async def subscribe(subscription: Subscription, request: Request):
    fabric, device_id = _fabric(request)
    import time

    fabric.devices.store.execute(
        "INSERT OR REPLACE INTO push_subscriptions(device_id, endpoint, p256dh, auth, created) VALUES (?,?,?,?,?)",
        (device_id, subscription.endpoint, subscription.p256dh, subscription.auth, time.time()),
    )
    return {"subscribed": True}


@router.delete("/push/subscribe")
async def unsubscribe(request: Request):
    fabric, device_id = _fabric(request)
    fabric.devices.store.execute("DELETE FROM push_subscriptions WHERE device_id=?", (device_id,))
    return {"subscribed": False}


@router.get("/devices/self")
async def me(request: Request):
    fabric, device_id = _fabric(request)
    device = fabric.devices.get(device_id)
    return {k: device[k] for k in ("id", "name", "kind", "status", "created")}


@router.delete("/devices/self")
async def forget(request: Request):
    """Forget this phone: its keys stop working at once."""
    fabric, device_id = _fabric(request)
    fabric.revoke(device_id)
    return {"forgotten": True}


@router.post("/ask")
async def ask(payload: Ask, request: Request):
    """A request from the phone. It runs as a background task (least privilege); its approvals come back here."""
    _, device_id = _fabric(request)
    task = _harness(request).tasks.submit(payload.text, origin="phone", title=payload.text[:80])
    return {"task_id": task["id"], "title": task.get("title", "")}


@router.post("/share")
async def share(payload: Share, request: Request):
    """Something shared from another app on the phone (V2-15 §5.5). The shared content is untrusted: it reaches
    PANINI inside an untrusted envelope, and only the person's own note says what to do with it."""
    _fabric(request)
    from ..policy.provenance import quarantine

    if not (payload.text or payload.url or payload.title):
        raise HTTPException(422, "Nothing was shared.")
    shared = {"title": payload.title, "text": payload.text, "url": payload.url}
    instruction = payload.note.strip() or "Tell me what this is and ask what I want to do with it."
    goal = " ".join([instruction, "Shared from my phone:", quarantine(shared, ["shared"])["untrusted"]])
    task = _harness(request).tasks.submit(
        goal[:4000], origin="phone-share", title=(payload.title or payload.url or "Shared item")[:80]
    )
    return {"task_id": task["id"]}


@router.get("/push/key")
async def push_key(request: Request):
    fabric, _ = _fabric(request)
    return {"key": fabric.push_key}


def _thread_view(task):
    return {key: task.get(key) for key in ("id", "title", "goal", "status", "created", "result")}


@router.get("/threads")
async def threads(request: Request):
    _fabric(request)
    return {"threads": [_thread_view(task) for task in _harness(request).tasks.list(limit=50)]}


@router.get("/personas")
async def personas(request: Request):
    _fabric(request)
    from .. import v2flags

    if not v2flags.enabled("PANINI_PERSONAS"):
        return {"enabled": False, "personas": [], "default_id": "panini"}
    service = _harness(request).personas
    return {
        "enabled": True,
        "personas": [service.presentation(p["id"]) for p in service.list()],
        "default_id": service.surface_default("phone_app"),
    }


@router.get("/calls")
async def calls(request: Request):
    _fabric(request)
    from ..telephony.inbound import runtime

    result = runtime(request.app).monitor()
    outbound = _harness(request).outbound
    active = outbound.monitor()
    if active:
        result["active"] = {**active, "persona": _harness(request).personas.presentation("panini")}
    result["calls"] += [
        {
            "id": row["id"],
            "state": row["state"],
            "duration_s": (row["outcome"] or {}).get("duration_s", 0),
            "reason": row["reason"],
            "receipt_id": row["receipt_id"],
            "summary": (row["outcome"] or {}).get("summary", ""),
            "direction": "outbound",
            "usage_seconds": (row["outcome"] or {}).get("usage_seconds"),
            "final_usage_seconds": (row["outcome"] or {}).get("final_usage_seconds"),
            "finalization_confirmed": (row["outcome"] or {}).get("finalization_confirmed", False),
            "outcome_report": row["outcome_report"],
        }
        for row in outbound.status()["briefs"]
        if row["receipt_id"]
    ]
    return result


@router.post("/calls/{call_id}/{action}")
async def call_action(call_id: str, action: str, request: Request):
    _fabric(request)
    from ..telephony.inbound import runtime

    service = runtime(request.app)
    outbound = _harness(request).outbound
    if outbound.active and outbound.active["id"] == call_id:
        await outbound.control(call_id, action)
        return {"status": "accepted"}
    if not service.active or service.active.id != call_id:
        raise HTTPException(409, "This call has ended or changed.")
    if action == "end":
        await service.end(reason="Ended on the paired phone.")
    elif action == "verify":
        service.request_verification()
    elif action == "transfer":
        await service.transfer()
    else:
        raise HTTPException(404, "Unknown call control.")
    return {"status": "accepted"}


@router.get("/threads/{task_id}")
async def thread(task_id: str, request: Request):
    _fabric(request)
    tasks = _harness(request).tasks
    try:
        task = tasks.get(task_id)
    except KeyError:
        raise HTTPException(404, "This task is no longer available.") from None
    from .. import v2flags

    h = _harness(request)
    messages = []
    if v2flags.enabled("PANINI_PERSONAS"):
        rows = h.store.all(
            "SELECT id,persona_id,text,created FROM persona_messages WHERE thread_id=? ORDER BY id DESC LIMIT 100",
            (task_id,),
        )
        messages = [
            {**dict(row), "persona": h.personas.presentation(row["persona_id"])} for row in reversed(rows)
        ]
    return {
        "thread": _thread_view(task),
        "events": tasks.events(task_id=task_id, limit=100),
        "messages": messages,
    }


@router.post("/threads/{task_id}/stop")
async def stop_thread(task_id: str, request: Request):
    _fabric(request)
    try:
        return {"thread": _thread_view(_harness(request).tasks.cancel(task_id))}
    except KeyError:
        raise HTTPException(404, "This task is no longer available.") from None
