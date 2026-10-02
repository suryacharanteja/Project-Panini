"""Tool specs synthesized from MCP connector servers, so policy and receipts can reason about each
connector tool (V2-02 §5.6). The model still reaches them through the use_connector meta tool."""

import re

from ..core_contracts import EffectClass, ToolSpec


def connector_tool_name(server: str, tool: str) -> str:
    return "mcp_" + re.sub(r"[^a-z0-9_]", "_", f"{server}__{tool}".lower())[:58]


def effect_for(annotations: dict | None, read_only: bool) -> tuple[EffectClass, tuple[str, ...]]:
    """MCP hints → effect class. Non-read-only tools default to destroy unless declared non-destructive."""
    annotations = annotations or {}
    if read_only or annotations.get("readOnlyHint") is True:
        taints = ("mcp",) if annotations.get("openWorldHint", True) is not False else ()
        return EffectClass.OBSERVE, taints
    if annotations.get("destructiveHint", True) is False:
        return EffectClass.SUBMIT, ()
    return EffectClass.DESTROY, ()


def connector_specs(connectors) -> list[ToolSpec]:
    specs = []
    for server in (getattr(connectors, "servers", None) or {}).values():
        background = set(server.spec.get("background_read_only", []))
        for tool in getattr(server, "tools", []) or []:
            effect, taints = effect_for(tool.get("annotations"), bool(tool.get("read_only")))
            specs.append(
                ToolSpec(
                    name=connector_tool_name(server.name, tool["name"]),
                    capability=f"mcp.{server.name}",
                    hand="connector",
                    title=tool["name"],
                    description=str(tool.get("description", ""))[:1000] or tool["name"],
                    input_schema={"type": "object", "additionalProperties": False, "properties": {}},
                    effect=effect,
                    taints=taints,
                    background=tool["name"] in background and effect is EffectClass.OBSERVE,
                    meta=False,
                    strict=False,
                )
            )
    return specs
