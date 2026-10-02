import asyncio
import hmac
import json
import logging
import re
import secrets
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..extension_bridge import PROTOCOL
from ..extension_pairing import proof as pairing_proof

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/browser")
# The extension dials a fixed, unprefixed path, so it gets its own router.
ws_router = APIRouter()


class BrowserRunRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    instruction: str = Field(min_length=1, max_length=5000)
    url: str = Field(default="", max_length=2048)
    run_policy: Literal["continuous", "bounded"] = "bounded"
    max_steps: int = Field(default=10, ge=1, le=1000)
    timeout_seconds: int = Field(default=120, ge=5, le=3600)
    # "chromium" drives a bundled browser with the full action registry;
    # "extension" drives the user's own Chrome, with their logins.
    mode: Literal["chromium", "extension"]
    target_id: str = Field(default="", max_length=100)

    @field_validator("url")
    @classmethod
    def web_url(cls, value):
        if value:
            parsed = urlsplit(value)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
            ):
                raise ValueError("URL must be an HTTP or HTTPS address without credentials")
        return value


def get_bridge(request: Request):
    if getattr(request.app.state, "extension_bridge", None) is None:
        from ..extension_bridge import ExtensionBridge

        request.app.state.extension_bridge = ExtensionBridge()
    return request.app.state.extension_bridge


def get_extension_loop(request: Request):
    if getattr(request.app.state, "extension_loop", None) is None:
        from ..extension_loop import ExtensionLoop

        request.app.state.extension_loop = ExtensionLoop(get_bridge(request))
    return request.app.state.extension_loop


NONCE = re.compile(r"^[A-Za-z0-9_-]{32,128}$")
DEVICE = re.compile(r"^[a-f0-9]{32}$")


@ws_router.websocket("/ws/extension")
async def extension_socket(websocket: WebSocket):
    """The extension's end of the bridge: paired devices only, mutually authenticated.

    1. extension -> hello {device_id, nonce}          (no secret on the wire)
    2. server    -> challenge {nonce, proof}           (server proves the pairing first)
    3. extension -> auth {proof}                       (extension proves the pairing)
    4. server    -> connected
    The extension never executes an action from a server that failed step 2.
    """
    origin = websocket.headers.get("origin", "")
    if origin and not origin.startswith(("chrome-extension://", "moz-extension://")):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    bridge = get_bridge(websocket)
    store = websocket.app.state.pairing
    try:
        hello = await asyncio.wait_for(websocket.receive_json(), timeout=5)
        device_id = hello.get("device_id", "") if isinstance(hello, dict) else ""
        client_nonce = hello.get("nonce", "") if isinstance(hello, dict) else ""
        if (
            not isinstance(hello, dict)
            or hello.get("type") != "hello"
            or hello.get("source") != "jev-rev-browxtension"
            or hello.get("protocol") != PROTOCOL
        ):
            await websocket.close(
                code=1008, reason="Update JevRev Claw to 3.0 or later, then pair it with PANINI."
            )
            return
        secret = store.secret(device_id) if DEVICE.match(str(device_id)) else None
        paired_origin = store.origin(device_id) if secret else ""
        if (
            not secret
            or not NONCE.match(str(client_nonce))
            or (origin and paired_origin and origin != paired_origin)
        ):
            await websocket.close(code=1008, reason="Pair this extension with PANINI to connect.")
            return
        server_nonce = secrets.token_urlsafe(32)
        await websocket.send_json(
            {
                "type": "challenge",
                "nonce": server_nonce,
                "proof": pairing_proof(secret, "server", client_nonce, server_nonce),
            }
        )
        answer = await asyncio.wait_for(websocket.receive_json(), timeout=5)
        expected = pairing_proof(secret, "client", server_nonce, client_nonce)
        if (
            not isinstance(answer, dict)
            or answer.get("type") != "auth"
            or not hmac.compare_digest(str(answer.get("proof", "")), expected)
        ):
            await websocket.close(code=1008, reason="Extension authentication failed. Pair it again.")
            return
        # Only after authentication may a connection affect the current attachment.
        # The same device may replace its own stale socket; a different one may not.
        if bridge.connected and bridge.device_id != device_id:
            await websocket.close(
                code=1008,
                reason="Another paired browser is connected. Disconnect it, then click Reconnect.",
            )
            return
        await bridge.attach(websocket, device_id)
        bridge.on_message(hello, websocket)
        await websocket.send_json(
            {"type": "connected", "protocol": PROTOCOL, "capabilities": ["workspace-v1", "cdp-v1"]}
        )
        while True:
            message = await websocket.receive_json()
            if not store.secret(device_id):
                await websocket.close(
                    code=1008, reason="This extension was unpaired. Pair it again to continue."
                )
                return
            bridge.on_message(message, websocket)
    except TimeoutError:
        await websocket.close(code=1008, reason="Extension handshake timed out. Reload Jev-Rev-browxtension.")
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.warning("Extension socket closed: %s", exc)
    finally:
        await bridge.detach(socket=websocket)


