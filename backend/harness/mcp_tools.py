"""Bounded MCP tool discovery and consistent results for both transports."""

import json

MAX_TOOLS = 500
MAX_PAGES = 20


async def list_tools(request):
    tools, seen_names, seen_cursors = [], set(), set()
    cursor = None
    for _ in range(MAX_PAGES):
        page = await request("tools/list", {"cursor": cursor} if cursor else {})
        if not isinstance(page, dict) or not isinstance(page.get("tools"), list):
            raise ValueError("Connector returned an invalid tool catalogue.")
        for tool in page["tools"]:
            if not isinstance(tool, dict) or not isinstance(tool.get("name"), str) or not tool["name"]:
                raise ValueError("Connector returned an invalid tool name.")
            if tool["name"] in seen_names:
                raise ValueError("Connector returned duplicate tool names.")
            seen_names.add(tool["name"])
            annotations = tool.get("annotations") or {}
            if not isinstance(annotations, dict):
                annotations = {}
            tools.append(
                {
                    "name": tool["name"],
                    "description": str(tool.get("description") or "")[:500],
                    "read_only": annotations.get("readOnlyHint") is True,
                    "input_schema": tool.get("inputSchema") or {"type": "object"},
                }
            )
            if len(tools) > MAX_TOOLS:
                raise ValueError("Connector tool catalogue exceeds 500 tools.")
        cursor = page.get("nextCursor")
        if not cursor:
            return tools
        if not isinstance(cursor, str) or cursor in seen_cursors:
            raise ValueError("Connector returned invalid or repeated pagination.")
        seen_cursors.add(cursor)
    raise ValueError("Connector tool pagination exceeds 20 pages.")


def tool_result(result):
    if not isinstance(result, dict):
        raise ValueError("Connector returned an invalid tool result.")
    parts = []
    for part in result.get("content") or []:
        if not isinstance(part, dict):
            raise ValueError("Connector returned invalid content.")
        parts.append(
            str(part.get("text", ""))
            if part.get("type") == "text"
            else f"[{part.get('type', 'content')} omitted]"
        )
    if result.get("structuredContent") is not None:
        parts.append(json.dumps(result["structuredContent"], ensure_ascii=False))
    return {"is_error": bool(result.get("isError")), "text": "\n".join(parts)[:20000]}
