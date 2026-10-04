"""Outbound review and controls share the existing authenticated console, avatar and fabric boundaries."""

from fastapi import HTTPException, Request
from pydantic import ValidationError


def add_outbound(router, guard, *, configurable=False):
    def service(request):
        guard(request)
        h = request.app.state.harness
        if h is None:
            raise HTTPException(404, "The harness is disabled.")
        return h.outbound

    @router.get("/outbound")
    async def status(request: Request):
        return service(request).status()

    if configurable:

        @router.post("/outbound/settings")
        async def settings(request: Request):
            calls = service(request)
            payload = await request.json()
            try:
                if not isinstance(payload, dict) or set(payload) - {
                    "config",
                    "username",
                    "password",
                    "checklist",
                }:
                    raise ValueError("Unknown configuration fields.")
                return calls.configure(**payload)
            except (ValueError, TypeError, KeyError):
                raise HTTPException(
                    422,
                    "Check the public SIP endpoint, E.164 numbers, credentials and complete owner checklist.",
                ) from None

    @router.post("/outbound/briefs")
    async def draft(request: Request):
        calls = service(request)
        try:
            return calls.draft(
                await request.json(), surface="phone_app" if router.prefix == "/api/remote" else "avatar"
            )
        except (ValueError, ValidationError):
            raise HTTPException(
                422, "Check all call brief fields, destination and permitted commitments."
            ) from None

    @router.put("/outbound/briefs/{identifier}")
    async def edit(identifier: str, request: Request):
        calls = service(request)
        try:
            return calls.draft(await request.json(), identifier=identifier)
        except (ValueError, ValidationError):
            raise HTTPException(
                422, "Check all call brief fields, destination and permitted commitments."
            ) from None

    @router.post("/outbound/block")
    async def block(request: Request):
        calls = service(request)
        try:
            calls.block((await request.json())["number"])
        except (ValueError, KeyError, TypeError):
            raise HTTPException(422, "Provide a valid E.164 number.") from None
        if calls.active and calls.store.one(
            "SELECT number FROM outbound_do_not_call WHERE number=?", (calls.active["brief"].number,)
        ):
            await calls.end(reason="Owner blocked this number.")
        return {"status": "blocked"}

    @router.post("/outbound/{identifier}/{action}")
    async def control(identifier: str, action: str, request: Request):
        calls = service(request)
        payload = await request.json() if action == "note" else {}
        try:
            await calls.control(identifier, action, payload.get("text"))
        except (ValueError, TypeError, AttributeError):
            raise HTTPException(422, "Provide a valid call control or short note.") from None
        return {"status": "accepted"}
