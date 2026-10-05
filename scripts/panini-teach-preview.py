"""Disposable loopback preview with the REAL Playwright driver and a deterministic fake healer.

Only the demo browser is attached. No provider requests, user profile or live account data.
"""

import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "panini-core/capabilities/cli/src")]
os.environ["PANINI_DATA_DIR"] = tempfile.mkdtemp(prefix="panini-teach-preview-")
for key in ("PANINI_TEACH", "PANINI_V2_LEDGER", "PANINI_V2_INTERRUPTS"):
    os.environ[key] = "true"
for key in (
    "PANINI_JEV_GATES",
    "PANINI_PHONE_LINE",
    "PANINI_OUTBOUND_CALLS",
    "PANINI_REDTEAM_ENFORCE",
    "PANINI_ANDROID",
    "PANINI_LIVE_VIEW",
    "PANINI_CLI_ENABLED",
    "JEV_REV_DESKTOP_ENABLED",
):
    os.environ[key] = "false"
os.environ.update(
    PANINI_BROWSER_DRIVER="playwright",
    PANINI_V2_POLICY="shadow",
    PANINI_BROWSER_TRACING="off",
    PANINI_CHANNELS="",
    AI_GATEWAY_API_KEY="",
)


def main():
    import json

    import uvicorn
    from fastapi.responses import FileResponse, HTMLResponse
    from fastapi.staticfiles import StaticFiles

    from backend.app import create_app
    from backend.hands.playwright_mcp import SIDECAR, PlaywrightSession, Preset
    from backend.harness.runtime import Harness

    class Model:
        async def respond(self, instructions, items, tools, **kwargs):
            assert tools == []
            data = json.loads(items[0]["content"])
            matching = [
                n for n in data["snapshot_untrusted"] if n["role"] == "button" and n["name"] == "Send report"
            ]
            return SimpleNamespace(
                calls=[], text=json.dumps({"ref": matching[0]["ref"] if len(matching) == 1 else None})
            )

    h = Harness(model_factory=Model)
    output = Path(os.environ["PANINI_DATA_DIR"]) / "artifacts/playwright/managed"
    output.mkdir(parents=True)
    session = PlaywrightSession(
        Preset(
            "managed",
            [
                "--cdp-endpoint",
                "http://127.0.0.1:3248",
                "--caps",
                "pdf,devtools,testing",
                "--image-responses",
                "omit",
                "--codegen",
                "none",
                "--output-dir",
                str(output),
                "--init-page",
                str(SIDECAR / "init-teach.cjs"),
            ],
        )
    )
    h.browsers.sessions["managed"] = session
    hand = h.registry.hands["browser_snapshot"]
    hand.managed.session = lambda: session
    app = create_app(console_token="teach-preview-only", harness_factory=lambda: h)
    public = ROOT / "frontend/public/admin"
    for directory in ("assets", "brand"):
        app.mount("/admin/" + directory, StaticFiles(directory=public / directory))

    @app.get("/build/skills")
    @app.get("/inbox")
    async def admin():
        return FileResponse(public / "index.html")

    @app.get("/demo")
    async def demo():
        return HTMLResponse("""<!doctype html><html lang="en"><meta charset="utf-8"><title>Disposable PANINI report demo</title>
          <style>body{font:18px system-ui;background:#121418;color:#eee;padding:48px}input,button{font:inherit;padding:14px;margin:12px}label{display:block}</style>
          <h1>Report demo</h1><label>Query <input id="query"></label><button id="save">Save report</button><p id="result" role="status"></p>
          <script>document.querySelector('#save').onclick=()=>document.querySelector('#result').textContent='Report ready: '+document.querySelector('#query').value;</script></html>""")

    @app.get("/demo/frame")
    async def frame_demo():
        return HTMLResponse(
            "<!doctype html><title>Framed report demo</title>"
            '<iframe name="Other" title="Other" src="/demo/frame-body"></iframe>'
            '<iframe name="Report" title="Report" src="/demo/frame-body"></iframe>'
        )

    @app.get("/demo/frame-body")
    async def frame_body():
        return HTMLResponse(
            '<!doctype html><label>Query <input id="query"></label>'
            '<button id="save">Save report</button><p id="result" role="status"></p>'
            '<script>save.onclick=()=>result.textContent="Report ready: "+query.value</script>'
        )

    @app.get("/preview/evidence")
    async def evidence():
        return {
            "receipts": h.store.all(
                "SELECT tool,effect,outcome,result_view FROM receipts ORDER BY started_at"
            ),
            "runs": h.store.all("SELECT * FROM skill_runs"),
            "recordings": h.store.all("SELECT * FROM teach_recordings"),
            "versions": h.teach.bundles.list(),
        }

    uvicorn.run(app, host="127.0.0.1", port=3247, access_log=False)


if __name__ == "__main__":
    main()
