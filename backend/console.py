"""Web-console access. Only the local gateway, after signing a user in, may call console routes.

The gateway authenticates people (a one-time sign-in link, then an HttpOnly
session cookie) and forwards console requests with a per-launch service token.
Checking that token here means another local process cannot skip the sign-in by
calling the backend port directly. The PANINI shell API, the extension socket
and pairing redemption carry their own credentials and are not console routes.
"""

import secrets

from starlette.datastructures import Headers
from starlette.responses import JSONResponse

HEADER = "x-panini-console"
# /a2a and the agent card carry their own bearer tokens (other agents, not people).
OPEN_PATHS = {"/api/health", "/health", "/api/extension/pair", "/a2a", "/.well-known/agent-card.json"}


def console_route(path):
    return path not in OPEN_PATHS and not path.startswith("/api/panini/")


class ConsoleBoundary:
    def __init__(self, app, token=""):
        self.app, self.token = app, token or ""

    async def __call__(self, scope, receive, send):
        if self.token and scope["type"] == "http" and console_route(scope.get("path", "")):
            supplied = Headers(scope=scope).get(HEADER, "")
            if not secrets.compare_digest(supplied.encode(), self.token.encode()):
                return await JSONResponse(
                    {
                        "error": {
                            "code": "CONSOLE_SIGN_IN_REQUIRED",
                            "message": "Sign in to the PANINI console.",
                        }
                    },
                    401,
                )(scope, receive, send)
        await self.app(scope, receive, send)
