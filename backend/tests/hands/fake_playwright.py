"""A stand-in for the Playwright MCP server in tests: a few browser_* tools, a crash switch and an env probe."""

import json
import os
import sys

TOOLS = [
    {"name": "browser_snapshot", "inputSchema": {"type": "object", "properties": {}}},
    {
        "name": "browser_click",
        "inputSchema": {
            "type": "object",
            "properties": {"element": {"type": "string"}, "target": {"type": "string"}},
        },
    },
    {
        "name": "browser_type",
        "inputSchema": {
            "type": "object",
            "properties": {"target": {"type": "string"}, "text": {"type": "string"}},
        },
    },
    {
        "name": "browser_navigate",
        "inputSchema": {"type": "object", "properties": {"url": {"type": "string"}}},
    },
    {
        "name": "browser_verify_text_visible",
        "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}},
    },
    {"name": "browser_close", "inputSchema": {"type": "object", "properties": {}}},
]
if os.environ.get("FAKE_EXTRA_TOOL"):
    TOOLS.append({"name": "browser_surprise", "inputSchema": {"type": "object"}})

SNAPSHOT = '- heading "Jev browser demo" [ref=e1]\n- textbox "Your name" [ref=e2]\n- textbox "Password" [ref=e3]\n- button "Buy now" [ref=e4]'


def main():
    for line in sys.stdin:
        message = json.loads(line)
        method, identifier = message.get("method"), message.get("id")
        if identifier is None:
            continue
        if method == "initialize":
            result = {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "fake-pw"},
            }
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            name, args = message["params"]["name"], message["params"].get("arguments", {})
            if args.get("target") == "crash" or args.get("url") == "https://crash.test/":
                os._exit(3)
            if name == "browser_snapshot" and args.get("probe_env"):
                text = json.dumps(sorted(os.environ))
            elif name in ("browser_snapshot", "browser_navigate", "browser_click"):
                text = f"### Page state\n{SNAPSHOT}\n(called {name} {json.dumps(args, sort_keys=True)})"
            elif name == "browser_verify_text_visible" and args.get("text") == "missing":
                print(
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": identifier,
                            "result": {
                                "content": [{"type": "text", "text": "Text not found"}],
                                "isError": True,
                            },
                        }
                    ),
                    flush=True,
                )
                continue
            else:
                text = f"{name} ok {json.dumps(args, sort_keys=True)}"
            result = {"content": [{"type": "text", "text": text}]}
        else:
            print(
                json.dumps(
                    {"jsonrpc": "2.0", "id": identifier, "error": {"code": -32601, "message": "nope"}}
                ),
                flush=True,
            )
            continue
        print(json.dumps({"jsonrpc": "2.0", "id": identifier, "result": result}), flush=True)


if __name__ == "__main__":
    main()
