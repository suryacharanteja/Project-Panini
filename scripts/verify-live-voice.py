"""Real OpenAI audio -> WebRTC -> extension actions -> spoken results in an isolated test app.

Imported by verify-extension.py with EXTENSION_TEST_VOICE=1. No user's microphone or
browser profile is used. Generated test speech is sent as a live browser media track.
"""

import asyncio
import base64
import json
import os
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from backend.comparison import get_openai_client

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "artifacts/voice-verification"


async def run(browser, popup, client, popup_state, until, click, base):
    OUT.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(ROOT / "frontend/test/fixtures"))
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    suffix = "?offscreen=1" if os.getenv("VOICE_TEST_ACK") else ""
    page = await browser.new_page(f"http://127.0.0.1:{server.server_port}/voice-jobs.html{suffix}")
    dashboard = None
    monitor = None
    try:
        state = await until(
            popup, popup_state, lambda s: any("PANINI voice jobs test" in t["text"] for t in s["tabs"])
        )
        tab = next(t for t in state["tabs"] if "PANINI voice jobs test" in t["text"])
        await popup.evaluate(
            "(id) => { const select = document.querySelector('#tabs'); select.value=id; select.dispatchEvent(new Event('change',{bubbles:true})); }",
            tab["id"],
        )
        await click(browser, popup, "#attach")
        await until(popup, popup_state, lambda s: "PANINI voice jobs test" in s["attached"])
        dashboard = await browser.new_page(base + "/browser")
        await until(
            dashboard,
            "() => ({ready: !!window.JevBrowser && !!document.querySelector('#live-start').onclick})",
            lambda s: s["ready"],
        )
        await dashboard.evaluate("""() => {
            const ctx=new AudioContext({sampleRate:24000}); const dst=ctx.createMediaStreamDestination();
            const silence=ctx.createConstantSource();silence.offset.value=0;silence.connect(dst);silence.start();
            window.voiceTest={ctx,dst};
            navigator.mediaDevices.getUserMedia=async()=>dst.stream;
            document.querySelector('#run-mode').value='extension';onModeChanged();
        }""")
        await until(dashboard, "() => ({ready: window.JevBrowser.canStartVoice()})", lambda s: s["ready"])
        await click(browser, dashboard, "#live-start")
        script = """() => ({state:document.querySelector('#live-panel').dataset.state,
            status:document.querySelector('#live-status').textContent,
            task:document.querySelector('#live-task').textContent,
            captions:document.querySelector('#live-captions').textContent,
            spoken:[...document.querySelectorAll('#live-captions .assistant')].map(e=>e.textContent).join(' '),
            results:[...document.querySelectorAll('#live-results li')].map(e=>({id:e.dataset.itemId,text:e.textContent})),
            actions:document.querySelector('#decision-actions').textContent})"""

        async def monitor_state():
            while True:
                OUT.joinpath("current-state.json").write_text(
                    str(await dashboard.evaluate(script)), encoding="utf-8"
                )
                await asyncio.sleep(3)

        monitor = asyncio.create_task(monitor_state())
        try:
            await until(dashboard, script, lambda s: s["state"] == "listening", timeout=60)
        except Exception:
            print(await dashboard.evaluate(script), flush=True)
            raise

        async def speak(text, name):
            audio = (
                (OUT / (name + ".wav")).read_bytes()
                if (OUT / (name + ".wav")).exists()
                else await asyncio.to_thread(
                    lambda: (
                        get_openai_client()
                        .audio.speech.create(
                            model="gpt-4o-mini-tts", voice="alloy", input=text, response_format="wav"
                        )
                        .content
                    )
                )
            )
            OUT.joinpath(name + ".wav").write_bytes(audio)
            await dashboard.evaluate(
                """(b64) => (async () => {
                const bytes=Uint8Array.from(atob(b64),c=>c.charCodeAt(0));
                await voiceTest.ctx.resume(); const buffer=await voiceTest.ctx.decodeAudioData(bytes.buffer);
                const source=voiceTest.ctx.createBufferSource();source.buffer=buffer;source.connect(voiceTest.dst);source.start();
            })()""",
                base64.b64encode(audio).decode(),
            )
            print("SPOKEN", text, flush=True)

        if os.getenv("VOICE_TEST_TARGET_ONLY"):
            await click(browser, popup, "#detach")
            ended = await until(dashboard, script, lambda s: s["state"] == "idle", timeout=20)
            assert "tab changed or disconnected" in ended["status"], ended
            health = (await client.get("/api/voice/status")).json()
            assert not health["active"]
            assert health["diagnostics"]["error_code"] == "session_target_or_connection_lost"
            print("PASS detaching a voice-owned tab closes audio with an actionable reason", flush=True)
            return

        async def job_workflow():
            await speak(
                "Hey Panini, search this job board for AI manager jobs and tell me the top five results.",
                "search",
            )
            if os.getenv("VOICE_TEST_ACK"):
                async with asyncio.timeout(45):
                    while (await client.get("/api/voice/status")).json().get("diagnostics", {}).get(
                        "runs", 0
                    ) < 1:
                        await asyncio.sleep(0.2)
                await speak("Mm, okay. Okay, that's fine. Yeah, okay.", "uat8-ack")
            result = await until(dashboard, script, lambda s: len(s["results"]) == 5, timeout=180)
            if os.getenv("VOICE_TEST_ACK"):
                diagnostics = (await client.get("/api/voice/status")).json()["diagnostics"]
                assert diagnostics["runs"] == 1, diagnostics
                print(
                    "PASS spoken acknowledgements preserve one run and return offscreen results", flush=True
                )
            OUT.joinpath("search.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
            print("PASS five verified jobs", flush=True)
            await until(
                dashboard,
                script,
                lambda s: "Acme" in s["captions"] and "Northstar" in s["captions"],
                timeout=35,
            )
            print("PASS actual spoken result transcript", flush=True)
            await speak("Apply to the first job at Acme. Ask me if any information is missing.", "apply")
            question = await until(
                dashboard,
                script,
                lambda s: "notice period" in s["captions"].lower(),
                timeout=150,
            )
            OUT.joinpath("question.json").write_text(json.dumps(question, indent=2), encoding="utf-8")
            print("PASS requested notice period", flush=True)
            await speak(
                "My notice period is thirty days. Fill that in and submit this test application.", "answer"
            )
            await until(
                page,
                "() => ({text:document.body.textContent})",
                lambda s: "Application submitted successfully" in s["text"],
                timeout=150,
            )
            final = await until(
                dashboard,
                script,
                lambda s: any(
                    word in s["captions"].lower()
                    for word in ("submitted", "went through", "confirmation code", "successfully sent")
                ),
                timeout=60,
            )
            OUT.joinpath("final.json").write_text(json.dumps(final, indent=2), encoding="utf-8")
            OUT.joinpath("conversation.png").write_bytes(base64.b64decode(await dashboard.screenshot()))
            print("PASS spoken follow-up submitted the local test form", flush=True)

        if not os.getenv("VOICE_TEST_CONTROLS_ONLY"):
            await job_workflow()
        await page.evaluate(
            "(url) => {location.href=url}", f"http://127.0.0.1:{server.server_port}/workflow.html?goal=30"
        )
        await until(page, "() => ({ready:document.body?.dataset.completed === '0'})", lambda s: s["ready"])
        await speak("Complete all thirty stages on this page by clicking Next stage.", "stages")
        await until(
            page,
            "() => ({count:Number(document.body.dataset.completed)})",
            lambda s: s["count"] >= 1,
            timeout=90,
        )
        await speak("Okay, just leave it, leave it.", "leave-it")
        stopped = await until(dashboard, script, lambda s: "stopped" in s["task"].lower(), timeout=30)
        count = await page.evaluate("() => document.body.dataset.completed")
        await asyncio.sleep(4)
        assert await page.evaluate("() => document.body.dataset.completed") == count
        assert stopped["state"] == "listening"
        print("PASS spoken stop prevents further browser actions and keeps audio open", flush=True)
        await speak("End the conversation. Goodbye.", "end")
        await until(dashboard, script, lambda s: s["state"] == "idle", timeout=40)
        assert not (await client.get("/api/voice/status")).json()["active"]
        print("PASS spoken end released session", flush=True)
        # Exercise the same voice UI against the other supported execution target.
        async with client.stream(
            "POST",
            "/api/browser/run-stream",
            json={
                "mode": "chromium",
                "instruction": f"Open {base}/browser-demo.html and stop once the demo form is visible.",
                "run_policy": "bounded",
                "max_steps": 5,
                "timeout_seconds": 90,
            },
        ) as response:
            response.raise_for_status()
            navigation = [json.loads(line) async for line in response.aiter_lines() if line.strip()]
        assert navigation[-1]["event"] == "complete", navigation[-1]
        await dashboard.evaluate("""() => {
            voiceTest.ctx.close();
            const ctx=new AudioContext({sampleRate:24000});const dst=ctx.createMediaStreamDestination();
            const silence=ctx.createConstantSource();silence.offset.value=0;silence.connect(dst);silence.start();
            window.voiceTest={ctx,dst};navigator.mediaDevices.getUserMedia=async()=>dst.stream;
            document.querySelector('#run-mode').value='chromium';onModeChanged();
        }""")
        await until(dashboard, "() => ({ready:window.JevBrowser.canStartVoice()})", lambda s: s["ready"])
        await click(browser, dashboard, "#live-start")
        await until(dashboard, script, lambda s: s["state"] == "listening", timeout=60)
        await speak(
            "On this demo page, enter PANINI in Your demo name, click Show greeting, and tell me the exact greeting.",
            "managed",
        )
        managed = await until(
            dashboard,
            script,
            lambda s: "hello, panini" in s["spoken"].lower(),
            timeout=150,
        )
        OUT.joinpath("managed.json").write_text(json.dumps(managed, indent=2), encoding="utf-8")
        assert (await client.get("/api/voice/status")).json()["diagnostics"]["actions"] >= 2
        print("PASS native live audio with managed Chromium", flush=True)
        await dashboard.evaluate("() => document.querySelector('#live-end').click()")
        await until(dashboard, script, lambda s: s["state"] == "idle", timeout=30)
    finally:
        if monitor:
            monitor.cancel()
            await asyncio.gather(monitor, return_exceptions=True)
        if dashboard:
            OUT.joinpath("last-state.json").write_text(
                str(await dashboard.evaluate(script)), encoding="utf-8"
            )
            print("LAST STATE", await dashboard.evaluate(script), flush=True)
            if not os.getenv("VOICE_TEST_TARGET_ONLY"):
                target = await dashboard.get_target_info()
                await browser.cdp_client.send.Target.activateTarget({"targetId": target["targetId"]})
                try:
                    frame = await asyncio.wait_for(dashboard.screenshot(), 10)
                    OUT.joinpath("last-view.png").write_bytes(base64.b64decode(frame))
                except TimeoutError:
                    print("Screenshot timed out; final DOM state retained", flush=True)
                await asyncio.wait_for(
                    dashboard.evaluate("() => document.querySelector('#live-end').click()"), 10
                )
        server.shutdown()
        server.server_close()
