"""Generate the browser vocabulary v2 manifest (V2-10) from the tool table and Playwright's own schemas.

Every offered Playwright MCP 1.63 tool keeps its name, description and input schema (schemas are checked against
the pinned catalogue); PANINI adds browser_select, browser_status and browser_media. Re-run after changing
backend/hands/browser_tools.py or upgrading playwright-core.

    .venv\\Scripts\\python.exe scripts/browser-manifest.py
"""

import asyncio
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.hands.browser_tools import TOOLS  # noqa: E402
from backend.hands.playwright_mcp import (  # noqa: E402
    clean_env,
    entry_script,
    load_catalogue,
    node_executable,
    schema_digest,
)
from backend.harness.mcp_stdio import McpStdio  # noqa: E402

OUT = ROOT / "panini-core/capabilities/browser/manifests/22-browser.json"
ORDER = {name: index for index, name in enumerate(TOOLS)}


async def playwright_tools():
    client = McpStdio("browser manifest")
    await client.spawn(
        node_executable(),
        [str(entry_script()), "--caps", "vision,pdf,devtools,testing,storage,network"],
        env=clean_env(),
    )
    try:
        await client.initialize()
        return {t["name"]: t for t in (await client.request("tools/list", {}))["tools"]}
    finally:
        await client.close(grace_s=2)


# Arguments PANINI does not offer: `filename` writes files outside PANINI's artifacts (an unclassified local
# write); a shallow snapshot `depth` hides content (long pages are truncated into a searchable artifact instead).
DROPPED = {"filename"}
DROPPED_FOR = {"browser_snapshot": {"depth"}}


def strict_object(schema, name=""):
    schema = copy.deepcopy(schema or {"type": "object", "properties": {}})
    for key in DROPPED | DROPPED_FOR.get(name, set()):
        schema.get("properties", {}).pop(key, None)
        if key in schema.get("required", []):
            schema["required"].remove(key)
    schema.setdefault("type", "object")
    schema.setdefault("properties", {})
    schema["additionalProperties"] = False
    schema.pop("$schema", None)
    return schema


def main():
    listed = asyncio.run(playwright_tools())
    pinned = load_catalogue()
    missing = sorted(set(listed) - set(TOOLS))
    if missing:
        raise SystemExit(f"Unmapped Playwright tools: {missing}")
    tools = []
    for name, entry in TOOLS.items():
        if not entry.offered:
            continue
        upstream = listed[name]
        if pinned.get(name) != schema_digest(upstream.get("inputSchema", {})):
            raise SystemExit(f"{name}: schema differs from the pinned catalogue")
        note = {
            "never": " Not available in the person's Chrome.",
            "tap": " Always needs the person's approval.",
        }
        availability = ""
        if entry.chrome == "never":
            availability += note["never"]
        if "tap" in (entry.chrome, entry.managed, entry.isolated):
            availability += note["tap"]
        tools.append(
            {
                "name": name,
                "hand": "browser",
                "title": entry.title or name.removeprefix("browser_").replace("_", " ").capitalize(),
                "description": (upstream.get("description", "").strip() + availability)[:4000],
                "input_schema": strict_object(upstream.get("inputSchema"), name),
                "effect": entry.effect,
                "dynamic_effect": entry.dynamic,
                "group": entry.group,
                "evidence": [entry.evidence] if entry.evidence else [],
                "taints": ["web"] if entry.effect in ("observe", "navigate") else [],
                "timeout_ms": 60000,
                "concurrency": "browser",
                "requires": ["browser_v2"],
                "strict": False,
                "order": 60 + ORDER[name],
            }
        )
    tools += [
        {
            "name": "browser_select",
            "hand": "browser",
            "title": "Choose the browser",
            "description": "Choose which browser the next browser_* tools act on: chrome (the person's own tab attached in JevRev Claw), managed (PANINI's own persistent profile) or isolated (a fresh in-memory browser for untrusted links). Use managed when the person asks for a separate browser.",
            "input_schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"browser": {"type": "string", "enum": ["chrome", "managed", "isolated"]}},
                "required": ["browser"],
            },
            "effect": "navigate",
            "group": "core",
            "requires": ["browser_v2"],
            "order": 58,
        },
        {
            "name": "browser_status",
            "hand": "browser",
            "title": "Browser status",
            "description": "Which browsers are ready and which one the browser_* tools act on.",
            "input_schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {},
                "required": [],
            },
            "effect": "observe",
            "group": "core",
            "requires": ["browser_v2"],
            "order": 59,
        },
        {
            "name": "browser_media",
            "hand": "browser",
            "title": "Video playback",
            "description": "Control the main video on the page (YouTube and similar): state, play, pause, or skip_ad when a skippable ad shows. Returns playing, ad_showing and ad_skippable_now.",
            "input_schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"action": {"type": "string", "enum": ["state", "play", "pause", "skip_ad"]}},
                "required": ["action"],
            },
            "effect": "input",
            "group": "core",
            "evidence": ["assertion"],
            "requires": ["browser_v2"],
            "order": 57,
        },
    ]
    manifest = {
        "schema": "panini.capability/1",
        "id": "browser_v2",
        "title": "Browser (Playwright vocabulary)",
        "version": "2.0.0",
        "summary": "Playwright's browser tools on the run's active browser: your attached Chrome tab, PANINI's managed profile or an isolated browser.",
        "entry": "backend.hands.browser_v2:BrowserHand",
        "prompt": {
            "principles": [
                "Browser tools act on the active browser (browser_status shows it): the person's attached Chrome tab when available, otherwise PANINI's own browser. Use browser_select managed when they ask for a separate browser.",
                "Read the page with browser_snapshot, then act on element refs from the latest snapshot, describing each element exactly as the snapshot names it; take a new snapshot when the page has changed.",
                "After an action that should change the page, confirm the change with a browser_verify_* tool before reporting success.",
                "Prefer robust targets; never wait for network idle; never type passwords or security codes.",
            ]
        },
        "tools": tools,
    }
    OUT.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {OUT.name} with {len(tools)} tools")


if __name__ == "__main__":
    main()
