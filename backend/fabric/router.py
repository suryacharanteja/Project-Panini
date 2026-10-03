"""The remote API surface (V2-14 §5.8): what a paired phone can do, over any transport.

Every route requires a fabric session (see server.py); the principal is the verified owner on the phone app.
Decisions on interrupts carry the device's signature over (interrupt_id, nonce, action_digest, status,
payload_digest), stored with the decision. I10: a phone can always pause PANINI.
"""

import hashlib
import json

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

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
    return _harness(request).interrupts.resolve(
        interrupt_id,
        nonce=decision.nonce,
        action_digest=decision.action_digest,
        status=decision.status,
        payload=decision.payload,
        decided_by=decided_by("phone_app", "tap", device_id=device_id, signature=decision.signature),
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
