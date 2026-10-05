"""Authenticated transport for the independent PANINI coordinator."""

import asyncio
import json
import os

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from .. import local_tts, stt_service
from ..panini.contracts import (
    ConfirmationDecision,
    Control,
    Fault,
    Feedback,
    MediaClose,
    MediaOffer,
    ObserverRequest,
    Register,
    Renew,
    SelectTarget,
    Stop,
    TicketExchange,
    Turn,
)
from .speech import Base64Audio
from .speech import encoded as transcribe_encoded

router = APIRouter(prefix="/api/panini")


def owner(request):
    coordinator = request.app.state.panini
    if not coordinator:
        raise Fault("NOT_FOUND", "PANINI is disabled in this profile.", 404)
    return coordinator


def bearer(request):
    value = request.headers.get("authorization", "")
    return value[7:] if value.startswith("Bearer ") else ""


def cookie_name(c):
    return f"panini_observer_{c.settings.instance_id.replace('-', '')}"


def principal(request, *, controller=False):
    c = owner(request)
    token = bearer(request) or request.cookies.get(cookie_name(c), "")
    p = c.auth.resolve(token, request.headers.get("x-panini-instance"))
    if controller:
        c.controller_role(p)
        if request.headers.get("x-panini-instance") != c.settings.instance_id:
            raise Fault("INSTANCE_REQUIRED", "Runtime identity is required.", 401)
    elif p.role not in {"controller", "observer"}:
        raise Fault("FORBIDDEN", "This credential cannot read conversations.", 403)
    return c, p


def session(request, session_id, *, mutation=False, check_lease=True):
    c, p = principal(request, controller=mutation)
    s = c.get(session_id, p)
    epoch = -1
    if mutation:
        try:
            epoch = int(request.headers.get("x-panini-lease-epoch", "-1"))
        except ValueError:
            raise Fault("LEASE_REQUIRED", "A valid controller lease is required.", 409) from None
        if check_lease:
            c.authorize(s, p, epoch)
    return c, p, s, epoch


@router.get("/health")
async def health(request: Request):
    c = owner(request)
    return {
        "status": "ok",
        "project_id": "jev-rev-panini",
        "profile_id": c.settings.profile_id,
        "instance_id": c.settings.instance_id,
        "release_id": c.settings.release_id,
        "protocol_major": 1,
        "desktop_enabled": c.settings.desktop_enabled,
        "code_enabled": c.settings.code_enabled,
    }


@router.post("/clients")
async def register(payload: Register, request: Request):
    c = owner(request)
    c.auth.bootstrap(bearer(request))
    if payload.profile_id != c.settings.profile_id:
        raise Fault("PROFILE_MISMATCH", "This runtime belongs to another PANINI profile.", 403)
    token, p = c.auth.issue("controller", ttl=86400)
    return {
        "client_id": p.client_id,
        "token": token,
        "instance_id": c.settings.instance_id,
        "sessions": [c.snapshot(s) for s in c.sessions.values() if s.state != "closed"],
    }


@router.post("/sessions")
async def create(payload: Control, request: Request):
    c, p = principal(request, controller=True)
    return await c.create_session(p, payload.command_id)


@router.post("/sessions/{session_id}/lease/acquire")
async def acquire(session_id: str, payload: Control, request: Request):
    c, p = principal(request, controller=True)
    s = c.get(session_id)
    return await c.control(
        s, p, -1, payload.command_id, "acquire", {}, lambda: c.acquire(s, p), needs_lease=False
    )


@router.post("/sessions/{session_id}/lease/renew")
async def renew(session_id: str, payload: Renew, request: Request):
    c, p, s, epoch = session(request, session_id, mutation=True, check_lease=False)
    return await c.control(
        s,
        p,
        epoch,
        payload.command_id,
        "renew",
        payload.model_dump(mode="json"),
        lambda: c.renew(s, p, epoch, payload.renderer_revision),
    )


@router.post("/sessions/{session_id}/lease/release")
async def release(session_id: str, payload: Control, request: Request):
    c, p, s, epoch = session(request, session_id, mutation=True, check_lease=False)
    return await c.control(s, p, epoch, payload.command_id, "release", {}, lambda: c.release_lease(s))


