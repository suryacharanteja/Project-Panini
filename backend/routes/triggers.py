"""Owner-facing V2-25 trigger and responsibility controls."""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Hook(Strict):
    name: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")


class OsEvent(Strict):
    kind: str = Field(pattern=r"^(locked|unlocked|resumed|suspended|on_ac|on_battery|online|offline)$")


class Binding(Strict):
    source: str = Field(pattern=r"^(email|calendar)$")
    server: str = Field(min_length=1, max_length=100)
    tool: str = Field(min_length=1, max_length=200)
    arguments: dict = Field(default_factory=dict)


def add_triggers(router: APIRouter, guard, *, shell=False):
    def bus(request):
        guard(request)
        return request.app.state.harness.triggers

    @router.get("/triggers")
    async def status(request: Request):
        return bus(request).status()

    @router.post("/triggers")
    async def propose(rule: dict, request: Request):
        try:
            return bus(request).propose(rule)
        except (KeyError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from None

    @router.post("/triggers/{identifier}/pause")
    async def pause(identifier: str, request: Request):
        try:
            return bus(request).pause(identifier)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from None

    @router.post("/triggers/{identifier}/resume")
    async def resume(identifier: str, request: Request):
        try:
            return bus(request).resume(identifier)
        except (KeyError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from None

    @router.post("/triggers/{identifier}/retire")
    async def retire(identifier: str, request: Request):
        try:
            return bus(request).retire(identifier)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from None

    @router.post("/triggers/responsibilities")
    async def responsibility(spec: dict, request: Request):
        try:
            return bus(request).responsibilities.propose(spec)
        except (KeyError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from None

    @router.post("/triggers/responsibilities/{identifier}/pause")
    async def pause_responsibility(identifier: str, request: Request):
        try:
            return bus(request).responsibilities.pause(identifier)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from None

    @router.post("/triggers/responsibilities/{identifier}/resume")
    async def resume_responsibility(identifier: str, request: Request):
        try:
            return bus(request).responsibilities.resume(identifier)
        except (KeyError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from None

    @router.post("/triggers/responsibilities/{identifier}/retire")
    async def retire_responsibility(identifier: str, request: Request):
        try:
            return bus(request).responsibilities.retire(identifier)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from None

    @router.post("/triggers/bindings")
    async def binding(payload: Binding, request: Request):
        from ..triggers.sources.mcp_events import bind

        service = bus(request)
        try:
            bind(service, payload.source, payload.server, payload.tool, payload.arguments)
        except (KeyError, ValueError, TypeError) as exc:
            raise HTTPException(422, str(exc)) from None
        return service.status()

    @router.delete("/triggers/bindings/{source}")
    async def remove_binding(source: str, request: Request):
        from ..triggers.sources.mcp_events import unbind

        service = bus(request)
        try:
            unbind(service, source)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        return service.status()

    if shell:

        @router.post("/triggers/os")
        async def os_event(payload: OsEvent, request: Request):
            from ..triggers.sources.os_events import receive

            return await receive(bus(request), payload.kind)
    else:

        @router.post("/triggers/hooks")
        async def new_hook(payload: Hook, request: Request):
            bus(request)
            from ..fabric.runtime import runtime

            try:
                return runtime(request.app).configure_trigger_hook(payload.name)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from None
