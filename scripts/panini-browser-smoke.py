"""Opt-in live Browser test against the isolated local demonstration form."""

import asyncio
import json
import os
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["BROWSER_HEADLESS"] = "true"


async def main():
    from backend.app import create_app
    from backend.capabilities.browser import BrowserAdapter
    from backend.panini.contracts import Fault

    app = create_app()
    server = ThreadingHTTPServer(
        ("127.0.0.1", 3299), partial(SimpleHTTPRequestHandler, directory=str(ROOT / "frontend/public"))
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    async def confirm(action, application, effect, data):
        url = action.get("parameters", {}).get("url") or action.get("url", "")
        if url and not url.startswith("http://127.0.0.1:3299/"):
            raise Fault("TEST_BOUNDARY", "Only the isolated local demonstration is approved.")
        return "test"

    context = SimpleNamespace(
        task=SimpleNamespace(task_id=str(uuid4())),
        check=lambda: None,
        dispatch=lambda: None,
        emit=lambda *args, **kwargs: None,
        record_evidence=lambda *args: str(uuid4()),
        confirm=confirm,
    )
    adapter = BrowserAdapter(app)
    try:
        target = await adapter.select("managed-chromium")
        result = await asyncio.wait_for(
            adapter.run(
                context,
                "Navigate to http://127.0.0.1:3299/browser-demo.html. Enter PANINI browser smoke in Your demo name, click Show greeting, and verify the greeting appears.",
                target,
            ),
            240,
        )
        print(
            json.dumps(
                {
                    "outcome": result.outcome,
                    "message": result.message,
                    "error": app.state.browser_loop.last_error,
                }
            )
        )
        assert result.outcome == "verified_complete"
    finally:
        await adapter.close()
        server.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
