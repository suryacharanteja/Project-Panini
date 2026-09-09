"""FastAPI server — the browser-facing surface.

The event bus already fans out everything the UI needs (segments, approvals,
provenance, errors), so this layer is deliberately thin: it moves events to the
browser and turns HTTP requests into turns. No decisions are made here.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from panini.app import Application
from panini.core.turn import Modality, Turn, TurnInput
from panini.errors import PaniniError, ProviderUnavailable

log = logging.getLogger("panini.api")

STATIC_DIR = Path(__file__).parent / "static"


class ChatRequest(BaseModel):
    text: str
    session_id: str = "web"


class ApprovalResponse(BaseModel):
    invocation_id: str
    token: str
    granted: bool


class FactEdit(BaseModel):
    statement: str


def create_app(application: Application) -> FastAPI:
    api = FastAPI(title="Panini", docs_url=None, redoc_url=None)

    # A single in-flight turn per session. Concurrent turns would make the
    # audit trail ambiguous about which approval authorised which action.
    turn_lock = asyncio.Lock()

    # ── events ───────────────────────────────────────────────────────────────

    @api.get("/events")
    async def events() -> StreamingResponse:
        async def stream() -> AsyncIterator[str]:
            # Replay lets a reconnecting browser recover mid-turn state rather
            # than showing a stale view with no way back.
            yield f": boot {application.bus.boot_id}\n\n"
            try:
                async for event in application.bus.subscribe(replay=True):
                    payload = json.dumps({"type": event.type, "seq": event.seq,
                                          **event.payload})
                    yield f"event: {event.type}\ndata: {payload}\n\n"
            except asyncio.CancelledError:
                raise

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ── chat ─────────────────────────────────────────────────────────────────

    @api.post("/chat")
    async def chat(request: ChatRequest) -> dict[str, Any]:
        try:
            orchestrator = application.require_orchestrator()
        except ProviderUnavailable as exc:
            raise HTTPException(503, str(exc)) from exc

        if turn_lock.locked():
            raise HTTPException(409, "a turn is already in progress")

        async with turn_lock:
            turn = Turn(
                session_id=request.session_id,
                input=TurnInput(request.text, Modality.TEXT),
            )
            # Memory is assembled before generation: a recalled fact of
            # untrusted origin taints the turn, and that must be set before the
            # first tool call is considered (DD-121 M-2).
            assembled = await application.context.assemble(
                application.system_prompt, turn
            )
            try:
                await orchestrator.run(turn, assembled.messages)
            except PaniniError as exc:
                application.bus.publish("error", {"turn_id": turn.id, "error": str(exc)})
            finally:
                await application.history.append_turn(turn)

            return {
                "turn_id": turn.id,
                "state": str(turn.state),
                "tainted": turn.tainted,
                "taint_sources": turn.taint_sources,
                "trimmed": assembled.turns_dropped,
            }

    # ── approvals ────────────────────────────────────────────────────────────

    @api.post("/approve")
    async def approve(response: ApprovalResponse) -> dict[str, bool]:
        # A rejected token is not an error to retry — it means the reply was
        # forged, replayed, or belongs to a turn that has ended.
        accepted = application.approvals.resolve(
            response.invocation_id, response.token, response.granted
        )
        if not accepted:
            raise HTTPException(409, "approval is no longer valid")
        return {"ok": True}

    # ── introspection ────────────────────────────────────────────────────────

    @api.get("/health")
    async def health() -> dict[str, Any]:
        return application.health()

    @api.get("/audit")
    async def audit(limit: int = 50) -> list[dict[str, Any]]:
        entries = await application.audit.query(limit=limit)
        return [
            {
                "ts": e.ts_begin,
                "tool": e.tool_name,
                "args": e.args_redacted,
                "outcome": e.outcome,
                "tainted": e.tainted,
                "taint_sources": e.taint_sources,
                "escalated": e.was_escalated,
                "declared_tier": e.declared_tier,
                "effective_tier": e.effective_tier,
                "is_egress": e.is_egress,
                "destination": e.destination,
                "approval_granted": e.approval_granted,
                "error": e.error,
            }
            for e in entries
        ]

    @api.get("/memory")
    async def memory() -> dict[str, Any]:
        def render(f: Any) -> dict[str, Any]:
            return {
                "id": f.id,
                "subject": f.subject,
                "statement": f.statement,
                "origin_tainted": f.origin_tainted,
                "taints_on_use": f.taints_on_use,
                "edited": f.edited_by_user,
                "use_count": f.use_count,
            }

        return {
            "confirmed": [render(f) for f in await application.facts.load_confirmed()],
            "unconfirmed": [render(f) for f in await application.facts.load_unconfirmed()],
        }

    @api.post("/memory/{fact_id}/confirm")
    async def confirm_fact(fact_id: str) -> dict[str, bool]:
        await application.facts.confirm(fact_id)
        return {"ok": True}

    @api.post("/memory/{fact_id}/edit")
    async def edit_fact(fact_id: str, edit: FactEdit) -> dict[str, bool]:
        await application.facts.edit(fact_id, edit.statement)
        return {"ok": True}

    @api.delete("/memory/{fact_id}")
    async def delete_fact(fact_id: str) -> dict[str, bool]:
        await application.facts.delete(fact_id)
        return {"ok": True}

    # ── static ───────────────────────────────────────────────────────────────

    @api.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    if STATIC_DIR.exists():
        api.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @api.on_event("shutdown")
    async def shutdown() -> None:
        with contextlib.suppress(Exception):
            await application.stop()

    return api
