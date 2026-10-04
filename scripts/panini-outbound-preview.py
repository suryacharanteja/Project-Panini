"""Loopback-only outbound UI preview. Isolated vault/database and fake SIP/voice; no external calls."""

import asyncio
import json
import mimetypes
import os
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [
    str(ROOT),
    str(ROOT / "panini-core/capabilities/cli/src"),
    str(ROOT / "panini-core/voice/live-spoken-conversation"),
]
os.environ["PANINI_DATA_DIR"] = tempfile.mkdtemp(prefix="panini-outbound-preview-")
for key in (
    "PANINI_CLI_ENABLED",
    "JEV_REV_DESKTOP_ENABLED",
    "PANINI_CAPABILITY_ROUTER_ENABLED",
    "PANINI_PERSONAS",
    "PANINI_SPACES",
    "PANINI_PHONE_LINE",
    "PANINI_REDTEAM_ENFORCE",
    "PANINI_JEV_GATES",
):
    os.environ[key] = "false"
os.environ["PANINI_OUTBOUND_CALLS"] = "true"
os.environ["PANINI_CHANNELS"] = ""
os.environ["AI_GATEWAY_API_KEY"] = ""


def main():
    mimetypes.add_type("text/javascript", ".mjs")
    import uvicorn
    from fastapi import HTTPException, Request
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles
    from panini_cli.agent.model import ScriptedModel

    from backend.app import create_app
    from backend.harness.runtime import Harness
    from backend.telephony.outbound import OutboundCalls

    quote = "Your appointment is confirmed for Thursday afternoon next week."
    proposal = {
        "suggested_status": "achieved",
        "findings": [
            {
                "kind": "outcome",
                "text": "Callee reports an afternoon appointment next week.",
                "outcome_index": 0,
                "commitment_permission": None,
                "citations": [{"utterance_id": 1, "quote": quote}],
            }
        ],
        "follow_ups": ["review_calendar_entry"],
    }
    h = Harness(model_factory=lambda: ScriptedModel([{"text": json.dumps(proposal)}]))
    observed = []

    async def provider(action, session_id=None, body=None):
        observed.append({"action": action, "session": session_id})
        if action == "refer":
            await h.outbound.event(h.outbound.active, {"type": "call.closed"})
        return {"session": {"id": "live_preview_" + str(len(observed))}} if action == "create" else None

    class Connection:
        def __init__(self, _key, on_event, on_request):
            self.on_event = on_event
            self.finalized = asyncio.Event()

        async def attach(self, session):
            await self.on_event({"type": "transport.ringing"})
            await self.on_event({"type": "transport.answered"})
            await self.on_event(
                {"type": "transcript", "role": "assistant", "delta": h.outbound.active["brief"].disclosure()}
            )

            await self.on_event({"type": "transcript", "role": "user", "delta": quote})

        async def speak(self, text, **kwargs):
            pass

        async def send(self, kind, **kwargs):
            observed.append({"action": kind})

        async def close(self):
            pass

    now = [datetime(2026, 10, 4, 16, tzinfo=UTC).timestamp()]
    h.outbound = OutboundCalls(
        h,
        provider=provider,
        connection_factory=Connection,
        clock=lambda: now[0],
    )
    h.interrupts.clock = lambda: now[0]
    app = create_app(console_token="panini-outbound-preview-only", harness_factory=lambda: h)
    public = ROOT / "frontend/public/admin"
    app.mount("/phone", StaticFiles(directory=ROOT / "panini-core/apps/phone"))
    for directory in ("assets", "brand"):
        app.mount("/admin/" + directory, StaticFiles(directory=public / directory))

    @app.get("/build/phone")
    async def shell():
        return FileResponse(public / "index.html")

    @app.get("/preview/state")
    async def state():
        return {"observed": observed, "status": h.outbound.status()}

    @app.post("/preview/advance")
    async def advance(request: Request):
        if request.headers.get("x-panini-console") != "panini-outbound-preview-only":
            raise HTTPException(401, "Preview control token required.")
        payload = await request.json()
        at = payload.get("at")
        if type(at) not in {int, float} or not now[0] <= at <= now[0] + 7 * 86400:
            raise HTTPException(422, "Advance the isolated preview clock by at most seven days.")
        now[0] = at
        await h.outbound.tick()
        if h.outbound.work:
            await h.outbound.work
        return h.outbound.status()

    uvicorn.run(app, host="127.0.0.1", port=3236, access_log=False)


if __name__ == "__main__":
    main()
