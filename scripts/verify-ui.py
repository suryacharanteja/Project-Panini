"""Explicit live UI check using the app's installed browser runtime.

Run against the running local app. Search may consume configured provider credits.
Uses an isolated headless profile; never connects to the user's Chrome session.
"""

import asyncio
import base64
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

os.environ["ANONYMIZED_TELEMETRY"] = "false"

from browser_use import Browser

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "artifacts/ui-verification"
BASE = os.environ.get("APP_URL", "http://localhost:3000").rstrip("/")
STATE = """() => ({
  url: location.href,
  documentId: performance.timeOrigin,
  theme: document.documentElement.dataset.theme || '',
  dark: document.documentElement.classList.contains('dark'),
  background: document.body ? getComputedStyle(document.body).backgroundColor : '',
  sheets: document.styleSheets.length,
  resultLinks: document.querySelectorAll('main a[href^="https://"]').length
})"""


async def until(page, script, predicate, timeout=45):
    async with asyncio.timeout(timeout):
        while True:
            value = json.loads(await page.evaluate(script))
            if predicate(value):
                return value
            await asyncio.sleep(0.25)


async def main():
    address = urlsplit(BASE)
    if address.scheme != "http" or address.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("APP_URL must be a local HTTP app address")
    OUT.mkdir(parents=True, exist_ok=True)
    browser = Browser(
        headless=True,
        enable_default_extensions=False,
        viewport={"width": 1440, "height": 1000},
    )
    checks = []
    try:
        await browser.start()
        page = await browser.new_page(BASE + "/search/results?q=TypeSafe+Jev+API+documentation+and+examples")
        state = await until(page, STATE, lambda s: s["sheets"] > 0 and s["resultLinks"] > 0)
        checks.append({"check": "search renders", **state})
        for _ in range(2):
            button = (await page.get_elements_by_css_selector('button[title="Toggle light / dark mode"]'))[0]
            await button.click()
            current = await until(page, STATE, lambda s: s["dark"] != state["dark"], timeout=5)
            assert current["background"] != state["background"], "Theme did not repaint"
            checks.append({"check": "theme toggles", **current})
            screenshot = await asyncio.wait_for(page.screenshot(), timeout=15)
            OUT.joinpath("dark.png" if current["dark"] else "light.png").write_bytes(
                base64.b64decode(screenshot)
            )
            state = current
        await page.reload()
        current = await until(
            page,
            STATE,
            lambda s: s["documentId"] != state["documentId"] and s["sheets"] > 0 and bool(s["theme"]),
        )
        assert current["dark"] == state["dark"], "Theme preference did not survive reload"
        checks.append({"check": "theme persists", **current})

        await page.goto(BASE + "/browser")
        target_script = """() => ({
          ready: !!document.querySelector('#run-mode'),
          documentId: performance.timeOrigin,
          mode: document.querySelector('#run-mode')?.value,
          disabled: document.querySelector('#process-btn')?.disabled
        })"""
        initial = await until(page, target_script, lambda s: s["ready"] and s["disabled"])
        assert initial["mode"] == "", "Fresh profiles must explicitly choose a browser"
        previous = initial
        for mode in ("extension", "chromium"):
            await page.evaluate(
                """(mode) => {
                  const select = document.querySelector('#run-mode');
                  select.value = mode;
                  select.dispatchEvent(new Event('change', {bubbles:true}));
                }""",
                mode,
            )
            await page.reload()
            current = await until(
                page,
                target_script,
                lambda s: s["documentId"] != previous["documentId"] and s["mode"] == mode,
                timeout=5,
            )
            checks.append({"check": "browser choice persists", **current})
            previous = current
        OUT.joinpath("report.json").write_text(json.dumps(checks, indent=2), encoding="utf-8")
        print(json.dumps({"passed": True, "checks": len(checks), "evidence": str(OUT)}), flush=True)
    finally:
        await browser.kill()


asyncio.run(main())
