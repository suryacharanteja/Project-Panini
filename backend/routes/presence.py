"""Owner-authenticated continuity resources, shared by the console, native shell and paired fabric."""

from typing import Literal

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field


class PcSignal(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    idle_s: float | int = Field(ge=0, le=604800)
    locked: bool
    suspended: bool
    avatar_visible: bool


class PhoneSignal(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    foreground: bool


class Handoff(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    thread_id: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9_-]+$")
    destination: Literal["pc", "phone", "call"]


class CommandAck(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    seq: int = Field(ge=1)


class AcceptHandoff(CommandAck):
    session_id: str = Field(min_length=1, max_length=200)


def continuity(request):
    h = getattr(request.app.state, "harness", None)
    service = getattr(h, "continuity", None)
    if not service:
        raise HTTPException(404, "Continuity is not available.")
    return service


def add_presence(router, guard, *, phone=False, native=False):
    @router.get("/presence")
    async def status(request: Request):
        guard(request)
        return continuity(request).status()

    if phone:

        @router.post("/presence")
        async def phone_signal(payload: PhoneSignal, request: Request):
            _, device_id = guard(request)
            service = continuity(request)
            return (
                service.presence.signal("phone:" + device_id, payload.model_dump())
                if service.enabled
                else {"enabled": False}
            )
    else:

        @router.post("/presence/pc")
        async def pc_signal(payload: PcSignal, request: Request):
            guard(request)
            service = continuity(request)
            return (
                service.presence.signal("pc", payload.model_dump()) if service.enabled else {"enabled": False}
            )

        @router.put("/presence/settings")
        async def settings(payload: dict, request: Request):
            guard(request)
            try:
                return continuity(request).configure(payload)
            except ValueError as error:
                raise HTTPException(422, str(error)) from None

        @router.post("/presence/urgent-grants")
        async def propose_urgent_grant(request: Request):
            guard(request)
            try:
                return continuity(request).urgent.propose()
            except (ValueError, RuntimeError) as error:
                raise HTTPException(409, str(error)) from None

        @router.delete("/presence/urgent-grants/{grant_id}")
        async def revoke_urgent_grant(grant_id: str, request: Request):
            guard(request)
            try:
                service = continuity(request)
                result = service.urgent.revoke(grant_id)
                active = service.h.outbound.active
                if active and service.h.outbound.get(active["id"])["standing_grant_id"] == grant_id:
                    await service.h.outbound.end(reason="Urgent call grant revoked by the owner.")
                return result
            except KeyError:
                raise HTTPException(404, "Unknown urgent call grant.") from None

        @router.get("/presence/commands")
        async def commands(request: Request):
            guard(request)
            return {"commands": continuity(request).commands()}

        @router.post("/presence/commands/ack")
        async def acknowledge(payload: CommandAck, request: Request):
            guard(request)
            continuity(request).acknowledge(payload.seq)
            return {"status": "acknowledged"}

    @router.post("/presence/handoff")
    async def handoff(payload: Handoff, request: Request):
        guard(request)
        service = continuity(request)
        if not service.enabled:
            raise HTTPException(409, "Continuity is off (PANINI_PRESENCE).")
        try:
            target = payload.thread_id
            if not service.store.one("SELECT id FROM sessions WHERE id=?", (target,)):
                binding = service.store.one(
                    "SELECT thread_id FROM thread_bindings WHERE surface='agui' AND external_id=?", (target,)
                )
                if not binding:
                    raise KeyError("Unknown conversation")
                target = binding["thread_id"]
            service.h.sessions.get(target)
            active_service = getattr(getattr(service.app, "state", None), "phone_line", None)
            active_call = getattr(active_service, "active", None)
            if payload.destination == "pc" and active_call and active_call.thread == target:
                if not active_call.verified or active_call.caller.get("kind") != "owner":
                    raise HTTPException(409, "Verify the owner call before continuing it on the PC.")
                # Finish and drain the source call before publishing the PC command; two surfaces must
                # not race writes to the same thread's transcript.
                await active_service.end(reason="Owner continued the conversation on the PC.")
            service.h.threads.attach("agui", target, target)
            return service.handoff(target, payload.destination, surface="phone_app" if phone else "avatar")
        except KeyError:
            raise HTTPException(404, "The original conversation no longer exists.") from None
        except ValueError as error:
            raise HTTPException(409, str(error)) from None

    @router.get("/presence/threads")
    async def threads(request: Request):
        guard(request)
        return {
            "threads": [
                {"id": row["id"], "title": row["title"], "updated": row["updated"]}
                for row in continuity(request).h.sessions.list(limit=50)
            ]
        }

    @router.get("/presence/threads/{thread_id}")
    async def thread(thread_id: str, request: Request):
        guard(request)
        from ..harness.sessions import transcript

        try:
            current = continuity(request).h.sessions.get(thread_id)
            return {
                "id": current["id"],
                "title": current["title"],
                "summary": current["summary"],
                "transcript": transcript(current["items"], limit=20000),
            }
        except KeyError:
            raise HTTPException(404, "Unknown conversation.") from None

    if native:

        @router.post("/presence/handoff/accept")
        async def accept(payload: AcceptHandoff, request: Request):
            from ..harness.sessions import transcript
            from .panini import session

            coordinator, _, current, _ = session(request, payload.session_id, mutation=True)
            service = continuity(request)
            if not service.enabled:
                raise HTTPException(409, "Continuity is off.")
            command = service.store.one(
                "SELECT * FROM continuity_commands WHERE seq=? AND kind='handoff' AND expires>?",
                (payload.seq, service.clock()),
            )
            if not command:
                raise HTTPException(404, "This handoff expired.")
            import json

            target = json.loads(command["data"])["thread_id"]
            # Do not retarget a running avatar task or an active voice session's private history.
            if current.active or current.media:
                raise HTTPException(
                    409, "Finish the current task and close the voice session before continuing."
                )
            adapter = coordinator.adapters.get("agent")
            if not adapter or not hasattr(adapter, "sessions"):
                raise HTTPException(409, "The avatar agent is unavailable.")
            try:
                restored = service.h.sessions.get(target)
            except KeyError:
                raise HTTPException(404, "The original conversation no longer exists.") from None
            service.store.execute(
                "INSERT INTO thread_bindings(surface,external_id,thread_id,created) VALUES ('avatar',?,?,?) ON CONFLICT(surface,external_id) DO UPDATE SET thread_id=excluded.thread_id,created=excluded.created",
                (payload.session_id, target, service.clock()),
            )
            adapter.sessions[payload.session_id] = target
            return {
                "thread_id": target,
                "title": restored["title"],
                "summary": restored["summary"],
                "transcript": transcript(restored["items"], limit=12000),
            }

    @router.post("/presence/items/{item_id}/seen")
    async def seen(item_id: str, request: Request):
        guard(request)
        service = continuity(request)
        if not service.store.one("SELECT id FROM continuity_items WHERE id=?", (item_id,)):
            raise HTTPException(404, "Unknown continuity item.")
        if item_id.startswith("interrupt:"):
            # Viewing an approval does not resolve it or cancel other surfaces' pending alerts.
            return {"status": "viewed", "decision_required": True}
        if item_id.startswith("notice:"):
            try:
                service.h.notices.mark(item_id.split(":", 1)[1], "read")
            except KeyError:
                pass
        service.resolve(item_id)
        return {"status": "seen"}