@router.get("/extension/status")
async def extension_status(request: Request):
    return get_bridge(request).status()


def get_loop(request: Request):
    # Import only on demand: comparison and voice remain available if browser
    # dependencies or the installed Chromium executable are unavailable.
    if request.app.state.browser_loop is None:
        from ..config import HEADLESS
        from ..jev_browser_loop import JevBrowserLoop

        request.app.state.browser_loop = JevBrowserLoop(headless=HEADLESS)
    return request.app.state.browser_loop


@router.get("/status")
async def status(request: Request, mode: Literal["chromium", "extension"] = "chromium"):
    loop = request.app.state.extension_loop if mode == "extension" else request.app.state.browser_loop
    return {
        "active": any(loop and loop.loop_active for loop in browser_loops(request)),
        "stats": loop.get_stats()
        if loop
        else {
            "browser_started": False,
            "total_instructions": 0,
            "recent_instructions": [],
            "last_error": None,
        },
    }


@router.post("/reset")
async def reset(request: Request):
    if getattr(request.app.state, "voice_session", None):
        raise HTTPException(409, "End the live conversation before clearing browser history")
    loops = browser_loops(request)
    if any(loop and loop.loop_active for loop in loops):
        raise HTTPException(409, "Stop the running instruction before resetting history")
    for loop in loops:
        if loop:
            loop.instruction_history.clear()
            if hasattr(loop, "last_run"):
                loop.last_run = {}
    return {"status": "reset", "message": "History cleared"}


@router.post("/stop")
async def stop(request: Request):
    voice = getattr(request.app.state, "voice_session", None)
    if voice:
        await voice.conversation.stop()
    loops = browser_loops(request)
    for loop in loops:
        if loop:
            loop.cancel()
    return {"status": "stopping" if any(loop and loop.loop_active for loop in loops) else "idle"}


def browser_loops(request: Request):
    return (request.app.state.browser_loop, request.app.state.extension_loop)


def prepare_run(payload: BrowserRunRequest, request: Request, *, voice_owner=False, owner="The web console"):
    if getattr(request.app.state, "voice_session", None) and not voice_owner:
        raise HTTPException(
            409, "Live conversation owns the browser. End the conversation to use typed commands."
        )
    options = payload.model_dump()
    if options.pop("run_policy") == "continuous":
        options.update(max_steps=None, timeout_seconds=None)
    mode = options.pop("mode")
    target_id = options.pop("target_id")
    if mode == "extension":
        bridge = get_bridge(request)
        if not bridge.connected or bridge.attached_tab is None:
            raise HTTPException(
                409,
                "No website tab is attached. Open Jev-Rev-browxtension, choose a website tab and click Attach selected tab.",
            )
        if not bridge.compatible:
            raise HTTPException(
                409,
                "Reload Jev-Rev-browxtension to version 1.3 or later in chrome://extensions, then attach the tab again.",
            )
        if not target_id or target_id != bridge.target_id:
            raise HTTPException(409, "Attached tab changed. Review the browser target and run again.")
        options["target_id"] = target_id
        loop = get_extension_loop(request)
    else:
        try:
            loop = get_loop(request)
        except ImportError as exc:
            raise HTTPException(503, "Browser dependencies are unavailable; run npm run setup") from exc
    busy = next((active for active in browser_loops(request) if active and active.loop_active), None)
    if busy:
        holder = getattr(busy, "owner", "") or "Another request"
        raise HTTPException(409, f"{holder} is using the browser. Stop it or wait for it to finish.")
    loop.owner = owner
    return loop, options


@router.post("/run-stream")
async def run(payload: BrowserRunRequest, request: Request):
    loop, options = prepare_run(payload, request)

    async def events():
        async for event in loop.run_instruction(**options):
            yield json.dumps(event).encode() + b"\n"

    return StreamingResponse(
        events(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
