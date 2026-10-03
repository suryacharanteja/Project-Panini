"""The fabric listener (V2-14 §5.4): pairing, the envelope WebSocket and the Funnel webhook route.

- lan: binds 0.0.0.0 and accepts only private-network clients (as the v1 phone listener).
- tailscale: binds 127.0.0.1 behind `tailscale serve`, and every request must carry the Tailscale-User-Login of the
  configured owner (Serve adds it; nothing outside the tailnet can reach the port).
The phone app's pages come with V2-15; this listener carries the protocol.
"""

import base64
import json
import secrets
import uuid

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field

from ..devices import PairingError
from ..envelope import EnvelopeError

MAX_FRAME = 1024 * 1024


class Pair(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=6, max_length=12)
    name: str = Field(default="Phone", max_length=60)
    sign_pub: str = Field(max_length=200)
    agree_pub: str = Field(max_length=200)


def allowed(mode: str, client: str, headers, owner_login: str) -> bool:
    if mode == "tailscale":
        login = headers.get("tailscale-user-login", "")
        return (
            client in ("127.0.0.1", "::1")
            and bool(owner_login)
            and secrets.compare_digest(login, owner_login)
        )
    from ...harness.mobile import private_client

    return private_client(client)


def build(fabric, mode: str, owner_login: str = "") -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        client = request.client.host if request.client else ""
        if not allowed(mode, client, request.headers, owner_login):
            return JSONResponse({"detail": "Not allowed."}, 403)
        return await call_next(request)

    @app.post("/fabric/pair")
    async def pair(payload: Pair):
        try:
            return fabric.devices.pair(payload.code, payload.name, payload.sign_pub, payload.agree_pub)
        except (PairingError, ValueError) as exc:
            return JSONResponse({"detail": str(exc)}, 400)

    @app.websocket("/fabric")
    async def session(ws: WebSocket):
        client = ws.client.host if ws.client else ""
        if not allowed(mode, client, ws.headers, owner_login):
            await ws.close(code=4003)
            return
        await ws.accept()
        conn = uuid.uuid4().hex
        try:
            while True:
                raw = await ws.receive_text()
                if len(raw) > MAX_FRAME:
                    await ws.close(code=1009)
                    return
                try:
                    frame = json.loads(raw)
                except ValueError:
                    await ws.close(code=1003)
                    return
                await ws.send_text(json.dumps(await fabric.server.handle(conn, frame)))
        except (WebSocketDisconnect, EnvelopeError):
            pass
        finally:
            fabric.server.close(conn)

    @app.api_route("/hooks/{hook_id}/{secret}", methods=["GET", "POST"])
    async def hook(hook_id: str, secret: str, request: Request):
        body = await request.body()
        answer = await fabric.ingress({
            "method": request.method, "path": f"/{hook_id}/{secret}", "query": request.url.query,
            "headers": dict(request.headers), "body": base64.b64encode(body).decode(),
        })  # fmt: skip
        return PlainTextResponse(answer.get("body") or "", answer.get("status", 200))

    return app
