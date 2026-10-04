"""Loopback-only, disposable Android UI preview. No real phone, driver installation or model calls."""

import mimetypes
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
os.environ["PANINI_DATA_DIR"] = tempfile.mkdtemp(prefix="panini-android-preview-")
os.environ["PANINI_V2_LEDGER"] = "true"
for key in (
    "PANINI_CLI_ENABLED",
    "JEV_REV_DESKTOP_ENABLED",
    "PANINI_CAPABILITY_ROUTER_ENABLED",
    "PANINI_PERSONAS",
    "PANINI_SPACES",
    "PANINI_PHONE_LINE",
    "PANINI_OUTBOUND_CALLS",
    "PANINI_REDTEAM_ENFORCE",
    "PANINI_JEV_GATES",
):
    os.environ[key] = "false"
os.environ.update(PANINI_ANDROID="true", PANINI_LIVE_VIEW="true", PANINI_CHANNELS="", AI_GATEWAY_API_KEY="")


def main():
    import uvicorn
    from fastapi import Request
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    from backend.app import create_app
    from backend.hands import adb
    from backend.harness.runtime import Harness
    from backend.registry.context import ToolContext, ToolView
    from backend.tests.hands.test_android import Driver

    mimetypes.add_type("text/javascript", ".mjs")

    async def fixed_adb(args, **kwargs):
        if args == ["devices", "-l"]:
            return "List of devices attached\nphone device model:Preview_Phone\n"
        if args[0] == "pair":
            return "Successfully paired to " + args[1]
        return "connected to " + args[1]

    adb.run = fixed_adb
    h = Harness(model_factory=lambda: None)
    h.android.driver = Driver()
    app = create_app(console_token="android-preview-only", harness_factory=lambda: h)
    public = ROOT / "frontend/public/admin"
    app.mount("/phone", StaticFiles(directory=ROOT / "panini-core/apps/phone"))
    for directory in ("assets", "brand"):
        app.mount("/admin/" + directory, StaticFiles(directory=public / directory))

    @app.get("/build/android")
    async def admin():
        return FileResponse(public / "index.html")

    @app.post("/preview/use")
    async def use(request: Request):
        from backend.hands.snapshot import parse

        async def approve(*args):
            return True

        ctx = ToolContext(view=ToolView(android=True), confirm=approve)
        result = await h.registry.call(ctx, "android_snapshot", {"depth": 20})
        ref = parse(result.data["snapshot"])[0].ref
        await h.registry.call(ctx, "android_fill", {"ref": ref, "text": "Preview note"})
        return h.status()

    @app.get("/preview/state")
    async def state():
        return {"android": h.android.status(), "events": h.android.driver.events}

    uvicorn.run(app, host="127.0.0.1", port=3240, access_log=False)


if __name__ == "__main__":
    main()
