"""Fail-closed boundaries for the V2-27 private preview.

The allowlist is intentionally small until each hand has an offline qualification.
It applies to both the v1 agent path and the v2 registry path; hiding model tools
alone would not prevent a direct hand call.
"""

import os

from starlette.responses import JSONResponse

LOCAL_TOOLS = frozenset(
    {
        "update_plan",
        "finish",
        "ask_user",
        "recall",
        "remember",
        "forget",
        "load_skill",
        "create_skill",
        "list_projects",
        "create_project",
        "set_project_status",
        "spaces_list",
        "pages_list",
        "pages_read",
        "pages_search",
        "pages_create",
        "pages_edit",
        "pages_move",
        "pages_delete",
        "activity_search",
    }
)

# These services can make outbound connections without a model tool call.
DISABLED_FLAGS = frozenset(
    {
        "PANINI_REMOTE_MODE",
        "PANINI_PHONE_LINE",
        "PANINI_CHANNELS",
        "PANINI_OUTBOUND_CALLS",
        "PANINI_MCP_SERVER",
        "PANINI_TRIGGERS",
        "PANINI_PRESENCE",
        "PANINI_SANDBOX",
        "PANINI_CLAW_CDP",
        "PANINI_ANDROID",
        "PANINI_TEACH",
    }
)


def active() -> bool:
    return os.getenv("PANINI_LOCAL_MODE", "off").strip().lower() == "private"


def allows_tool(name: str) -> bool:
    return not active() or name in LOCAL_TOOLS


def reason() -> str:
    return "Unavailable in private mode until this capability has an offline qualification."


def blocks_http(path: str, method: str) -> bool:
    """Refuse direct API paths that can bypass the agent's hand boundary."""
    if not active():
        return False
    if path in {"/api/compare", "/api/browser/run-stream", "/a2a", "/api/remote"}:
        return True
    if path.startswith(
        (
            "/api/voice/",
            "/api/harness/android",
            "/api/harness/teach",
            "/api/panini/harness/teach",
            "/api/harness/mobile",
            "/api/panini/harness/mobile",
            "/api/admin/v1/channels",
            "/api/admin/v1/connectors",
            "/api/admin/v1/agents",
            "/api/admin/v1/hooks",
            "/api/remote/",
            "/api/live/ingest",
            "/api/harness/connectors",
            "/api/panini/harness/connectors",
            "/api/harness/agents",
            "/api/panini/harness/agents",
        )
    ):
        return True
    # Reading session state is local; executing a coordinator task can still
    # select a cloud adapter outside the agent's registry.
    if path.startswith("/api/panini/sessions/") and method != "GET":
        return path.endswith(("/commands", "/media"))
    return False


class PrivateBoundary:
    """Pure ASGI guard: forward request bodies unchanged to the API size boundary."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and blocks_http(scope.get("path", ""), scope.get("method", "")):
            await JSONResponse({"detail": reason()}, status_code=503)(scope, receive, send)
            return
        await self.app(scope, receive, send)
