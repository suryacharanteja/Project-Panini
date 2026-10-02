"""AG-UI endpoint (docs/panini-enhancements/lld/V2-07-agui-adapter.md §5.2).

Console routes (`/api/agui/...`, behind the console sign-in) and the same routes for the shell
(`/api/panini/agui/...`, shell bearer). Not exposed on the phone listener or a relay.
"""

from ag_ui.encoder import EventEncoder
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from .. import v2flags
from ..agui.input import parse
from .panini import principal

PERSONAS = {"panini"}  # V2-18 adds personas.


class ThreadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    thread_id: str = Field(alias="threadId", min_length=1, max_length=200)
    run_id: str | None = Field(default=None, alias="runId", max_length=200)


def _runs(request: Request, persona: str):
    if not v2flags.enabled("PANINI_V2_AGUI"):
        raise HTTPException(404, "The AG-UI endpoint is off (PANINI_V2_AGUI).")
    if persona not in PERSONAS:
        raise HTTPException(404, "Unknown agent.")
    harness = request.app.state.harness
    if harness is None:
        raise HTTPException(404, "The PANINI harness is disabled.")
    runs = getattr(request.app.state, "agui", None)
    if runs is None or runs.h is not harness:
        from ..agui.runner import AguiRuns

        runs = request.app.state.agui = AguiRuns(harness)
    return runs


def _sse(request: Request, events):
    encoder = EventEncoder(accept=request.headers.get("accept"))

    async def body():
        async for event in events:
            yield encoder.encode(event)

    return StreamingResponse(
        body(),
        media_type=encoder.get_content_type(),
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


def build(prefix, guard):
    r = APIRouter(prefix=prefix)

    @r.post("/agents/{persona}/run")
    async def run(persona: str, request: Request):
        guard(request)
        runs = _runs(request, persona)
        payload = parse(await request.json())
        return _sse(request, runs.stream(payload))

    @r.post("/agents/{persona}/connect")
    async def connect(persona: str, body: ThreadRequest, request: Request):
        guard(request)
        runs = _runs(request, persona)
        return _sse(request, runs.connect(body.thread_id, body.run_id))

    @r.post("/agents/{persona}/stop")
    async def stop(persona: str, body: ThreadRequest, request: Request):
        guard(request)
        return {"stopped": await _runs(request, persona).stop(body.thread_id)}

    return r


console_router = build("/api/agui", lambda request: None)  # ConsoleBoundary checks the console token.
shell_router = build("/api/panini/agui", lambda request: principal(request, controller=True))
