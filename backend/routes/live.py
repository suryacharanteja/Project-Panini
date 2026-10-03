"""Live view routes (V2-12).

- Viewers (console under /api/harness/live, shell under /api/panini/harness/live): list, SSE stream, latest frame,
  takeover state, take / give back, and owner input to the hand you hold.
- Sources: POST /api/live/ingest/<token> from loopback only (a Playwright page's screencast); the token is
  per session and names the hand and target, so a source can only publish its own frames.
"""

import contextlib
import json
from urllib.parse import quote, unquote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response, StreamingResponse

from .. import live
from .panini import principal

LOOPBACK = {"127.0.0.1", "::1", "localhost"}
MAX_FRAME = 2_000_000
INPUTS = ("click", "type", "key", "scroll")

# Owner input per hand: async (target, action, args) -> dict. Hands register themselves.
input_handlers: dict = {}


def _enabled():
    if not live.enabled():
        raise HTTPException(404, "Live view is off (PANINI_LIVE_VIEW).")


def _hand(hand: str) -> str:
    from ..live.hub import HANDS

    if hand not in HANDS:
        raise HTTPException(404, "Unknown hand.")
    return hand


def build(prefix, guard):
    r = APIRouter(prefix=prefix)

    @r.get("/live")
    async def overview(request: Request):
        guard(request)
        _enabled()
        return {"targets": live.hub().describe(), "holding": live.takeover().holding()}

    @r.get("/live/{hand}/{target}/stream")
    async def stream(hand: str, target: str, request: Request, fps: float = 4.0):
        guard(request)
        _enabled()
        frames = live.hub().subscribe(_hand(hand), target, max_fps=min(4.0, max(0.2, fps)))

        async def body():
            async with contextlib.aclosing(frames) as source:
                yield ": live\n\n"
                async for frame in source:
                    if await request.is_disconnected():
                        return
                    yield f"event: frame\ndata: {json.dumps(frame)}\n\n"

        return StreamingResponse(
            body(), media_type="text/event-stream", headers={"Cache-Control": "no-store"}
        )

    @r.get("/live/{hand}/{target}/latest.jpg")
    async def latest(hand: str, target: str, request: Request):
        guard(request)
        _enabled()
        live.hub().touch(_hand(hand), target)
        found = live.hub().latest(hand, target)
        if not found:
            raise HTTPException(404, "No frame yet.")
        jpeg, meta = found
        kind = "image/png" if jpeg[:4] == b"\x89PNG" else "image/jpeg"
        headers = {
            "Cache-Control": "no-store",
            "X-Live-Seq": str(meta["seq"]),
            "X-Live-Url": quote(meta.get("url") or "", safe=":/?&=#%"),
            "X-Live-Title": quote(meta.get("title") or ""),
            "X-Live-Width": str(meta.get("width") or 0),
            "X-Live-Height": str(meta.get("height") or 0),
        }
        return Response(jpeg, media_type=kind, headers=headers)

    @r.get("/live/{hand}/{target}/control")
    async def control(hand: str, target: str, request: Request):
        guard(request)
        _enabled()
        return live.takeover().state(_hand(hand), target).view()

    @r.post("/live/{hand}/{target}/control/{verb}")
    async def change(hand: str, target: str, verb: str, request: Request):
        guard(request)
        _enabled()
        takeover = live.takeover()
        if verb == "take":
            return takeover.take(_hand(hand), target)
        if verb == "release":
            return takeover.release(_hand(hand), target)
        raise HTTPException(404, "Use take or release.")

    @r.post("/live/{hand}/{target}/input/{action}")
    async def owner_input(hand: str, target: str, action: str, request: Request):
        guard(request)
        _enabled()
        if action not in INPUTS:
            raise HTTPException(404, "Use click, type, key or scroll.")
        if live.takeover().state(_hand(hand), target).holder != "human":
            raise HTTPException(409, "Take control first.")
        handler = input_handlers.get(hand)
        if handler is None:
            raise HTTPException(409, "This hand takes input on the PC itself.")
        try:
            args = await request.json()
        except ValueError:
            args = {}
        try:
            return await handler(target, action, args if isinstance(args, dict) else {})
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(422, str(exc)[:300]) from None

    return r


console_router = build("/api/harness", lambda request: None)  # ConsoleBoundary checks the console token.
shell_router = build("/api/panini/harness", lambda request: principal(request, controller=True))

ingest_router = APIRouter(prefix="/api/live/ingest")


def _source(token: str, request: Request):
    if (request.client.host if request.client else "") not in LOOPBACK:
        raise HTTPException(403, "Frames are accepted from this computer only.")
    key = live.hub().resolve(token)
    if key is None:
        raise HTTPException(403, "Unknown live session.")
    return key


@ingest_router.get("/{token}/watch")
async def watch(token: str, request: Request):
    """Sources poll this and only stream while someone is watching (or an effect awaits approval)."""
    hand, target = _source(token, request)
    return {"watched": live.enabled() and live.hub().watched(hand, target)}


@ingest_router.post("/{token}")
async def ingest(token: str, request: Request):
    hand, target = _source(token, request)
    body = await request.body()
    if not body or len(body) > MAX_FRAME:
        raise HTTPException(413, "Frames must be 1 byte to 2 MB.")
    headers = request.headers
    meta = {
        "url": unquote(headers.get("x-live-url", ""))[:2048],
        "title": unquote(headers.get("x-live-title", ""))[:300],
        "width": int(headers.get("x-live-width", "0") or 0),
        "height": int(headers.get("x-live-height", "0") or 0),
    }
    seq = live.hub().publish(headers.get("x-run-id"), hand, target, body, meta)
    return {"seq": seq}
