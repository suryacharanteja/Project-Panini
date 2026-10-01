"""Exercise the actual Windows broker/worker against our own disposable WPF fixture."""

import asyncio
import base64
import json
import os
import secrets
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


async def main():
    from backend.capabilities.desktop import DesktopPipe
    from backend.panini.contracts import Fault

    def executable(project):
        return (
            ROOT
            / "panini-core/capabilities/desktop"
            / project
            / "bin/Release/net10.0-windows"
            / (project + ".exe")
        )

    os.environ["PANINI_DESKTOP_PIPE"] = "panini-test-" + str(uuid4())
    os.environ["PANINI_DESKTOP_SECRET"] = secrets.token_urlsafe(32)
    os.environ["PANINI_DESKTOP_WORKER"] = str(executable("Panini.Desktop.Worker"))
    broker = subprocess.Popen(
        [str(executable("Panini.Desktop.Broker"))],
        creationflags=subprocess.CREATE_NO_WINDOW,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    fixture = subprocess.Popen(
        [str(executable("Panini.Desktop.Fixture"))], creationflags=subprocess.CREATE_NO_WINDOW
    )
    checks = []
    try:
        pipe = DesktopPipe()
        target = None
        for _ in range(30):
            await asyncio.sleep(0.2)
            try:
                windows = await pipe.call({"op": "targets"})
                target = next((t for t in windows["targets"] if t["pid"] == fixture.pid), None)
                if target:
                    break
            except (Fault, OSError):
                pass
        assert target, "The fixture window was not available in the interactive session."
        checks.append("enumeration and process identity")
        catalog = await pipe.call({"op": "applications"})
        assert any(row["application_id"] == "notepad" for row in catalog["applications"])
        assert all("path" not in row for row in catalog["applications"])
        checks.append("registered application discovery exposes IDs, not arbitrary executable paths")
        try:
            await pipe.call({"op": "launch", "application_id": "cmd.exe /c echo unauthorized"})
            raise AssertionError("An unregistered executable was accepted")
        except Fault as exc:
            assert exc.code == "APPLICATION_UNAVAILABLE"
        checks.append("unregistered application launch rejected")
        cancelled_launch = str(uuid4())
        await pipe.call({"op": "stop", "task_id": cancelled_launch})
        try:
            await pipe.call({"op": "launch", "task_id": cancelled_launch, "application_id": "notepad"})
            raise AssertionError("A cancelled task launched an application")
        except Fault as exc:
            assert exc.code == "UNKNOWN_EFFECT"
        checks.append("Stop fences queued application launch")

        async def observe():
            return await pipe.call({"op": "observe", "target": target})

        page = await observe()
        assert not any(e["automation_id"] == "password-fixture" for e in page["elements"])
        assert "fixture-secret" not in json.dumps(page)
        checks.append("password field and value excluded from observations")
        try:
            await pipe.call({"op": "observe", "target": {**target, "created": "0"}})
            raise AssertionError("Stale process identity was accepted")
        except Fault as exc:
            assert exc.code == "TARGET_CHANGED"
        checks.append("stale target rejection")
        if not page["foreground"]:
            host = subprocess.run(
                [str(executable("Panini.RuntimeHost")), "--focus", json.dumps(target)],
                capture_output=True,
                text=True,
                timeout=2,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            assert host.returncode == 0 and (await observe())["foreground"], "Fixture focus handoff failed"
        page = await observe()
        draft = next(e for e in page["elements"] if e["automation_id"] == "draft")
        action = {
            "kind": "set_value",
            "element_id": draft["element_id"],
            "text": "PANINI native verification café 漢字 🪷",
            "expected_name": draft["name"],
            "expected_automation_id": draft["automation_id"],
        }
        response = await pipe.call(
            {"op": "action", "target": target, "observed_at": page["observed_at"], "action": action}
        )
        assert response["verified"]
        page = await observe()
        assert any(e.get("value") == action["text"] for e in page["elements"])
        checks.append("UIA ValuePattern and exact readback")
        button = next(e for e in page["elements"] if e["automation_id"] == "apply")
        await pipe.call(
            {
                "op": "action",
                "target": target,
                "observed_at": page["observed_at"],
                "action": {
                    "kind": "invoke",
                    "element_id": button["element_id"],
                    "expected_name": button["name"],
                    "expected_automation_id": button["automation_id"],
                },
            }
        )
        assert any(e["name"] == "Applied: " + action["text"] for e in (await observe())["elements"])
        checks.append("UIA InvokePattern and exact postcondition")
        try:
            await pipe.call({"op": "observe", "target": {**target, "created": "0"}})
            raise AssertionError("Stale process identity was accepted")
        except Fault as exc:
            assert exc.code == "TARGET_CHANGED"
        checks.append("stale target rejection")
        try:
            await pipe.call({"op": "action", "target": target, "observed_at": 0, "action": action})
            raise AssertionError("Stale observation was accepted")
        except Fault as exc:
            assert exc.code == "OBSERVATION_EXPIRED"
        checks.append("stale observation rejection")
        try:
            await pipe.call(
                {"op": "action", "target": target, "observed_at": 9999999999999, "action": action}
            )
            raise AssertionError("Future observation was accepted")
        except Fault as exc:
            assert exc.code == "OBSERVATION_EXPIRED"
        checks.append("future observation rejection")

        async def semantic(kind, automation_id, text=""):
            page = await observe()
            element = next(e for e in page["elements"] if e["automation_id"] == automation_id)
            return await pipe.call(
                {
                    "op": "action",
                    "target": target,
                    "observed_at": page["observed_at"],
                    "action": {
                        "kind": kind,
                        "element_id": element["element_id"],
                        "expected_name": element["name"],
                        "expected_automation_id": automation_id,
                        "direction": "down",
                        "text": text,
                    },
                }
            )

        await semantic("toggle", "option")
        assert any(
            e["automation_id"] == "option" and e["value"] == "on" for e in (await observe())["elements"]
        )
        checks.append("UIA TogglePattern and state readback")
        await semantic("select", "choice-beta")
        assert any(
            e["automation_id"] == "choice-beta" and e["value"] == "selected"
            for e in (await observe())["elements"]
        )
        checks.append("UIA SelectionItemPattern and state readback")
        before = {e["name"] for e in (await observe())["elements"] if e["name"].startswith("Fixture row ")}
        await semantic("scroll", "fixture-scroll")
        after = {e["name"] for e in (await observe())["elements"] if e["name"].startswith("Fixture row ")}
        assert before != after, "Scroll did not change the visible rows"
        checks.append("UIA ScrollPattern and visible viewport change")
        await semantic("set_value", "draft", "")
        await semantic("click", "draft")
        await asyncio.sleep(0.15)
        await semantic("type_text", "draft", "Native Unicode café 漢字 🪷")
        for _ in range(10):
            if any(e.get("value") == "Native Unicode café 漢字 🪷" for e in (await observe())["elements"]):
                break
            await asyncio.sleep(0.1)
        else:
            raise AssertionError("Native keyboard input did not match exact Unicode text")
        await semantic("click", "apply")
        await asyncio.sleep(0.15)
        assert any(e["name"] == "Applied: Native Unicode café 漢字 🪷" for e in (await observe())["elements"])
        checks.append("grounded SendInput clicks and Unicode keyboard delivery with exact readback")
        try:
            await semantic("type_text", "draft", "must not reach an unfocused edit")
            raise AssertionError("Keyboard input accepted an unfocused edit")
        except Fault as exc:
            assert exc.code == "TEXT_TARGET_NOT_FOCUSED"
        checks.append("native keyboard input rejects unfocused edit")
        capture = await pipe.call({"op": "capture", "target": target})
        png = base64.b64decode(capture["image_base64"], validate=True)
        assert png.startswith(b"\x89PNG\r\n\x1a\n")
        assert 0 < capture["width"] <= 1280 and 0 < capture["height"] <= 1280
        assert len(png) <= 600000 and capture["method"] == "PrintWindow"
        (ROOT / "artifacts" / "panini-native-capture.png").write_bytes(png)
        try:
            await pipe.call({"op": "capture", "target": {**target, "created": "0"}})
            raise AssertionError("Capture accepted a stale target")
        except Fault as exc:
            assert exc.code == "TARGET_CHANGED"
        checks.append("selected-window PNG capture, bounded output and stale identity rejection")
        combined = await pipe.call({"op": "state", "target": target})
        assert combined["snapshot_kind"] == "uia_plus_window_png"
        assert combined["observation"]["elements"] and combined["capture"]["image_base64"]
        checks.append("combined semantic-plus-visual Windows state observation")
        (ROOT / "artifacts" / "panini-native-verification.json").write_text(
            json.dumps({"passed": checks}, indent=2), encoding="utf-8"
        )
        print(json.dumps({"passed": checks}))
    finally:
        fixture.terminate()
        broker.terminate()
        fixture.wait(timeout=5)
        broker.wait(timeout=5)


if __name__ == "__main__":
    asyncio.run(main())
