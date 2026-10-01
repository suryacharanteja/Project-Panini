"""ASGI request boundary: local access, bounded bodies and safe request tracing."""

import json
import logging
import time
from uuid import UUID, uuid4

from starlette.datastructures import URL, Headers, MutableHeaders
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse

logger = logging.getLogger("jev.http")
MAX_BODY_BYTES = 15 * 1024 * 1024


class ApiBoundary:
    def __init__(self, app, allowed_origins=()):
        self.app = app
        self.allowed_origins = set(allowed_origins)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        headers = Headers(scope=scope)
        try:
            request_id = str(UUID(headers.get("x-request-id", "")))
        except ValueError:
            request_id = str(uuid4())
        scope.setdefault("state", {})["request_id"] = request_id
        started, size, status = time.monotonic(), 0, 500

        async def traced_send(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                response_headers = MutableHeaders(scope=message)
                response_headers["x-request-id"] = request_id
                response_headers["cache-control"] = "no-store"
            await send(message)

        async def bounded_receive():
            nonlocal size
            message = await receive()
            if message["type"] == "http.request":
                size += len(message.get("body", b""))
                if size > MAX_BODY_BYTES:
                    raise HTTPException(413, "Request is too large")
            return message

        try:
            origin = headers.get("origin")
            expected = str(URL(scope=scope).replace(path="", query=""))
            allowed = origin in self.allowed_origins
            if (
                scope.get("path") == "/api/extension/pair"
                and origin
                and origin.startswith(("chrome-extension://", "moz-extension://"))
            ):
                allowed = True  # The one-time pairing code is the authority for this endpoint only.
            if (origin and origin != expected and not allowed) or (
                headers.get("sec-fetch-site") == "cross-site" and not allowed
            ):
                return await JSONResponse({"error": "Origin is not allowed"}, 403)(
                    scope, receive, traced_send
                )
            length = headers.get("content-length")
            if length and (not length.isdigit() or int(length) > MAX_BODY_BYTES):
                return await JSONResponse({"error": "Request is too large"}, 413)(scope, receive, traced_send)
            await self.app(scope, bounded_receive, traced_send)
        finally:
            # Never log query strings, request bodies, audio, cookies or credentials.
            route = getattr(scope.get("route"), "path", "unmatched")
            logger.info(
                json.dumps(
                    {
                        "event": "http.request",
                        "request_id": request_id,
                        "method": scope["method"],
                        "route": route,
                        "status": status,
                        "duration_ms": round((time.monotonic() - started) * 1000),
                    }
                )
            )
