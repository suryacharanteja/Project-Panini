"""Independent bearer boundary at the exact local MCP endpoint; owner setup remains console-only."""

import ipaddress
import os
import sys
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken
from pydantic import BaseModel, ConfigDict, Field
from starlette.datastructures import Headers
from starlette.responses import JSONResponse

from .. import config, v2flags
from ..mcp_server.clients import DEFAULT_SCOPES, SCOPES

router = APIRouter(prefix="/api/admin/v1/mcp")


def loopback(host):
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


class Endpoint:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        async def error(status, message):
            await JSONResponse({"error": message}, status)(scope, receive, send)

        if not v2flags.enabled("PANINI_MCP_SERVER"):
            return await error(404, "MCP server is disabled.")
        headers = Headers(scope=scope)
        try:
            host = urlsplit("http://" + headers.get("host", "")).hostname
        except ValueError:
            host = None
        if headers.get("origin") is not None or not host or not loopback(host):
            return await error(403, "MCP requires loopback access without an Origin header.")
        peer = scope.get("client")
        if not peer or not loopback(peer[0]):
            return await error(403, "MCP requires a loopback peer.")
        service = getattr(self.app.state, "mcp", None)
        if not service or not service.available():
            return await error(503, "Enable the action ledger and durable interrupts for MCP.")
        authorization = headers.get("authorization", "")
        client = service.clients.authenticate(authorization)
        if not client:
            return await error(401, "A registered MCP client token is required.")
        scope["panini_mcp_client"] = client["id"]
        active_calls = scope["panini_mcp_calls"] = set()
        scope["user"] = AuthenticatedUser(
            AccessToken(
                token=authorization[7:], client_id=client["id"], subject=client["id"], scopes=client["grants"]
            )
        )

        async def connected_receive():
            message = await receive()
            if message["type"] == "http.disconnect":
                for task in list(active_calls):
                    task.cancel()
            return message

        await service.manager.handle_request(scope, connected_receive, send)


class ClientSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    grants: list[str] = Field(default_factory=lambda: list(DEFAULT_SCOPES), max_length=len(SCOPES))


class GrantSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool: str = Field(min_length=1, max_length=64)
    seconds: int = Field(default=3600, ge=1, le=3600)


def service(request):
    current = getattr(request.app.state, "mcp", None)
    if current is None:
        raise HTTPException(503, "The MCP runtime is unavailable.")
    return current


@router.get("/clients")
async def clients(request: Request):
    current = service(request)
    return {
        "clients": current.clients.list(),
        "scopes": list(SCOPES),
        "enabled": v2flags.enabled("PANINI_MCP_SERVER"),
        "ready": current.available(),
        "activity": list(current.activity.values()),
        "grants": [
            g.__dict__
            for c in current.clients.list()
            for g in current.h.policy.grants("mcp_client:" + c["id"])
        ],
    }


@router.post("/clients", status_code=201)
async def create_client(body: ClientSpec, request: Request):
    try:
        issued = service(request).clients.create(body.name, body.grants)
        endpoint = f"http://127.0.0.1:{os.getenv('FRONTEND_PORT', '3200')}/mcp"
        executable = config.ROOT / "cli/panini-mcp.exe"
        command = str(executable) if executable.is_file() else sys.executable
        args = [] if executable.is_file() else [str(config.ROOT / "scripts/panini-mcp.py")]
        args += ["--client", issued["id"], "--token-file", issued["token_file"], "--url", endpoint]
        return {**issued, "endpoint": endpoint, "stdio": {"command": command, "args": args}}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    except OSError:
        raise HTTPException(503, "Could not protect the client token file. No client was created.") from None


@router.delete("/clients/{identifier}")
async def revoke_client(identifier: str, request: Request):
    if not await service(request).revoke(identifier):
        raise HTTPException(404, "Active client not found.")
    return {"revoked": True}


@router.post("/clients/{identifier}/grants", status_code=201)
async def allow_session(identifier: str, body: GrantSpec, request: Request):
    import time
    from dataclasses import asdict

    from ..mcp_server.tools import mapping
    from ..policy.engine import new_grant

    current = service(request)
    client = current.clients.get(identifier)
    if not client or client["revoked"] is not None:
        raise HTTPException(404, "Active client not found.")
    internal = mapping(current.h, client, current.view(client)).get(body.tool)
    if not internal or not body.tool.startswith("browser_"):
        raise HTTPException(400, "Choose a granted browser tool.")
    grant = new_grant(
        subject="mcp_client:" + identifier,
        tools=(internal,),
        levels={"navigate": "none", "input": "none"},
        conditions={"surfaces": ["mcp"]},
        expires_at=time.time() + body.seconds,
        note="Owner approved temporary browser navigation/input; submissions still ask.",
    )
    current.h.policy.put_grant(grant)
    return asdict(grant)


@router.delete("/clients/{identifier}/grants/{grant_id}")
async def revoke_grant(identifier: str, grant_id: str, request: Request):
    current = service(request)
    if not any(g.id == grant_id for g in current.h.policy.grants("mcp_client:" + identifier)):
        raise HTTPException(404, "Client grant not found.")
    current.h.policy.revoke(grant_id)
    return {"revoked": True}
