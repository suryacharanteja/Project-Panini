"""Live extension UAT in an isolated Chromium profile against a separate local app.

Start a test app with FRONTEND_PORT=3200 BACKEND_PORT=8200, then run this script.
EXTENSION_TEST_BROWSER must name a Chromium/Chrome for Testing executable that
supports --load-extension. This test consumes configured Jev API credits.
It never connects to the user's browser or their app's extension connection.
"""

import asyncio
import base64
import importlib.util
import json
import os
import shutil
import tempfile
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

os.environ["ANONYMIZED_TELEMETRY"] = "false"

import httpx
from browser_use import Browser

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "artifacts/extension-verification"
BASE = os.environ.get("EXTENSION_TEST_URL", "http://127.0.0.1:3200").rstrip("/")


async def until(page, script, predicate, timeout=20):
    async with asyncio.timeout(timeout):
        while True:
            value = json.loads(await page.evaluate(script))
            if predicate(value):
                return value
            await asyncio.sleep(0.2)


async def click(browser, page, selector):
    target = await page.get_target_info()
    await browser.cdp_client.send.Target.activateTarget({"targetId": target["targetId"]})
    elements = await page.get_elements_by_css_selector(selector)
    assert elements, f"Missing control: {selector}"
    await asyncio.wait_for(elements[0].click(), timeout=15)


async def screenshot(browser, page, filename):
    target = await page.get_target_info()
    await browser.cdp_client.send.Target.activateTarget({"targetId": target["targetId"]})
    data = await asyncio.wait_for(page.screenshot(), timeout=15)
    OUT.joinpath(filename).write_bytes(base64.b64decode(data))


async def continuous_uat(browser, popup, client, popup_state):
    """Run more than ten real extension actions, then stop a subsequent task."""

    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(QuietHandler, directory=str(ROOT / "frontend/test/fixtures"))
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/workflow.html"
        page = await browser.new_page(url)
        state = await until(
            popup, popup_state, lambda s: any("Continuous workflow test" in t["text"] for t in s["tabs"])
        )
        tab = next(t for t in state["tabs"] if "Continuous workflow test" in t["text"])
        await popup.evaluate(
            "(id) => { const select = document.querySelector('#tabs'); select.value = id; select.dispatchEvent(new Event('change', {bubbles:true})); }",
            tab["id"],
        )
        await click(browser, popup, "#attach")
        await until(popup, popup_state, lambda s: "Continuous workflow test" in s["attached"])
        dashboard = await browser.new_page(BASE + "/browser")
        await until(
            dashboard,
            "() => ({ready: typeof document.querySelector('#process-btn')?.onclick === 'function'})",
            lambda s: s["ready"],
        )
        assert await dashboard.evaluate("() => document.querySelector('#run-policy').value") == "continuous"
        await dashboard.evaluate(
            "() => { const select = document.querySelector('#run-mode'); select.value = 'extension'; select.dispatchEvent(new Event('change', {bubbles:true})); document.querySelector('#voice-input').value = 'Complete all 12 stages by clicking Next stage until all stages are completed.'; }"
        )
        await until(
            dashboard,
            "() => ({ready: !document.querySelector('#process-btn').disabled})",
            lambda s: s["ready"],
        )
        await dashboard.evaluate("() => document.querySelector('#process-btn').click()")
        progress_script = """() => ({status: document.querySelector('#status-text').textContent,
            steps: Number(document.querySelector('#stat-actions').textContent),
            actions: [...document.querySelectorAll('.action-row')].map(el => el.textContent)})"""
        ui = await until(
            dashboard,
            progress_script,
            lambda s: s["status"].startswith(("complete:", "incomplete:", "error:", "Error:", "timeout:")),
            timeout=240,
        )
        OUT.joinpath("continuous-dashboard.json").write_text(json.dumps(ui, indent=2), encoding="utf-8")
        await screenshot(browser, dashboard, "continuous-dashboard.png")
        assert ui["status"].startswith("complete:"), ui
        assert ui["steps"] == 12, ui
        assert await page.evaluate("() => document.body.dataset.completed") == "12"
        await screenshot(browser, page, "continuous-completed.png")
        print("PASS continuous run completed 12 real extension actions", flush=True)

        await page.evaluate("(url) => { location.href = url; }", url + "?goal=30")
        await until(page, "() => ({ready: document.body.dataset.completed === '0'})", lambda s: s["ready"])
        await dashboard.evaluate(
            "() => { document.querySelector('#voice-input').value = 'Complete all 30 stages by clicking Next stage until all stages are completed.'; document.querySelector('#process-btn').click(); }"
        )
        await until(dashboard, progress_script, lambda s: s["steps"] >= 1, timeout=60)
        await dashboard.evaluate("() => document.querySelector('#stop-btn').click()")
        stopped = await until(dashboard, progress_script, lambda s: s["status"] == "Instruction stopped")
        before = await page.evaluate("() => document.body.dataset.completed")
        await asyncio.sleep(1)
        assert before == await page.evaluate("() => document.body.dataset.completed"), (
            "An action executed after Stop"
        )
        assert not (await client.get("/api/browser/status?mode=extension")).json()["active"]
        print("PASS Stop cancelled continuous work and released the run", flush=True)
        return [
            {"check": "continuous task completed beyond ten actions", "actions": 12},
            {"check": "Stop cancelled next task with no later page changes", "state": stopped},
        ]
    finally:
        server.shutdown()
        server.server_close()


