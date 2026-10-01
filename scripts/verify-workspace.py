"""Real Chrome workspace UAT; invoked by verify-extension.py in its isolated profile."""

import json
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


async def run(browser, popup, client, until, click, base, out):
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(ROOT / "frontend/test/fixtures"))
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    root = f"http://127.0.0.1:{server.server_port}/workspace.html"
    state_script = "() => chrome.runtime.sendMessage({kind: 'state'})"
    try:
        alpha = await browser.new_page(root + "?name=Alpha")
        beta = await browser.new_page(root + "?name=Beta")
        outsider = await browser.new_page(root + "?name=Outside")
        state = await until(
            popup, state_script, lambda s: len([t for t in s["tabs"] if "Workspace " in t["title"]]) == 3
        )
        ids = {t["title"]: t["id"] for t in state["tabs"] if t["title"].startswith("Workspace ")}
        await popup.evaluate("""() => {
          const mode = document.querySelector('#mode'); mode.value = 'workspace';
          mode.dispatchEvent(new Event('change', {bubbles:true}));
        }""")
        await until(
            popup,
            "() => ({count:document.querySelector('#workspace-tabs').options.length})",
            lambda s: s["count"] >= 3,
        )
        await popup.evaluate(
            """(ids) => {
          for (const option of document.querySelector('#workspace-tabs').options) option.selected = ids.includes(Number(option.value));
          document.querySelector('#allow-new').checked = true;
        }""",
            [ids["Workspace Alpha"], ids["Workspace Beta"]],
        )
        await click(browser, popup, "#workspace-save")
        state = await until(popup, state_script, lambda s: bool(s.get("workspace")))
        assert not (await popup.evaluate("() => document.querySelector('#error').textContent"))
        assert len(state["workspace"]["tabs"]) == 2
        group = json.loads(
            await popup.evaluate("(id) => chrome.tabGroups.get(id)", state["workspace"]["group_id"])
        )
        assert group["title"] == "JevRev" and group["color"] == "yellow"
        current = (await client.get("/api/browser/extension/status")).json()
        target = current["target_id"]
        assert current["workspace"] and current["compatible"]
        events = []
        instruction = (
            f"In the approved workspace, switch to Workspace Beta and click Mark reviewed. "
            f"Then open {root}?name=Gamma in a NEW workspace tab and click Mark reviewed there. "
            "Finally switch back to Workspace Alpha and click Finish workspace review. "
            "Do not navigate an existing tab to Gamma or change any other tab."
        )
        async with client.stream(
            "POST",
            "/api/browser/run-stream",
            json={
                "mode": "extension",
                "target_id": target,
                "instruction": instruction,
                "run_policy": "bounded",
                "max_steps": 15,
                "timeout_seconds": 150,
            },
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if line.strip():
                    event = json.loads(line)
                    events.append(event)
                    print(event.get("event"), event.get("message") or event.get("error"), flush=True)
        out.joinpath("workspace-events.json").write_text(json.dumps(events, indent=2), encoding="utf-8")
        assert events[-1]["event"] == "complete", events[-1]
        assert await alpha.evaluate("() => document.body.dataset.finished") == "yes"
        assert await beta.evaluate("() => document.body.dataset.reviewed") == "yes"
        assert await outsider.evaluate("() => document.body.dataset.reviewed || ''") == ""
        state = await until(popup, state_script, lambda s: len(s.get("workspace", {}).get("tabs", [])) == 3)
        gamma = next(t for t in state["workspace"]["tabs"] if "Gamma" in t["url"])
        gamma_result = json.loads(
            await popup.evaluate(
                "(id) => chrome.scripting.executeScript({target:{tabId:id},func:()=>document.body.dataset.reviewed})",
                gamma["id"],
            )
        )
        assert gamma_result[0]["result"] == "yes"
        current = (await client.get("/api/browser/extension/status")).json()
        assert current["target_id"] == target and current["attached_tab"] == ids["Workspace Alpha"]
        observation = (await client.get("/api/browser/status?mode=extension")).json()["stats"]["last_run"][
            "observation"
        ]
        assert len(observation["visited_pages"]) == 3
        print(
            "PASS real planner + Jev: switch, act, open, act, return, finish; unrelated tab untouched",
            flush=True,
        )
        dashboard = await browser.new_page(base + "/browser")
        await until(dashboard, "() => ({ready: !!document.querySelector('#run-mode')})", lambda s: s["ready"])
        await dashboard.evaluate(
            "() => { const s=document.querySelector('#run-mode');s.value='extension';s.dispatchEvent(new Event('change',{bubbles:true})); }"
        )
        await until(
            dashboard,
            "() => ({count:document.querySelectorAll('#workspace-state li').length, ready: !document.querySelector('#process-btn').disabled})",
            lambda s: s["count"] == 3 and s["ready"],
        )
        for page, name in [(dashboard, "workspace-dashboard.png"), (popup, "workspace-popup.png")]:
            import base64

            target_info = await page.get_target_info()
            await browser.cdp_client.send.Target.activateTarget({"targetId": target_info["targetId"]})
            out.joinpath(name).write_bytes(base64.b64decode(await page.screenshot()))
        # Dragging an unrelated tab into the visible group does not authorize it.
        await popup.evaluate(
            "(v) => chrome.tabs.group({tabIds:[v.id],groupId:v.group})",
            {"id": ids["Workspace Outside"], "group": state["workspace"]["group_id"]},
        )
        state = json.loads(await popup.evaluate(state_script))
        assert ids["Workspace Outside"] not in [t["id"] for t in state["workspace"]["tabs"]]
        # Moving out revokes the task grant; queued callers must get a conflict.
        await popup.evaluate("(id) => chrome.tabs.ungroup([id])", ids["Workspace Beta"])
        await until(popup, state_script, lambda s: len(s["workspace"]["tabs"]) == 2)
        current = (await client.get("/api/browser/extension/status")).json()
        assert current["target_id"] != target
        response = await client.post(
            "/api/browser/run-stream",
            json={"mode": "extension", "target_id": target, "instruction": "Click Mark reviewed"},
        )
        assert response.status_code == 409
        await popup.evaluate("() => chrome.runtime.sendMessage({kind:'workspaceDetach'})")
        state = await until(popup, state_script, lambda s: s["workspace"] is None and s["tab"] is None)
        assert all(id in [t["id"] for t in state["tabs"]] for id in ids.values())
        out.joinpath("workspace-report.json").write_text(
            json.dumps(
                {
                    "passed": True,
                    "checks": [
                        "real multi-tab planner execution",
                        "group title and color",
                        "opt-in new tab",
                        "stable task ownership",
                        "cross-tab observations",
                        "dashboard workspace status",
                        "unapproved grouped tab ignored",
                        "moved tab revoked",
                        "stale task rejected",
                        "detach leaves pages open",
                    ],
                    "permission_dialog": "pre-granted in isolated headless test copy",
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        print("PASS workspace lifecycle, revocation, dashboard, and detach", flush=True)
    finally:
        server.shutdown()
        server.server_close()
