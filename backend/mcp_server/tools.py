"""Curated tools and registry hand. No host shell, owner memory or arbitrary tool passthrough."""

import base64
import hashlib
import io
from copy import deepcopy
from dataclasses import asdict, replace

from mcp import types
from PIL import Image

from .. import v2flags
from ..core_contracts import EffectClass, ToolResult

BROWSER_TOOLS = frozenset(
    {
        "browser_status",
        "browser_select",
        "browser_snapshot",
        "browser_take_screenshot",
        "browser_find",
        "browser_verify_element_visible",
        "browser_verify_text_visible",
        "browser_verify_value",
        "browser_verify_list_visible",
        "browser_navigate",
        "browser_navigate_back",
        "browser_click",
        "browser_type",
        "browser_select_option",
        "browser_press_key",
        "browser_tabs",
        "browser_wait_for",
    }
)
ALIASES = {
    "panini_status": "mcp_status",
    "desktop_windows": "mcp_desktop_windows",
    "desktop_screenshot": "mcp_desktop_screenshot",
    "receipts_search": "mcp_receipts_search",
    "search_web": "search_run",
    "desktop_do": "mcp_desktop_do",
    "panini_ask": "mcp_ask",
}


def mapping(harness, client, view):
    names = {"panini_status": "mcp_status", "receipts_search": "mcp_receipts_search"}
    names["panini_ask"] = "mcp_ask"
    scopes = set(client["grants"])
    if "desktop" in scopes:
        names.update({k: v for k, v in ALIASES.items() if k.startswith("desktop_")})
    if "search" in scopes:
        names["search_web"] = "search_run"
    if "browser" in scopes:
        names.update({n: n for n in BROWSER_TOOLS})
    if "pages" in scopes and v2flags.enabled("PANINI_SPACES"):
        names.update({"pages_search": "mcp_pages_search", "pages_read": "mcp_pages_read"})
    if "sandbox" in scopes:
        from ..hands.sandbox import TOOL_ACTIONS

        names.update({n: n for n in (*TOOL_ACTIONS, "sandbox_status", "sandbox_start", "sandbox_stop")})
    return {
        public: internal
        for public, internal in names.items()
        if internal in harness.registry.specs
        and harness.registry.enabled(harness.registry.specs[internal].capability)
        and view.satisfies(harness.registry.specs[internal].requires)
    }


def annotations(spec):
    read = spec.effect == EffectClass.OBSERVE and not spec.dynamic_effect
    return types.ToolAnnotations(
        readOnlyHint=read,
        destructiveHint=not read,
        idempotentHint=bool(spec.idempotent),
        openWorldHint=spec.hand not in {"desktop", "mcp"},
    )


def schema(public, spec, *, client=None, harness=None):
    if public == "panini_ask" and client is not None:
        result = deepcopy(spec.input_schema)
        result["properties"]["persona"]["enum"] = [
            "panini",
            *[
                s.split(":", 1)[1]
                for s in client["grants"]
                if s.startswith("persona:")
                and s != "persona:panini"
                and v2flags.enabled("PANINI_PERSONAS")
                and harness.store.one(
                    "SELECT id FROM personas WHERE id=? AND archived=0", (s.split(":", 1)[1],)
                )
            ],
        ]
        return result
    if public == "search_web":
        return {
            "type": "object",
            "properties": {"query": {"type": "string", "minLength": 1, "maxLength": 300}},
            "required": ["query"],
            "additionalProperties": False,
        }
    return spec.input_schema


def persona_allows(harness, persona_id, internal):
    if persona_id in {None, "panini"}:
        return True
    harness.personas.get(persona_id)  # Archived personas cannot use even control tools.
    if internal in {"", "finish", "update_plan", "mcp_status", "mcp_receipts_search"}:
        return True
    spec = harness.registry.tool(internal)
    public = next((p for p, i in ALIASES.items() if i == internal), internal)
    if internal.startswith("mcp_pages_"):
        public = internal.removeprefix("mcp_")
    capability = (
        "spaces"
        if internal.startswith("mcp_pages_")
        else "desktop"
        if public.startswith("desktop_")
        else "browser"
        if public.startswith("browser_")
        else spec.capability
    )
    return harness.personas.allows(persona_id, replace(spec, name=public, capability=capability))


def image_bytes(raw):
    if len(raw) > 4_000_000:
        raise ValueError("Screenshot is too large.")
    with Image.open(io.BytesIO(raw)) as picture:
        if picture.width * picture.height > 20_000_000:
            raise ValueError("Screenshot dimensions are too large.")
        picture.thumbnail((1600, 1200))
        stream = io.BytesIO()
        picture.convert("RGB").save(stream, format="JPEG", quality=80)
    data = stream.getvalue()
    if len(data) > 1_000_000:
        raise ValueError("Screenshot exceeds the 1 MB result limit.")
    return data


