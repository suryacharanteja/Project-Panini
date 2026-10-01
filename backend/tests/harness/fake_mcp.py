"""A minimal MCP stdio server for tests: one read-only tool and one that changes data."""

import json
import sys

TOOLS = [
    {
        "name": "lookup",
        "description": "Look up a note.",
        "inputSchema": {"type": "object", "properties": {"q": {"type": "string"}}},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "create_note",
        "description": "Create a note.",
        "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}},
    },
]

for line in sys.stdin:
    message = json.loads(line)
    method, identifier = message.get("method"), message.get("id")
    if identifier is None:
        continue
    if method == "initialize":
        result = {
            "protocolVersion": "2025-06-18",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "fake"},
        }
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        params = message["params"]
        text = f"{params['name']} -> {json.dumps(params.get('arguments', {}), sort_keys=True)}"
        result = {"content": [{"type": "text", "text": text}], "isError": False}
    else:
        print(
            json.dumps({"jsonrpc": "2.0", "id": identifier, "error": {"code": -32601, "message": "nope"}}),
            flush=True,
        )
        continue
    print(json.dumps({"jsonrpc": "2.0", "id": identifier, "result": result}), flush=True)
