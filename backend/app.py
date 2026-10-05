"""Application composition and lifecycle. Run with python -m backend.app."""

import asyncio
import logging
import os
import time
from contextlib import AsyncExitStack, asynccontextmanager
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import comparison as comparison_service
from . import config, jev_client, stt_service
from .console import ConsoleBoundary
from .extension_pairing import PairingStore
from .fabric import router as fabric_router
from .http import ApiBoundary
from .panini.contracts import Fault
from .panini.settings import Settings
from .routes import admin, agui, browser, comparison, harness, panini, speech, voice
from .routes import android as android_routes
from .routes import channels as channels_routes
from .routes import console as console_routes
from .routes import live as live_routes
from .routes import mcp as mcp_routes
from .routes import spaces as spaces_routes
from .routes import teach as teach_routes
from .work import CapacityError, WorkPool


@asynccontextmanager
async def lifespan(app: FastAPI):
    stack = AsyncExitStack()
    mcp_monitor = None
    if app.state.harness:
        from .mcp_server.server import Service

        app.state.mcp = Service(app.state.harness)
        app.state.harness.mcp = app.state.mcp
        await stack.enter_async_context(app.state.mcp.manager.run())
        mcp_monitor = asyncio.create_task(app.state.mcp.monitor())
    if app.state.panini:
        app.state.panini.start()
    if app.state.harness:
        app.state.harness.start()
        from . import v2flags

        if v2flags.enabled("PANINI_PHONE_LINE"):
            from .telephony.inbound import runtime as phone_runtime

            await phone_runtime(app).recover()
        if v2flags.value("PANINI_REMOTE_MODE") != "off":
            from .fabric.runtime import runtime

            await runtime(app).start()
    warmup = asyncio.create_task(asyncio.to_thread(jev_client.warm))
    try:
        yield
    finally:
        try:
            if mcp_monitor:
                mcp_monitor.cancel()
                await asyncio.gather(mcp_monitor, return_exceptions=True)
                await app.state.mcp.close()
            await stack.aclose()
            if getattr(app.state, "phone_line", None):
                await app.state.phone_line.close()
            if app.state.panini:
                await app.state.panini.close()
            if getattr(app.state, "fabric_runtime", None):
                await app.state.fabric_runtime.stop()
            if app.state.harness:
                await app.state.harness.close()
            if app.state.voice_session:
                await app.state.voice_session.close()
            if app.state.browser_loop:
                await app.state.browser_loop.shutdown()
            if app.state.extension_loop:
                await app.state.extension_loop.shutdown()
            if app.state.extension_bridge:
                await app.state.extension_bridge.detach()
            await asyncio.gather(app.state.comparison_work.close(), app.state.speech_work.close())
        finally:
            await warmup
            await asyncio.to_thread(jev_client.stop)


