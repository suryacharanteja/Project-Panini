"""Console and extension-pairing routes shared by the web console and the PANINI shell."""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from ..extension_pairing import PairingError

router = APIRouter()


class PairRedemption(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=8, max_length=20)
    label: str = Field(default="", max_length=100)


def pairing(request: Request):
    return request.app.state.pairing


@router.post("/api/extension/pairing-code")
async def create_pairing_code(request: Request):
    return pairing(request).create_code()


@router.post("/api/extension/pair")
async def redeem_pairing_code(payload: PairRedemption, request: Request):
    # The one-time code is the authority here; ApiBoundary admits extension origins for this path only.
    origin = request.headers.get("origin", "")
    try:
        device = pairing(request).redeem(payload.code, origin, payload.label)
    except PairingError as exc:
        raise HTTPException(403, str(exc)) from None
    return {**device, "protocol": 4}


@router.get("/api/extension/devices")
async def paired_devices(request: Request):
    return {"devices": pairing(request).devices()}


@router.delete("/api/extension/devices/{device_id}")
async def forget_device(device_id: str, request: Request):
    if not pairing(request).revoke(device_id):
        raise HTTPException(404, "No paired extension has this ID.")
    bridge = getattr(request.app.state, "extension_bridge", None)
    if bridge and bridge.device_id == device_id:
        socket = bridge._socket
        await bridge.detach(reason="Pairing removed")
        if socket:
            await socket.close(code=1008, reason="This extension was unpaired. Pair it again to continue.")
    return {"status": "revoked"}


@router.post("/api/console/panini-observer")
async def panini_observer(request: Request):
    """Let a signed-in console watch the live PANINI conversation (read-only)."""
    coordinator = request.app.state.panini
    live = [s for s in (coordinator.sessions.values() if coordinator else []) if s.state != "closed"]
    if not live:
        return {"session_id": None, "ticket": None}
    session = live[-1]
    return {
        "session_id": session.session_id,
        "ticket": coordinator.auth.ticket("observer", session.session_id),
        "expires_in": 30,
    }
