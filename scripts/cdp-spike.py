"""V2-09 spike: record the CDP methods Playwright MCP 1.63 sends in chrome (relay) mode.

Runs the real Playwright MCP server against PANINI's CDP relay with the policy switched to "allow and record",
on a throwaway headless Edge profile (the test leg, never your browser), and exercises the core tool set on the
demo pages. Writes panini-core/capabilities/browser/playwright/cdp-methods-1.63.0.json; the policy contract test
checks that every recorded method is allowed (or handled by the relay) by the I9 policy.

    .venv\\Scripts\\python.exe scripts/cdp-spike.py
"""

import asyncio
import json
import sys
import tempfile
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend/tests/hands"))

from chrome_leg import DirectChromeLeg  # noqa: E402

from backend.hands import cdp_relay  # noqa: E402
from backend.hands.playwright_mcp import PlaywrightSession, chrome_preset, text_of  # noqa: E402

OUT = ROOT / "panini-core/capabilities/browser/playwright/cdp-methods-1.63.0.json"


async def spike():
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(ROOT / "frontend/public"))
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    leg = DirectChromeLeg()
    tab = await leg.start(f"{base}/browser-demo.html")
    original = cdp_relay.decide
    cdp_relay.decide = lambda method, params, scope=None: (True, "")  # Record everything Playwright asks for.
    relay = await cdp_relay.CdpRelay(leg, {"tabs": [tab], "origin": base, "allow_new_tabs": True}).start()
    leg.relay = relay
    session = PlaywrightSession(chrome_preset(relay, Path(tempfile.mkdtemp())))
    steps = [
        ("browser_snapshot", {}),
        ("browser_navigate", {"url": f"{base}/browser-demo.html"}),
        ("browser_type", {"element": "name", "target": "textbox", "text": "Ada", "submit": False}),
        ("browser_press_key", {"key": "Enter"}),
        ("browser_wait_for", {"text": "Hello, Ada"}),
        ("browser_verify_text_visible", {"text": "Hello, Ada! Browser task complete."}),
        ("browser_take_screenshot", {"scale": "css", "type": "jpeg"}),
        ("browser_tabs", {"action": "list"}),
        ("browser_navigate_back", {}),
        ("browser_tabs", {"action": "new", "url": f"{base}/browser-demo.html"}),
        ("browser_tabs", {"action": "close"}),
        ("browser_pdf_save", {}),
    ]
    log = []
    try:
        for tool, args in steps:
            if tool == "browser_type":
                snap = text_of(await session.call("browser_snapshot", {}))
                ref = next(
                    line.split("[ref=")[1].split("]")[0] for line in snap.splitlines() if "textbox" in line
                )
                args = {**args, "target": ref}
            try:
                result = await session.call(tool, args, timeout_s=60)
                log.append((tool, "error" if result.get("isError") else "ok"))
            except Exception as exc:  # noqa: BLE001 -- The spike records what fails too.
                log.append((tool, f"exception: {exc}"))
    finally:
        await session.close()
        await relay.close("spike done")
        cdp_relay.decide = original
        await leg.close()
        server.shutdown()
    return sorted(relay.methods), log


def main():
    methods, log = asyncio.run(spike())
    for tool, outcome in log:
        print(f"{tool:32} {outcome}")
    OUT.write_text(
        json.dumps({"playwright_core": "1.63.0", "methods": methods}, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {OUT.name}: {len(methods)} methods")


if __name__ == "__main__":
    main()