@router.get("/sessions/{session_id}")
async def snapshot(session_id: str, request: Request):
    c, _, s, _ = session(request, session_id)
    return c.snapshot(s)


@router.get("/sessions/{session_id}/evidence/{evidence_id}")
async def evidence(session_id: str, evidence_id: str, request: Request):
    _, _, s, _ = session(request, session_id)
    if evidence_id not in s.evidence:
        raise Fault("EVIDENCE_EXPIRED", "This verification record is no longer retained.", 404)
    return s.evidence[evidence_id]


@router.get("/capabilities")
async def capabilities(request: Request):
    c, _ = principal(request)
    return {"capabilities": await asyncio.gather(*(a.availability() for a in c.adapters.values()))}


@router.get("/targets")
async def targets(request: Request, capability: str):
    c, _ = principal(request, controller=True)
    if capability not in {"browser", "desktop", "code"}:
        raise Fault("INVALID_CAPABILITY", "Choose Browser, Desktop or Code.", 422)
    return {"targets": await c.adapters[capability].targets()}


@router.post("/sessions/{session_id}/targets")
async def select_target(session_id: str, payload: SelectTarget, request: Request):
    c, p, s, epoch = session(request, session_id, mutation=True, check_lease=False)
    return await c.control(
        s,
        p,
        epoch,
        payload.command_id,
        "target",
        payload.model_dump(mode="json"),
        lambda: c.select_target(s, p, epoch, payload),
    )


@router.get("/sessions/{session_id}/desktop/focus-target")
async def desktop_focus_target(session_id: str, request: Request):
    _, _, s, _ = session(request, session_id, mutation=True)
    if not s.active or s.active.capability != "desktop" or not s.active.target:
        raise Fault("TASK_CHANGED", "No desktop task is requesting focus.")
    return {key: s.active.target[key] for key in ("hwnd", "pid", "created")}


@router.post("/sessions/{session_id}/commands", status_code=202)
async def command(session_id: str, payload: Turn, request: Request):
    c, p, s, epoch = session(request, session_id, mutation=True)
    return await c.submit(s, p, epoch, payload)


@router.get("/sessions/{session_id}/stt/status")
async def dictation_status(session_id: str, request: Request):
    session(request, session_id)
    return stt_service.get_status()


@router.post("/sessions/{session_id}/stt/transcribe")
async def dictation(session_id: str, payload: Base64Audio, request: Request):
    session(request, session_id, mutation=True)
    return await transcribe_encoded(payload, request)


class LocalSpeech(BaseModel):
    text: str = Field(min_length=1, max_length=local_tts.MAX_TEXT)


@router.post("/sessions/{session_id}/tts/synthesize")
async def local_speech(session_id: str, payload: LocalSpeech, request: Request):
    session(request, session_id, mutation=True)
    if os.getenv("PANINI_LOCAL_MODE", "off").strip().lower() not in {"private", "fallback"}:
        raise Fault("LOCAL_SPEECH_OFF", "Local speech is available in private or fallback mode.", 503)
    if not local_tts.readiness()["ready"]:
        raise Fault("LOCAL_SPEECH_UNAVAILABLE", "Windows local speech is unavailable.", 503)
    try:
        return await request.app.state.speech_work.run(lambda: local_tts.synthesize(payload.text))
    except (ValueError, RuntimeError) as exc:
        raise Fault("LOCAL_SPEECH_FAILED", str(exc)[:200], 502) from exc


@router.post("/sessions/{session_id}/tts/handoff")
async def handoff_speech(session_id: str, request: Request):
    session(request, session_id, mutation=True)
    from .. import v2flags

    if not v2flags.enabled("PANINI_PRESENCE"):
        raise Fault("HANDOFF_SPEECH_OFF", "Continuity is off.", 503)
    if not local_tts.readiness()["ready"]:
        raise Fault("LOCAL_SPEECH_UNAVAILABLE", "Windows local speech is unavailable.", 503)
    try:
        return await request.app.state.speech_work.run(
            lambda: local_tts.synthesize("Continuing from your phone.")
        )
    except (ValueError, RuntimeError) as exc:
        raise Fault("LOCAL_SPEECH_FAILED", str(exc)[:200], 502) from exc