class Hand:
    def __init__(self, manifest, runtime):
        self.h = runtime.get("harness")

    async def call(self, ctx, spec, args):
        if self.h is None or ctx.principal.kind != "mcp_client":
            return ToolResult("blocked", "This tool requires a registered MCP client runtime.")
        ctx.check()
        if spec.name == "mcp_ask":
            from .agent import ClientAgent

            state = ctx.mcp_service.new_state()
            child = ctx.mcp_service.context(ctx.mcp_client, state, ctx.mcp_request)
            persona_id = args.get("persona", "panini")
            if persona_id != "panini":
                if (
                    not v2flags.enabled("PANINI_PERSONAS")
                    or "persona:" + persona_id not in ctx.mcp_client["grants"]
                ):
                    return ToolResult("blocked", "This persona is unavailable to this client.")
                self.h.personas.get(persona_id)
                child.mcp_persona = persona_id
            agent = ClientAgent(ctx.mcp_service, child, emit=child.mcp_emit)
            try:
                result = await agent.run(args["goal"])
            finally:
                await ctx.mcp_service.close_run(child.run_id)
            data = asdict(result)
            data["persona"] = self.h.personas.presentation(persona_id)
            data["receipts"] = [
                r["id"] for r in self.h.store.all("SELECT id FROM receipts WHERE run_id=?", (child.run_id,))
            ]
            return ToolResult(
                "ok" if result.outcome in {"reply", "verified_complete"} else "blocked",
                result.message,
                data=data,
            )
        if spec.name == "mcp_desktop_do":
            return await ctx.mcp_desktop(args)
        if spec.name == "mcp_pages_search":
            return ToolResult(
                data={"pages": self.h.spaces.search(args["query"], getattr(ctx, "mcp_persona", "panini"))},
                tainted=True,
            )
        if spec.name == "mcp_pages_read":
            page = self.h.spaces.read(args["page_id"], getattr(ctx, "mcp_persona", "panini"))
            return ToolResult(data={k: page[k] for k in ("id", "title", "content", "revision")}, tainted=True)
        if spec.name == "mcp_status":
            return ToolResult(
                data={
                    "enabled": v2flags.enabled("PANINI_MCP_SERVER"),
                    "paused": self.h.policy.paused(),
                    "client": {"id": ctx.principal.client_id, "name": ctx.mcp_client["name"]},
                    "grants": ctx.mcp_client["grants"],
                    "desktop_available": bool(
                        self.h.adapters.get("desktop") and self.h.adapters["desktop"].enabled
                    ),
                }
            )
        if spec.name == "mcp_receipts_search":
            # Filter inside SQL before search/limit. Never hand an owner's receipt to an MCP client.
            query = (
                "%"
                + args.get("query", "").replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                + "%"
            )
            rows = self.h.store.all(
                "SELECT id,tool,outcome,result_view,started_at FROM receipts "
                "WHERE json_extract(principal,'$.kind')='mcp_client' "
                "AND json_extract(principal,'$.client_id')=? AND started_at>=? "
                "AND (tool LIKE ? ESCAPE '\\' OR result_view LIKE ? ESCAPE '\\') "
                "ORDER BY started_at DESC LIMIT 30",
                (ctx.principal.client_id, args.get("since", 0), query, query),
            )
            return ToolResult(data={"receipts": rows})
        adapter = self.h.adapters.get("desktop")
        if not adapter or not adapter.enabled:
            return ToolResult("blocked", "Launch PANINI with the Windows companion enabled.")
        if spec.name == "mcp_desktop_windows":
            raw = await adapter.pipe.call({"op": "targets", "task_id": ctx.run_id})
            ctx.check()
            ctx.mcp_state["windows"] = {
                hashlib.sha256(f"{t['hwnd']}:{t['pid']}:{t['created']}".encode()).hexdigest()[:32]: t
                for t in raw["targets"]
                if adapter.permits(t["application"])
            }
            return ToolResult(
                data={
                    "windows": [
                        {"window": key, "title": t["label"], "application": t["application"]}
                        for key, t in ctx.mcp_state["windows"].items()
                    ]
                },
                evidence=[{"kind": "observation", "summary": "Listed permitted windows."}],
            )
        windows = ctx.mcp_state.get("windows", {})
        key = args.get("window")
        target = windows.get(key) if key else next(iter(windows.values())) if len(windows) == 1 else None
        if not target or not adapter.permits(target["application"]):
            return ToolResult("blocked", "List desktop_windows and choose a window from that result.")
        page = await adapter.pipe.call(
            {"op": "snapshot", "task_id": ctx.run_id, "target": target, "depth": 20, "max_nodes": 600}
        )
        if any("password" in n.get("states", []) for n in page.get("nodes", [])):
            return ToolResult("blocked", "This window contains a credential field; its image is private.")
        ctx.check()
        raw = await adapter.pipe.call({"op": "capture", "task_id": ctx.run_id, "target": target})
        data = image_bytes(base64.b64decode(raw["image_base64"], validate=True))
        ctx.check()
        artifact = self.h.artifacts.put("screenshot", data, "image/jpeg", run_id=ctx.run_id)
        return ToolResult(
            data={"window": key, "application": target["application"]},
            artifact_ids=[artifact],
            evidence=[{"kind": "screenshot", "summary": "Window captured."}],
        )
