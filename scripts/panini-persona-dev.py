"""Loopback-only persona preview with a throwaway database and scripted identity replies.

Build Admin first. Used by panini-persona-smoke.mjs; never starts product tasks or external tools.
"""

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["PANINI_PERSONAS"] = "true"
os.environ["PANINI_JEV_GATES"] = "false"
os.environ["PANINI_HARNESS_ENABLED"] = "0"
for switch in ("PANINI_CLI_ENABLED", "JEV_REV_DESKTOP_ENABLED", "PANINI_CAPABILITY_ROUTER_ENABLED"):
    os.environ[switch] = "false"

import uvicorn  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402


def main():
    token = os.environ["PANINI_PERSONA_PREVIEW_TOKEN"]
    port = int(os.environ.get("PANINI_PERSONA_PREVIEW_PORT", "3231"))
    with tempfile.TemporaryDirectory(prefix="panini-persona-preview-") as directory:
        root = Path(directory)
        os.environ["PANINI_DATA_DIR"] = str(root)
        from panini_cli.agent.model import ScriptedModel

        from backend.app import create_app
        from backend.harness.runtime import Harness
        from backend.harness.skills import Skills
        from backend.harness.store import Store

        h = Harness(
            Store(root / "preview.sqlite3"),
            skills=Skills(user_root=root / "skills"),
            model_factory=lambda: ScriptedModel(
                [{"text": "I am your specialist, ready for an assigned task."}]
            ),
        )
        app = create_app(harness_factory=lambda: h, console_token=token)
        app.state.harness = h
        app.mount("/admin", StaticFiles(directory=ROOT / "frontend/public/admin"), name="admin-preview")

        @app.get("/{path:path}")
        async def index(path: str):
            return FileResponse(ROOT / "frontend/public/admin/index.html")

        try:
            uvicorn.run(app, host="127.0.0.1", port=port, lifespan="off", log_level="warning")
        finally:
            h.store.close()


if __name__ == "__main__":
    main()
