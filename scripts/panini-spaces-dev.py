"""Loopback-only notebook preview with a throwaway database and the built Admin UI."""

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["PANINI_SPACES"] = "true"
os.environ["PANINI_V2_AGUI"] = "true"
os.environ["PANINI_V2_INTERRUPTS"] = "true"
os.environ["PANINI_JEV_GATES"] = "false"

import uvicorn  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from panini_cli.agent.model import ScriptedModel  # noqa: E402

from backend.console import ConsoleBoundary  # noqa: E402
from backend.harness.runtime import Harness  # noqa: E402
from backend.harness.skills import Skills  # noqa: E402
from backend.harness.store import Store  # noqa: E402
from backend.routes.agui import console_router as agui_router  # noqa: E402
from backend.routes.spaces import console_router  # noqa: E402

app = FastAPI()
directory = Path(tempfile.mkdtemp(prefix="panini-spaces-"))
store = Store(directory / "preview.db")
model = ScriptedModel([])
app.state.harness = Harness(store, model_factory=lambda: model, skills=Skills(user_root=directory / "skills"))
app.add_middleware(ConsoleBoundary, token="panini-spaces-development-only-token")
app.include_router(console_router)
app.include_router(agui_router)
ui = ROOT / "frontend/public/admin"
app.mount("/admin/assets", StaticFiles(directory=ui / "assets"), name="assets")
app.mount("/admin/brand", StaticFiles(directory=ui / "brand"), name="brand")


@app.post("/api/harness/spaces-dev/draft/{page_id}")
async def scripted_draft(page_id: str):
    page = app.state.harness.spaces.read(page_id)
    model.steps = [
        {
            "calls": [
                {
                    "name": "pages_edit",
                    "arguments": {
                        "page_id": page_id,
                        "expected_revision": page["revision"],
                        "content": "# Reviewed plan\n\nApprove or edit this draft before saving.",
                    },
                }
            ]
        },
        {
            "calls": [
                {
                    "name": "finish",
                    "arguments": {"outcome": "verified_complete", "message": "Saved the approved page."},
                }
            ]
        },
    ]
    return {"ok": True, "model": "scripted preview, no provider calls"}


@app.get("/{path:path}")
async def index(path: str):
    return FileResponse(ui / "index.html")


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=3223)
