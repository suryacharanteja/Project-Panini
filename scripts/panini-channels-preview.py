"""Loopback-only UI preview with real account/task APIs and a fake Telegram service. No external sends."""

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [
    str(ROOT),
    str(ROOT / "panini-core/capabilities/cli/src"),
    str(ROOT / "panini-core/voice/live-spoken-conversation"),
]
os.environ["PANINI_DATA_DIR"] = tempfile.mkdtemp(prefix="panini-channel-preview-")
for key in (
    "PANINI_CLI_ENABLED",
    "JEV_REV_DESKTOP_ENABLED",
    "PANINI_CAPABILITY_ROUTER_ENABLED",
    "PANINI_PERSONAS",
    "PANINI_SPACES",
):
    os.environ[key] = "false"
os.environ["PANINI_CHANNELS"] = "telegram"
os.environ["PANINI_REDTEAM_ENFORCE"] = "false"
os.environ["PANINI_JEV_GATES"] = "false"
os.environ["AI_GATEWAY_API_KEY"] = ""


def main():
    import httpx
    import uvicorn
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles
    from panini_cli.agent.model import ScriptedModel

    from backend.app import create_app
    from backend.channels.manager import ChannelManager
    from backend.channels.telegram import Telegram, normalize
    from backend.harness.runtime import Harness

    h = Harness(model_factory=lambda: ScriptedModel([{"text": "Preview channel response."}]))
    observed = []

    def provider(request):
        import json

        payload = json.loads(request.content)
        observed.append({"method": request.url.path.rsplit("/", 1)[-1], "payload": payload})
        return httpx.Response(
            200,
            json={
                "ok": True,
                "result": [] if request.url.path.endswith("getUpdates") else {"message_id": len(observed)},
            },
        )

    def factory(account, accounts):
        return Telegram(account, accounts, transport=httpx.MockTransport(provider))

    h.channels = ChannelManager(h, h.channels.accounts.secrets, provider_factory=factory)
    app = create_app(console_token="panini-channels-preview-only", harness_factory=lambda: h)
    public = ROOT / "frontend/public/admin"
    app.mount("/admin/assets", StaticFiles(directory=public / "assets"))
    app.mount("/admin/brand", StaticFiles(directory=public / "brand"))

    @app.get("/admin/{path:path}")
    @app.get("/build/channels")
    async def shell(path: str = ""):
        return FileResponse(public / "index.html")

    @app.post("/preview/message")
    async def message():
        account = h.channels.accounts.list()[0]
        await h.channels.deliver(
            normalize(
                account["id"],
                {
                    "message": {
                        "message_id": 1,
                        "from": {"id": 42, "is_bot": False},
                        "chat": {"id": 42, "type": "private"},
                        "text": "Preview request",
                    }
                },
            )
        )
        return {"accepted": True}

    @app.get("/preview/state")
    async def state():
        return {"observed": observed, "channels": h.channels.status()}

    uvicorn.run(app, host="127.0.0.1", port=3235, access_log=False)


if __name__ == "__main__":
    main()