async def search_uat(browser, popup, client, popup_state):
    """Same unquoted user request against a realistic fixture and optional real Google."""

    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(QuietHandler, directory=str(ROOT / "frontend/test/fixtures"))
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    targets = [("fixture", f"http://127.0.0.1:{server.server_port}/search.html")]
    if os.environ.get("EXTENSION_TEST_GOOGLE") == "1":
        targets.append(("google", "https://www.google.com/?hl=en"))
    checks = []
    try:
        for name, url in targets:
            page = await browser.new_page(url)
            await until(
                page,
                "() => ({ready: document.readyState === 'complete', title: document.title})",
                lambda s: s["ready"] and bool(s["title"]),
                timeout=30,
            )
            title = await page.get_title()
            state = await until(popup, popup_state, lambda s: any(title in t["text"] for t in s["tabs"]))
            tab = next(t for t in state["tabs"] if title in t["text"])
            await popup.evaluate(
                """(id) => {
                const select = document.querySelector('#tabs'); select.value = id;
                select.dispatchEvent(new Event('change', {bubbles:true}));
            }""",
                tab["id"],
            )
            await click(browser, popup, "#attach")
            await until(popup, popup_state, lambda s: title in s.get("attached", ""))
            if name == "fixture":
                snapshot = json.loads(
                    await popup.evaluate(
                        "(id) => chrome.tabs.sendMessage(Number(id), {kind:'snapshot'})", tab["id"]
                    )
                )
                field = next(a for a in snapshot["actions"] if a["kind"] == "fill")
                # User edits invalidate the chosen field, even if its ID is unchanged.
                await page.evaluate("() => { document.querySelector('#query').value = 'user edit'; }")
                refused = json.loads(
                    await popup.evaluate(
                        "(request) => chrome.tabs.sendMessage(request.tab, request.action)",
                        {
                            "tab": int(tab["id"]),
                            "action": {
                                "kind": "act",
                                "id": field["id"],
                                "op": "TYPE_TEXT",
                                "text": "guard probe",
                                "marker": snapshot["marker"],
                            },
                        },
                    )
                )
                assert "changed" in refused.get("error", ""), refused
                snapshot = json.loads(
                    await popup.evaluate(
                        "(id) => chrome.tabs.sendMessage(Number(id), {kind:'snapshot'})", tab["id"]
                    )
                )
                # An unrelated widget changing must not invalidate a still-identical field.
                await page.evaluate(
                    "() => { document.querySelector('#navigation a').textContent = 'Updated widget'; }"
                )
                accepted = json.loads(
                    await popup.evaluate(
                        "(request) => chrome.tabs.sendMessage(request.tab, request.action)",
                        {
                            "tab": int(tab["id"]),
                            "action": {
                                "kind": "act",
                                "id": field["id"],
                                "op": "TYPE_TEXT",
                                "text": "guard probe",
                                "marker": snapshot["marker"],
                            },
                        },
                    )
                )
                assert accepted.get("ok") == "typed", accepted
                await page.evaluate(
                    "() => { const input = document.querySelector('#query'); input.value = ''; input.dispatchEvent(new Event('input', {bubbles:true})); }"
                )
                checks.append({"check": "changed field rejected; unrelated widget updates accepted"})
            current = (await client.get("/api/browser/extension/status")).json()
            response = await client.post(
                "/api/browser/run-stream",
                json={
                    "mode": "extension",
                    "target_id": current["target_id"],
                    "instruction": "open AI jobs search",
                    "max_steps": 8,
                    "timeout_seconds": 120,
                },
            )
            response.raise_for_status()
            events = [json.loads(line) for line in response.text.splitlines() if line]
            OUT.joinpath(f"{name}-search-events.json").write_text(
                json.dumps(events, indent=2), encoding="utf-8"
            )
            result = json.loads(
                await page.evaluate("""() => ({
                url: location.href, title: document.title,
                query: document.querySelector('textarea[name=q],input[name=q]')?.value,
                text: document.body.innerText.slice(0, 10000),
                links: document.querySelectorAll('#results a, #search a h3').length
            })""")
            )
            OUT.joinpath(f"{name}-search-page.json").write_text(
                json.dumps(result, indent=2), encoding="utf-8"
            )
            await screenshot(browser, page, f"{name}-search.png")
            if name == "google" and events[-1].get("requires_user"):
                assert any(
                    e.get("success")
                    and (e.get("params", {}).get("text") == "AI jobs" or e.get("action") == "navigate")
                    for e in events
                ), "Search did not reach Google"
                checks.append(
                    {
                        "check": "Google search submitted; verification requires the user",
                        "outcome": "requires_user",
                        "results_verified": False,
                    }
                )
                print("Google submitted search; human verification blocks result validation", flush=True)
                continue
            assert events[-1]["event"] == "complete", events[-1]
            query = (result.get("query") or "").lower()
            assert "ai" in query and "jobs" in query, result
            assert result["links"] >= 1, f"No actual search result links: {result}"
            checks.append(
                {
                    "check": f"{name}: unquoted search submitted and result links visible",
                    "query": result["query"],
                    "links": result["links"],
                    "url": result["url"],
                }
            )
            print(f"PASS {name} natural-language search", flush=True)
    finally:
        server.shutdown()
        server.server_close()
    return checks