def create_app(
    *,
    comparison_limit=4,
    speech_limit=2,
    compare_operation=None,
    transcribe_operation=None,
    panini_settings=None,
    panini_adapters=None,
    console_token=None,
    pairing=None,
    harness_factory=None,
) -> FastAPI:
    """Create isolated service state; adapters are composed only at this boundary.

    One runtime serves every surface at once: the PANINI shell (coordinator API),
    the signed-in web console, and the paired browser extension.
    """
    app = FastAPI(title="PANINI", version="1.0.0", lifespan=lifespan)
    app.state.pairing = pairing or PairingStore()
    app.state.browser_loop = None
    app.state.extension_bridge = None
    app.state.extension_loop = None
    app.state.voice_session = None
    settings = panini_settings or Settings.from_env()
    app.state.panini = None
    app.state.harness = None
    app.state.admin = None
    app.state.mcp = None
    if os.getenv("PANINI_HARNESS_ENABLED", "1") != "0":
        from .harness.runtime import Harness

        app.state.harness = (harness_factory or Harness)()
        if hasattr(app.state.harness, "continuity"):
            app.state.harness.continuity.app = app
    if settings.enabled:
        from .panini.coordinator import Coordinator
        from .panini.planning import route
        from .panini.voice import VoiceMedia

        if panini_adapters is None:
            from .capabilities.browser import BrowserAdapter
            from .capabilities.code import CodeAdapter
            from .capabilities.desktop import DesktopAdapter
            from .capabilities.search import SearchAdapter

            panini_adapters = {
                "browser": BrowserAdapter(app),
                "search": SearchAdapter(),
                "desktop": DesktopAdapter(settings.desktop_enabled),
                "code": CodeAdapter(settings.code_enabled),
            }
            if app.state.harness:
                from .capabilities.agent import AgentAdapter

                app.state.harness.adapters = dict(panini_adapters)
                panini_adapters["agent"] = AgentAdapter(app.state.harness, app.state.harness.adapters)
            app.state.panini = Coordinator(settings, panini_adapters, route=route, voice_factory=VoiceMedia)
        else:
            app.state.panini = Coordinator(settings, panini_adapters)
    app.state.comparison_work = WorkPool(comparison_limit)
    app.state.speech_work = WorkPool(speech_limit)
    app.state.compare_service = compare_operation or comparison_service.compare
    app.state.transcribe_service = transcribe_operation or stt_service.transcribe_audio_bytes
    origins = config.allowed_origins()
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=[
            "localhost",
            "127.0.0.1",
            "[::1]",
            *[urlsplit(origin).hostname for origin in origins],
        ],
    )
    app.add_middleware(ApiBoundary, allowed_origins=origins)
    app.add_middleware(
        ConsoleBoundary,
        token=os.getenv("PANINI_CONSOLE_TOKEN", "") if console_token is None else console_token,
    )
    app.include_router(comparison.router)
    app.include_router(browser.router)
    app.include_router(browser.ws_router)
    app.include_router(speech.router)
    app.include_router(voice.router)
    app.include_router(panini.router)
    app.include_router(console_routes.router)
    app.include_router(harness.shell_router)
    app.include_router(harness.console_router)
    app.include_router(harness.a2a_router)
    app.include_router(spaces_routes.console_router)
    app.include_router(spaces_routes.shell_router)
    app.include_router(admin.router)
    app.include_router(android_routes.router)
    app.include_router(teach_routes.router)
    app.include_router(teach_routes.shell_router)
    app.include_router(mcp_routes.router)
    from starlette.routing import Route

    app.router.routes.append(Route("/mcp", mcp_routes.Endpoint(app), methods=["GET", "POST", "DELETE"]))
    app.include_router(channels_routes.router)
    app.include_router(agui.console_router)
    app.include_router(agui.shell_router)
    app.include_router(live_routes.console_router)
    app.include_router(live_routes.shell_router)
    app.include_router(live_routes.ingest_router)
    app.include_router(live_routes.remote_router)
    app.include_router(fabric_router.router)

    @app.exception_handler(Fault)
    async def panini_error(request: Request, error: Fault):
        return JSONResponse(error.payload(getattr(request.state, "request_id", "")), error.status)

    @app.exception_handler(CapacityError)
    async def at_capacity(_request: Request, error: CapacityError):
        return JSONResponse({"detail": str(error)}, 429, headers={"Retry-After": "1"})

    @app.get("/api/health")
    @app.get("/health", include_in_schema=False)
    async def health():
        return {
            "status": "ok",
            "timestamp": time.time(),
            "providers": {
                "openai_configured": config.configured("OPENAI_API_KEY"),
                "jev_configured": config.configured("AI_GATEWAY_API_KEY"),
                "jev_ready": jev_client.status(),
                "speech_configured": config.configured("ASSEMBLYAI_API_KEY"),
            },
            "work": {
                "comparisons": {"active": app.state.comparison_work.active, "limit": comparison_limit},
                "transcriptions": {"active": app.state.speech_work.active, "limit": speech_limit},
                "voice": {"active": int(app.state.voice_session is not None), "limit": 1},
            },
        }

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    uvicorn.run(
        app, host="127.0.0.1", port=int(os.getenv("BACKEND_PORT", "8000")), log_level="info", access_log=False
    )
