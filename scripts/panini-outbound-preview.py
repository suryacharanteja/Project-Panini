"""Loopback-only outbound UI preview. Isolated vault/database and fake SIP/voice; no external calls."""

import asyncio
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
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles
    from panini_cli.agent.model import ScriptedModel

    from backend.app import create_app
    from backend.harness.runtime import Harness
    from backend.telephony.outbound import OutboundCalls

    h = Harness(model_factory=lambda: ScriptedModel([{"text": "Preview"}]))
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

        async def speak(self, text, **kwargs):
            pass

        async def send(self, kind, **kwargs):
            observed.append({"action": kind})

        async def close(self):
            pass

    h.outbound = OutboundCalls(
        h,
        provider=provider,
        connection_factory=Connection,
        clock=lambda: datetime(2026, 10, 4, 16, tzinfo=UTC).timestamp(),
    )
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

    uvicorn.run(app, host="127.0.0.1", port=3236, access_log=False)


if __name__ == "__main__":
    main()