@router.get("/sessions/{session_id}/commands/{command_id}")
async def receipt(session_id: str, command_id: str, request: Request):
    c, p = principal(request, controller=True)
    s = c.get(session_id)
    entry = s.commands.get((p.client_id, command_id))
    if not entry:
        raise Fault("COMMAND_NOT_FOUND", "No accepted command has this ID.", 404)
    return entry["receipt"]


@router.post("/sessions/{session_id}/feedback")
async def feedback(session_id: str, payload: Feedback, request: Request):
    c, p, s, epoch = session(request, session_id, mutation=True, check_lease=False)
    return c.feedback(s, p, epoch, payload)


@router.post("/sessions/{session_id}/stop")
async def stop(session_id: str, payload: Stop, request: Request):
    c, p, s, epoch = session(request, session_id, mutation=True, check_lease=False)
    return await c.control(
        s,
        p,
        epoch,
        payload.command_id,
        "stop",
        payload.model_dump(mode="json"),
        lambda: c.stop(s, payload.expected_task_id),
    )


@router.post("/sessions/{session_id}/end")
async def end(session_id: str, payload: Control, request: Request):
    c, p, s, epoch = session(request, session_id, mutation=True, check_lease=False)
    return await c.control(s, p, epoch, payload.command_id, "end", {}, lambda: c.end(s))


