"""Pin the Playwright MCP tool catalogue (V2-08).

Starts the Playwright MCP server from the installed playwright-core with every capability, lists its tools
(no browser is launched for that) and writes panini-core/capabilities/browser/playwright/tools-<version>.json:
each tool's name, read-only hint and the sha256 of its input schema. A session refuses to start when a tool
it is offered is missing from this file or its schema changed. Re-run after upgrading playwright-core.

    .venv\\Scripts\\python.exe scripts/playwright-catalogue.py [--check]
"""

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.hands.playwright_mcp import (  # noqa: E402
    CATALOGUE,
    VERSION,
    clean_env,
    entry_script,
    node_executable,
    schema_digest,
)
from backend.harness.mcp_stdio import McpStdio  # noqa: E402

# Every capability, so later items can enable more tools without re-pinning (presets choose a subset).
CAPS = "vision,pdf,devtools,testing,storage,network"


async def listed() -> list[dict]:
    client = McpStdio("playwright catalogue")
    await client.spawn(node_executable(), [str(entry_script()), "--caps", CAPS], env=clean_env())
    try:
        await client.initialize()
        result = await client.request("tools/list", {})
    finally:
        await client.close(grace_s=2)
    return result["tools"]


def catalogue(tools: list[dict]) -> dict:
    return {
        "playwright_core": VERSION,
        "caps": CAPS,
        "tools": [
            {
                "name": t["name"],
                "read_only": bool((t.get("annotations") or {}).get("readOnlyHint")),
                "schema_sha256": schema_digest(t.get("inputSchema", {})),
            }
            for t in sorted(tools, key=lambda t: t["name"])
        ],
    }


def main():
    current = catalogue(asyncio.run(listed()))
    text = json.dumps(current, indent=2) + "\n"
    if "--check" in sys.argv:
        pinned = CATALOGUE.read_text(encoding="utf-8") if CATALOGUE.exists() else ""
        if pinned != text:
            print(f"{CATALOGUE.name} differs from playwright-core {VERSION}; re-run without --check.")
            return 1
        print(f"{CATALOGUE.name}: {len(current['tools'])} tools match.")
        return 0
    CATALOGUE.write_text(text, encoding="utf-8")
    print(f"wrote {CATALOGUE.name} with {len(current['tools'])} tools")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
