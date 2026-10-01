"""Local session capabilities. Browser clients never receive the OpenAI project key."""

import asyncio
import json
import secrets
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from .. import config
from .browser import BrowserRunRequest, prepare_run

router = APIRouter(prefix="/api/voice")


class Start(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sdp: str = Field(min_length=20, max_length=100000)
    mode: Literal["extension", "chromium"]
    target_id: str = Field(default="", max_length=100)


class Control(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(min_length=20, max_length=100)


class Events(Control):
    after: int = Field(default=0, ge=0)


class ConsoleStart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["extension", "chromium"]
    target_id: str = Field(default="", max_length=100)


class SpokenTurn(Control):
    text: str = Field(min_length=1, max_length=5000)
    turn_id: str = Field(min_length=1, max_length=100)


def owned(request, token):
    session = request.app.state.voice_session
    if not session or not secrets.compare_digest(session.token, token):
        raise HTTPException(404, "Voice session has ended")
    return session


@router.get("/status")
async def status(request: Request):
    session = request.app.state.voice_session
    return {
        "configured": config.configured("OPENAI_API_KEY"),
        "active": bool(session and not session.closed),
        "provider": "OpenAI Live",
        "state": session.conversation.state if session else "idle",
        "diagnostics": getattr(session, "diagnostics", {})
        if session
        else getattr(request.app.state, "voice_last_diagnostics", {}),
    }


@router.post("/session")
async def start(payload: Start, request: Request):
    from ..voice_service import VoiceSession

    if not config.configured("OPENAI_API_KEY"):
        raise HTTPException(503, "Configure OPENAI_API_KEY to start a live conversation")
    if request.app.state.voice_session is not None:
        raise HTTPException(409, "A live conversation is already open. End it before starting another.")
    target = {"mode": payload.mode, "target_id": payload.target_id}
    prepare_run(BrowserRunRequest(instruction="Validate selected browser", **target), request)
    session = VoiceSession(request.app, target)
    request.app.state.voice_session = session  # reserve before awaiting the provider
    try:
        answer = await session.start(payload.sdp)
        return {"session_id": session.token, "sdp": answer, "model": session.connection.model}
    except BaseException as exc:
        await session.close()
        if isinstance(exc, asyncio.CancelledError):
            raise
        raise HTTPException(
            502, "Could not connect live voice. Check OpenAI access and your connection."
        ) from None


@router.post("/events")
async def events(payload: Events, request: Request):
    session = owned(request, payload.session_id)
    if session.stream_connected:
        raise HTTPException(409, "This voice session already has an event listener")
    session.stream_connected = True

    async def stream():
        cursor = payload.after
        try:
            while True:
                session.changed.clear()
                batch = [event for event in session.events if event["sequence"] > cursor]
                for event in batch:
                    cursor = event["sequence"]
                    yield json.dumps(event).encode() + b"\n"
                if session.closed:
                    break
                try:
                    await asyncio.wait_for(session.changed.wait(), 10)
                except TimeoutError:
                    yield b'{"type":"heartbeat"}\n'
        finally:
            # SSE lifetime owns the session, even if the tab closes without sending /end.
            await asyncio.shield(session.close())

    return StreamingResponse(stream(), media_type="application/x-ndjson", headers={"X-Accel-Buffering": "no"})


@router.post("/console")
async def console(payload: ConsoleStart, request: Request):
    from panini.console_connection import ConsoleConnection

    from ..voice_service import VoiceSession

    if not config.configured("OPENAI_API_KEY"):
        raise HTTPException(503, "Configure OPENAI_API_KEY for conversation planning")
    if request.app.state.voice_session:
        raise HTTPException(409, "A live conversation is already open")
    target = payload.model_dump()
    prepare_run(BrowserRunRequest(instruction="Validate selected browser", **target), request)
    session = VoiceSession(request.app, target, connection_factory=ConsoleConnection)
    request.app.state.voice_session = session
    await session.start("")
    return {"session_id": session.token}


@router.post("/turn")
async def turn(payload: SpokenTurn, request: Request):
    from panini.console_connection import ConsoleConnection

    session = owned(request, payload.session_id)
    if not isinstance(session.connection, ConsoleConnection):
        raise HTTPException(409, "This session receives instructions from its live audio connection")
    await session.request(payload.text, payload.turn_id)
    return {"status": "accepted"}


@router.post("/stop")
async def stop(payload: Control, request: Request):
    session = owned(request, payload.session_id)
    await session.conversation.stop()
    await session.publish({"type": "message", "message": "Browser work stopped. I'm still listening."})
    return {"status": "stopped"}


@router.post("/end")
async def end(payload: Control, request: Request):
    session = owned(request, payload.session_id)
    await session.close()
    return {"status": "closed"}