@router.get("/sessions/{session_id}/events")
async def events(session_id: str, request: Request, after: int = 0):
    c, p, s, _ = session(request, session_id)
    token = bearer(request) or request.cookies.get(cookie_name(c), "")
    sub, backlog = s.hub.subscribe(after)

    async def stream():
        try:
            for event in backlog:
                yield json.dumps(s.hub.project(event, p), ensure_ascii=False) + "\n"
            while True:
                try:
                    c.auth.resolve(token)
                except Fault:
                    break
                if sub.overflow:
                    yield '{"type":"transport.resync"}\n'
                    break
                if s.state == "closed" and sub.queue.empty():
                    break
                try:
                    event, size = await asyncio.wait_for(sub.queue.get(), 10)
                    sub.bytes -= size
                    yield json.dumps(s.hub.project(event, p), ensure_ascii=False) + "\n"
                except TimeoutError:
                    yield '{"type":"transport.heartbeat"}\n'
        finally:
            s.hub.unsubscribe(sub)

    return StreamingResponse(
        stream(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.post("/sessions/{session_id}/observers")
async def observer_ticket(session_id: str, payload: ObserverRequest, request: Request):
    c, _, s, _ = session(request, session_id, mutation=True)
    active = [
        p
        for p in c.auth.tokens.values()
        if p.role == "observer" and p.session_id == session_id and p.expires > c.clock()
    ]
    if len(active) >= 4:
        raise Fault("OBSERVER_LIMIT", "Four activity viewers are already connected.", 429)
    return {"ticket": c.auth.ticket("observer", s.session_id, payload.share_captions), "expires_in": 30}


@router.post("/observer/exchange")
async def observer_exchange(payload: TicketExchange, request: Request):
    c = owner(request)
    # ApiBoundary and the gateway enforce same-origin. No bearer is needed for a one-use ticket.
    if not request.headers.get("origin"):
        raise Fault("ORIGIN_REQUIRED", "Open the dashboard through PANINI.", 403)
    token, p = c.auth.exchange(payload.ticket, "observer")
    s = c.get(p.session_id)
    if s.state == "closed":
        c.auth.revoke(p.client_id)
        raise Fault("SESSION_ENDED", "This conversation has ended.", 410)
    active = [v for v in c.auth.tokens.values() if v.role == "observer" and v.session_id == s.session_id]
    if len(active) > 4:
        c.auth.revoke(p.client_id)
        raise Fault("OBSERVER_LIMIT", "Four activity viewers are already connected.", 429)
    response = JSONResponse(
        {"session_id": s.session_id, "instance_id": c.settings.instance_id, "observer_id": p.client_id}
    )
    response.set_cookie(
        cookie_name(c), token, httponly=True, samesite="strict", path="/api/panini", max_age=3600
    )
    return response


@router.delete("/sessions/{session_id}/observers/{observer_id}")
async def revoke_observer(session_id: str, observer_id: str, payload: Control, request: Request):
    c, _, _, _ = session(request, session_id, mutation=True)
    for p in c.auth.tokens.values():
        if p.client_id == observer_id and (p.role != "observer" or p.session_id != session_id):
            raise Fault("FORBIDDEN", "This viewer belongs to another session.", 403)
    c.auth.revoke(observer_id)
    return {"status": "revoked"}


@router.get("/sessions/{session_id}/confirmations/{confirmation_id}")
async def confirmation(session_id: str, confirmation_id: str, request: Request):
    c, _, s, _ = session(request, session_id, mutation=True)
    return {k: v for k, v in c.confirmation(s, confirmation_id).items() if k != "future"}


@router.post("/sessions/{session_id}/confirmations/{confirmation_id}")
async def decide(session_id: str, confirmation_id: str, payload: ConfirmationDecision, request: Request):
    c, _, s, _ = session(request, session_id, mutation=True)
    return c.decide(s, confirmation_id, payload)


@router.post("/sessions/{session_id}/media")
async def connect_media(session_id: str, payload: MediaOffer, request: Request):
    c, p, s, epoch = session(request, session_id, mutation=True)
    if not c.voice_factory:
        raise Fault(
            "VOICE_UNAVAILABLE", "Live voice is unavailable. Typed instructions remain available.", 503
        )
    async with s.media_lock:
        c.authorize(s, p, epoch)
        if s.media or payload.media_generation <= s.media_generation:
            raise Fault("MEDIA_BUSY", "Close the existing microphone connection before reconnecting.")
        from ..panini.voice import voice_profile

        profile = voice_profile(c, payload.persona_id)
        s.voice_persona_id = profile["id"]
        s.media_generation = payload.media_generation
        media = c.voice_factory(c, s, p, epoch)
        s.media = media
        try:
            answer = await media.start(payload.sdp)
            c.authorize(s, p, epoch)
            s.hub.publish(
                "media.connected",
                {
                    "generation": s.media_generation,
                    "persona": {key: profile[key] for key in ("id", "name", "color")},
                },
            )
            return {"sdp": answer, "media_generation": s.media_generation}
        except BaseException as exc:
            s.media = None
            await media.close()
            if isinstance(exc, asyncio.CancelledError):
                raise
            message = (
                str(exc)
                if isinstance(exc, RuntimeError)
                else "The live voice provider could not connect. Check model access and retry."
            )
            raise Fault("VOICE_UNAVAILABLE", message[:500], 503) from None


@router.delete("/sessions/{session_id}/media")
async def disconnect_media(session_id: str, payload: MediaClose, request: Request):
    c, _, s, _ = session(request, session_id, mutation=True)
    if payload.media_generation != s.media_generation:
        raise Fault("MEDIA_CHANGED", "The microphone connection has changed.")
    await c.stop(s)
    await c.close_media(s)
    return {"status": "closed"}


@router.get("/sessions/{session_id}/voice-personas")
async def voice_personas(session_id: str, request: Request):
    c, _, _, _ = session(request, session_id, mutation=True)
    from .. import v2flags

    service = getattr(c.adapters.get("agent"), "harness", None)
    if not v2flags.enabled("PANINI_PERSONAS") or service is None:
        return {"personas": []}
    return {"personas": [service.personas.presentation(row["id"]) for row in service.personas.list()]}


@router.post("/extensions/pairing-code")
async def extension_pairing_code(payload: Control, request: Request):
    """A short one-time code the user types into JevRev Claw. Controller only."""
    principal(request, controller=True)
    return request.app.state.pairing.create_code()
