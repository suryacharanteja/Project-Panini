"""V2-12 integration (local): a real Playwright page streams frames only while watched, at most 4 fps."""

import asyncio
import contextlib
import socket
import threading
import time

import pytest

from backend import live
from backend.hands import playwright_mcp as pm

pytestmark = pytest.mark.skipif(pm.node_executable() is None, reason="Node.js is not installed.")


def _serve(port):
    import uvicorn
    from fastapi import FastAPI

    from backend.routes import live as routes

    app = FastAPI()
    app.include_router(routes.ingest_router)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            return server
        time.sleep(0.05)
    raise RuntimeError("test backend did not start")


@pytest.fixture
def backend(monkeypatch):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    monkeypatch.setenv("PANINI_LIVE_VIEW", "true")
    monkeypatch.setenv("BACKEND_PORT", str(port))
    monkeypatch.setattr(live, "_hub", None)
    server = _serve(port)
    yield
    server.should_exit = True


def test_frames_flow_only_while_watched(backend, tmp_path):
    async def scenario():
        hub = live.hub()
        session = pm.PlaywrightSession(pm.isolated_preset("live-test", tmp_path, live=("managed", "test")))
        try:
            page = (
                "data:text/html,<h1 id=t>0</h1><script>setInterval(()=>t.textContent=Date.now(),50)</script>"
            )
            await session.call("browser_navigate", {"url": page}, timeout_s=90)
            await asyncio.sleep(3)
            assert hub.latest("managed", "test") is None  # Nobody watching: no frames.
            seen = []
            async with contextlib.aclosing(hub.subscribe("managed", "test", max_fps=10)) as frames:
                started = time.monotonic()
                async for frame in frames:
                    seen.append(frame)
                    if time.monotonic() - started > 5:
                        break
            assert len(seen) >= 3
            produced = hub.latest("managed", "test")[1]["seq"]
            assert produced <= 4 * 8  # Throttled at the source: at most 4 fps (with start-up slack).
            assert seen[-1]["width"] > 0 and seen[-1]["frame"]
            await asyncio.sleep(8)  # Grace period (5 s) plus a poll: the source stops.
            settled = hub.latest("managed", "test")[1]["seq"]
            await asyncio.sleep(3)
            assert hub.latest("managed", "test")[1]["seq"] == settled
        finally:
            await session.close()

    asyncio.run(scenario())