async def main():
    address = urlsplit(BASE)
    if (
        address.scheme != "http"
        or address.hostname not in {"localhost", "127.0.0.1"}
        or address.port in {None, 3000, 3100}
    ):
        raise ValueError("Use a separate local test app, not the user's running app")
    binary = Path(os.environ["EXTENSION_TEST_BROWSER"]).resolve(strict=True)
    OUT.mkdir(parents=True, exist_ok=True)
    checks = []
    async with httpx.AsyncClient(base_url=BASE, timeout=150) as client:
        current = (await client.get("/api/browser/extension/status")).json()
        assert not current["connected"], "Test app already has an extension connected"
        with tempfile.TemporaryDirectory(prefix="runtime-", dir=OUT) as workspace:
            extension = Path(workspace) / "extension"
            shutil.copytree(ROOT / "panini-core" / "capabilities" / "browser" / "extension", extension)
            # Only the relay address differs from the shipping extension.
            background = extension / "background.js"
            background.write_text(
                background.read_text(encoding="utf-8").replace(
                    "ws://127.0.0.1:3000/ws/extension", BASE.replace("http:", "ws:") + "/ws/extension"
                ),
                encoding="utf-8",
            )
            if os.environ.get("EXTENSION_TEST_WORKSPACE") == "1":
                # Headless Chromium cannot display the native optional-permission dialog.
                # Pre-grant ONLY in this disposable test copy; shipping permissions stay optional.
                manifest_path = extension / "manifest.json"
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifest["permissions"].append("tabGroups")
                manifest["optional_permissions"].remove("tabGroups")
                manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            browser = Browser(
                headless=True,
                enable_default_extensions=False,
                executable_path=str(binary),
                args=[f"--load-extension={extension}", f"--disable-extensions-except={extension}"],
                viewport={"width": 1280, "height": 900},
            )
            try:
                await browser.start()
                demo = await browser.new_page(BASE + "/browser-demo.html")
                await until(
                    demo, "() => ({ready: !!document.querySelector('#demo-name')})", lambda s: s["ready"]
                )
                await browser.new_page("chrome://extensions/")
                async with asyncio.timeout(20):
                    while True:
                        targets = (await browser.cdp_client.send.Target.getTargets({"filter": [{}]}))[
                            "targetInfos"
                        ]
                        OUT.joinpath("targets.json").write_text(
                            json.dumps(targets, indent=2), encoding="utf-8"
                        )
                        workers = [
                            t
                            for t in targets
                            if t["url"].startswith("chrome-extension://") and "/background.js" in t["url"]
                        ]
                        if workers:
                            break
                        await asyncio.sleep(0.2)
                popup = await browser.new_page(workers[0]["url"].rsplit("/", 1)[0] + "/popup.html")
                popup_state = """() => ({
                    status: document.querySelector('#status')?.textContent,
                    hint: document.querySelector('#hint')?.textContent,
                    error: document.querySelector('#error')?.textContent,
                    attached: document.querySelector('#tab')?.textContent,
                    tabs: Array.from(document.querySelector('#tabs')?.options || [], o => ({id: o.value, text: o.textContent})),
                    attachDisabled: document.querySelector('#attach')?.disabled
                })"""
                state = await until(
                    popup,
                    popup_state,
                    lambda s: s.get("status") == "Connected to JevRev" and len(s["tabs"]) > 1,
                )
                assert "cannot be attached" in state["hint"]
                assert not any("chrome://" in t["text"] for t in state["tabs"])
                tab = next(t for t in state["tabs"] if "Jev browser demo" in t["text"])
                await popup.evaluate(
                    """(id) => {
                    const select = document.querySelector('#tabs');
                    select.value = id;
                    select.dispatchEvent(new Event('change', {bubbles: true}));
                }""",
                    tab["id"],
                )
                await click(browser, popup, "#attach")
                state = await until(popup, popup_state, lambda s: s["attached"].startswith("Attached:"))
                assert not state["error"], state
                await screenshot(browser, popup, "attached-popup.png")
                checks.append({"check": "website chosen from restricted page and attached", "state": state})
                print("PASS actual popup attachment", flush=True)

                if os.environ.get("EXTENSION_TEST_WORKSPACE") == "1":
                    spec = importlib.util.spec_from_file_location(
                        "workspace_uat", ROOT / "scripts/verify-workspace.py"
                    )
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                    await module.run(browser, popup, client, until, click, BASE, OUT)
                    return

                if os.environ.get("EXTENSION_TEST_VOICE") == "1":
                    spec = importlib.util.spec_from_file_location(
                        "voice_uat", ROOT / "scripts/verify-live-voice.py"
                    )
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                    await module.run(browser, popup, client, popup_state, until, click, BASE)
                    return

                if os.environ.get("EXTENSION_TEST_CONTINUOUS") == "1":
                    checks.extend(await continuous_uat(browser, popup, client, popup_state))
                    OUT.joinpath("continuous-report.json").write_text(
                        json.dumps(checks, indent=2), encoding="utf-8"
                    )
                    print(
                        json.dumps({"passed": True, "checks": len(checks), "evidence": str(OUT)}), flush=True
                    )
                    return

                if os.environ.get("EXTENSION_TEST_JOBS") == "1":
                    server = ThreadingHTTPServer(
                        ("127.0.0.1", 0),
                        partial(SimpleHTTPRequestHandler, directory=str(ROOT / "frontend/test/fixtures")),
                    )
                    threading.Thread(target=server.serve_forever, daemon=True).start()
                    try:
                        suffix = "?shadow=1" if os.environ.get("EXTENSION_TEST_SHADOW") == "1" else ""
                        page = await browser.new_page(
                            f"http://127.0.0.1:{server.server_port}/jobs.html{suffix}"
                        )
                        state = await until(
                            popup,
                            popup_state,
                            lambda s: any("Job results fixture" in t["text"] for t in s["tabs"]),
                        )
                        tab = next(t for t in state["tabs"] if "Job results fixture" in t["text"])
                        await popup.evaluate(
                            "(id) => { const select = document.querySelector('#tabs'); select.value = id; select.dispatchEvent(new Event('change', {bubbles:true})); }",
                            tab["id"],
                        )
                        await click(browser, popup, "#attach")
                        await until(popup, popup_state, lambda s: "Job results fixture" in s["attached"])
                        observed = json.loads(
                            await popup.evaluate(
                                "(id) => chrome.tabs.sendMessage(Number(id), {kind:'snapshot'})", tab["id"]
                            )
                        )
                        assert (
                            observed["content_version"]
                            == json.loads(
                                (
                                    ROOT
                                    / "panini"
                                    / "capabilities"
                                    / "browser"
                                    / "extension"
                                    / "manifest.json"
                                ).read_text()
                            )["version"]
                        )
                        assert any(
                            "search" in a["label"].lower() and a["kind"] == "click"
                            for a in observed["actions"]
                        ), observed
                        attached = (await client.get("/api/browser/extension/status")).json()
                        events = []
                        async with client.stream(
                            "POST",
                            "/api/browser/run-stream",
                            json={
                                "mode": "extension",
                                "target_id": attached["target_id"],
                                "run_policy": "continuous",
                                "instruction": "Search job listings for AI manager on this site.",
                            },
                        ) as response:
                            response.raise_for_status()
                            async for line in response.aiter_lines():
                                if line.strip():
                                    event = json.loads(line)
                                    events.append(event)
                                    print(
                                        event.get("event"),
                                        event.get("message") or event.get("error"),
                                        flush=True,
                                    )
                        result = json.loads(
                            await page.evaluate(
                                "() => ({url: location.href, text: document.querySelector('#results').innerText, query: (document.querySelector('#query') || document.querySelector('#shadow-host')?.shadowRoot.querySelector('#query')).value})"
                            )
                        )
                        diagnostic = (await client.get("/api/browser/status?mode=extension")).json()["stats"][
                            "last_run"
                        ]
                        OUT.joinpath("jobs-report.json").write_text(
                            json.dumps(
                                {"events": events, "result": result, "diagnostic": diagnostic}, indent=2
                            ),
                            encoding="utf-8",
                        )
                        assert events[-1]["event"] == "complete", events[-1]
                        assert result["query"].lower() == "ai manager", result
                        assert "keywords=AI%20manager" in result["url"], result
                        assert any(e.get("params", {}).get("kind") == "press_enter" for e in events), events
                        assert len([e for e in events if e["event"] == "step"]) >= 3
                        await screenshot(browser, page, "jobs-results.png")
                        print(
                            "PASS hidden icon search, exact query, formless Enter and verified completion",
                            flush=True,
                        )
                    finally:
                        server.shutdown()
                        server.server_close()
                    return

                current = (await client.get("/api/browser/extension/status")).json()
                assert current["attached_tab"] == int(tab["id"]) and current["target_id"]
                dashboard = await browser.new_page(BASE + "/browser")
                await until(
                    dashboard, "() => ({ready: !!document.querySelector('#run-mode')})", lambda s: s["ready"]
                )
                await dashboard.evaluate("""() => {
                    const select = document.querySelector('#run-mode');
                    select.value = 'extension';
                    select.dispatchEvent(new Event('change', {bubbles: true}));
                    document.querySelector('#voice-input').value = 'Type "Jev" into Your demo name and click Show greeting.';
                }""")
                await until(
                    dashboard,
                    "() => ({ready: !document.querySelector('#process-btn').disabled})",
                    lambda s: s["ready"],
                )
                # Exercise the app's actual bound handler. The dashboard button
                # sits below the viewport; headless CDP mouse scrolling is flaky.
                await dashboard.evaluate("() => document.querySelector('#process-btn').click()")
                ui = await until(
                    dashboard,
                    """() => ({
                    status: document.querySelector('#status-text').textContent,
                    steps: document.querySelector('#stat-actions').textContent,
                    target: document.querySelector('#extension-state').textContent,
                    actions: Array.from(document.querySelectorAll('#decision-actions > .action-row'), el => el.textContent)
                })""",
                    lambda s: s["status"].startswith(
                        ("complete:", "incomplete:", "error:", "Error:", "timeout:")
                    ),
                    timeout=130,
                )
                OUT.joinpath("dashboard-events.json").write_text(json.dumps(ui, indent=2), encoding="utf-8")
                assert ui["status"].startswith("complete:"), ui
                assert int(ui["steps"]) >= 2
                await screenshot(browser, dashboard, "completed-dashboard.png")
                state = await until(
                    demo,
                    """() => ({name: document.querySelector('#demo-name').value, greeting: document.querySelector('#greeting').textContent})""",
                    lambda s: "Hello, Jev!" in s["greeting"],
                    timeout=5,
                )
                await screenshot(browser, demo, "completed-demo.png")
                checks.append(
                    {
                        "check": "real Jev instruction changes attached page",
                        "state": state,
                        "dashboard": ui,
                    }
                )
                print("PASS actual page typed and clicked through Jev", flush=True)

                await click(browser, popup, "#reconnect")
                await until(popup, popup_state, lambda s: s.get("status") == "Connected to JevRev")
                async with asyncio.timeout(10):
                    while True:
                        renewed = (await client.get("/api/browser/extension/status")).json()
                        if renewed["connected"] and renewed["target_id"] != current["target_id"]:
                            break
                        await asyncio.sleep(0.2)
                assert renewed["attached_tab"] == current["attached_tab"]
                response = await client.post(
                    "/api/browser/run-stream",
                    json={
                        "mode": "extension",
                        "target_id": current["target_id"],
                        "instruction": "click Show greeting",
                    },
                )
                assert response.status_code == 409
                current = renewed
                checks.append({"check": "reconnect retains selected tab and invalidates old runs"})

                second = await browser.new_page(BASE + "/browser-demo.html?target=second")
                await until(
                    second, "() => ({ready: !!document.querySelector('#demo-name')})", lambda s: s["ready"]
                )
                await second.evaluate("() => { document.title = 'Second test website'; }")
                state = await until(
                    popup, popup_state, lambda s: any("Second test website" in t["text"] for t in s["tabs"])
                )
                second_tab = next(t for t in state["tabs"] if "Second test website" in t["text"])
                await popup.evaluate(
                    """(id) => {
                    const select = document.querySelector('#tabs');
                    select.value = id;
                    select.dispatchEvent(new Event('change', {bubbles: true}));
                }""",
                    second_tab["id"],
                )
                await click(browser, popup, "#attach")
                await until(popup, popup_state, lambda s: "Second test website" in s["attached"])
                response = await client.post(
                    "/api/browser/run-stream",
                    json={
                        "mode": "extension",
                        "target_id": current["target_id"],
                        "instruction": "click Show greeting",
                    },
                )
                assert response.status_code == 409
                current = (await client.get("/api/browser/extension/status")).json()
                assert current["attached_tab"] == int(second_tab["id"])
                response = await client.post(
                    "/api/browser/run-stream",
                    json={
                        "mode": "extension",
                        "target_id": current["target_id"],
                        "url": BASE + "/browser-demo.html?after=navigation",
                        "instruction": 'Type "Second" into Your demo name and click Show greeting.',
                        "max_steps": 6,
                        "timeout_seconds": 120,
                    },
                )
                response.raise_for_status()
                second_events = [json.loads(line) for line in response.text.splitlines() if line]
                OUT.joinpath("switched-events.json").write_text(
                    json.dumps(second_events, indent=2), encoding="utf-8"
                )
                OUT.joinpath("navigation-document.json").write_text(
                    await second.evaluate(
                        "() => ({url: location.href, title: document.title, ready: document.readyState, html: document.documentElement.outerHTML.slice(0, 16000)})"
                    ),
                    encoding="utf-8",
                )
                second_state = await until(
                    second,
                    "() => ({greeting: document.querySelector('#greeting')?.textContent})",
                    lambda s: "Hello, Second!" in s.get("greeting", ""),
                    timeout=5,
                )
                original = json.loads(
                    await demo.evaluate("() => ({name: document.querySelector('#demo-name').value})")
                )
                assert original["name"] == "Jev", "Switching modified the previous tab"
                checks.append(
                    {"check": "switch and navigation affect only newly attached tab", "state": second_state}
                )
                print("PASS reconnect, switch and navigation", flush=True)

                await click(browser, popup, "#detach")
                await until(popup, popup_state, lambda s: s["attached"] == "No tab attached yet.")
                response = await client.post(
                    "/api/browser/run-stream",
                    json={
                        "mode": "extension",
                        "target_id": current["target_id"],
                        "instruction": "click Show greeting",
                    },
                )
                assert response.status_code == 409
                checks.append(
                    {"check": "detached tab rejects stale instruction", "status": response.status_code}
                )
                if os.environ.get("EXTENSION_TEST_SEARCH") == "1":
                    checks.extend(await search_uat(browser, popup, client, popup_state))
                OUT.joinpath("report.json").write_text(json.dumps(checks, indent=2), encoding="utf-8")
                print(json.dumps({"passed": True, "checks": len(checks), "evidence": str(OUT)}), flush=True)
            finally:
                await browser.kill()


if __name__ == "__main__":
    asyncio.run(main())
