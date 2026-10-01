"""Real Windows input against a disposable fixture. No model or provider calls."""

import asyncio
import base64
import ctypes
import json
import os
import re
import secrets
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))


async def main():
    from backend.capabilities.desktop import DesktopPipe
    from backend.panini.contracts import Fault

    def executable(name):
        return (
            ROOT / "panini-core/capabilities/desktop" / name / "bin/Release/net10.0-windows" / (name + ".exe")
        )

    report_dir = ROOT / (
        "artifacts/desktop-graphical-live" if "--planner-only" in sys.argv else "artifacts/desktop-graphical"
    )
    out = report_dir / ("run-" + uuid4().hex[:12])
    out.mkdir(parents=True, exist_ok=True)
    os.environ["PANINI_DESKTOP_PIPE"] = "panini-graphics-" + uuid4().hex
    os.environ["PANINI_DESKTOP_SECRET"] = secrets.token_urlsafe(32)
    os.environ["PANINI_DESKTOP_WORKER"] = str(executable("Panini.Desktop.Worker"))
    broker = subprocess.Popen(
        [str(executable("Panini.Desktop.Broker"))], creationflags=subprocess.CREATE_NO_WINDOW
    )
    fixture = subprocess.Popen(
        [str(executable("Panini.Desktop.Fixture")), "--graphical"], creationflags=subprocess.CREATE_NO_WINDOW
    )
    pipe, checks, frames = DesktopPipe(), [], []
    target = None
    task = "graphical-smoke"
    try:
        for _ in range(40):
            await asyncio.sleep(0.2)
            try:
                target = next(
                    (t for t in (await pipe.call({"op": "targets"}))["targets"] if t["pid"] == fixture.pid),
                    None,
                )
                if target:
                    break
            except (Fault, OSError):
                pass
        assert target, "Fixture did not appear"

        async def state():
            return await pipe.call({"op": "visual_state", "target": target, "task_id": task})

        async def focus():
            result = subprocess.run(
                [str(executable("Panini.RuntimeHost")), "--focus", json.dumps(target)],
                capture_output=True,
                text=True,
                timeout=3,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            assert result.returncode == 0, result.stdout
            await asyncio.sleep(0.15)
            assert (await state())["observation"]["foreground"], "Fixture focus handoff failed"

        await focus()

        def element(snapshot, identifier):
            return next(e for e in snapshot["observation"]["elements"] if e["automation_id"] == identifier)

        def point(snapshot, identifier, fx=0.5, fy=0.5):
            b = element(snapshot, identifier)["bounds"]
            c = snapshot["capture"]
            return {
                "x": int((b["x"] + b["width"] * fx - c["source_x"]) * c["width"] / c["source_width"]),
                "y": int((b["y"] + b["height"] * fy - c["source_y"]) * c["height"] / c["source_height"]),
            }

        def save(snapshot, name):
            png = base64.b64decode(snapshot["capture"]["image_base64"], validate=True)
            assert png.startswith(b"\x89PNG\r\n\x1a\n")
            filename = name + ".png"
            (out / filename).write_bytes(png)
            return filename

        async def act(action, snapshot=None):
            snapshot = snapshot or await state()
            for attempt in range(2):
                try:
                    result = await pipe.call(
                        {
                            "op": "visual_action",
                            "target": target,
                            "task_id": task,
                            "snapshot_id": snapshot["snapshot_id"],
                            "action": action,
                        }
                    )
                    break
                except Fault as exc:
                    # The executor rejects focus loss before dispatch. The isolated
                    # fixture may be covered by the test host, so restore focus once
                    # and obtain a new, one-use screenshot receipt.
                    if exc.code == "FOREGROUND_CHANGED" and attempt == 0:
                        await focus()
                        snapshot = await state()
                        continue
                    if exc.code == "SNAPSHOT_CHANGED":
                        save(snapshot, "failure-expected")
                        save(await state(), "failure-actual")
                    raise
            assert result["verified"] is False, "Input acknowledgement must not claim task verification"
            assert result.get("after"), result
            index = len(frames)
            frames.append(
                {
                    "action": action,
                    "before": save(snapshot, f"{index:02}-before"),
                    "after": save(result["after"], f"{index:02}-after"),
                }
            )
            return result["after"]

        def counts(snapshot):
            text = element(snapshot, "graphics-status")["name"]
            return [int(v) for v in re.findall(r"-?\d+", text)]

        if "--planner-only" not in sys.argv:
            s = await state()
            save(s, "initial")
            checks.append("screenshot PNG and selected-window dimensions")
            before = counts(s)
            s = await act({"kind": "click", **point(s, "graphics-board", 0.2, 0.3)}, s)
            assert counts(s)[0] == before[0] + 1, counts(s)
            checks.append("coordinate left click received by canvas")
            before = counts(s)
            s = await act({"kind": "double_click", **point(s, "graphics-board", 0.4, 0.3)}, s)
            assert counts(s)[1] == before[1] + 1, counts(s)
            checks.append("coordinate double click received by canvas")
            before = counts(s)
            s = await act({"kind": "click", "button": "right", **point(s, "graphics-board", 0.6, 0.3)}, s)
            assert counts(s)[2] == before[2] + 1, counts(s)
            checks.append("coordinate right click")
            moves = counts(s)[3]
            s = await act({"kind": "move", **point(s, "graphics-board", 0.7, 0.6)}, s)
            assert counts(s)[3] > moves
            checks.append("pointer move")
            before = counts(s)
            path = [
                point(s, "graphics-board", x, y)
                for x, y in [(0.15, 0.7), (0.3, 0.35), (0.45, 0.75), (0.6, 0.35), (0.8, 0.7)]
            ]
            s = await act({"kind": "drag", "path": path}, s)
            assert counts(s)[4] == before[4] + 1, counts(s)
            checks.append("multi-point drag with actual canvas stroke")
            before = counts(s)
            s = await act(
                {"kind": "scroll", **point(s, "graphics-board"), "scroll_y": 240, "scroll_x": 120}, s
            )
            assert counts(s)[5] > before[5] and counts(s)[6] < before[6], counts(s)
            checks.append("horizontal and vertical wheel delivery")
            s = await act({"kind": "click", **point(s, "graphics-draft")}, s)
            s = await act({"kind": "keypress", "keys": ["CTRL", "A"]}, s)
            text = "  PANINI café 漢字 🪷\nsecond line  "
            s = await act({"kind": "type", "text": text}, s)
            assert element(s, "graphics-draft")["value"].replace("\r\n", "\n") == text
            checks.append("Ctrl+A and exact Unicode multiline whitespace-preserving typing")
            s = await act({"kind": "keypress", "keys": ["CTRL", "SHIFT", "HOME"]}, s)
            assert (
                "Control" in element(s, "graphics-keys")["name"]
                and "Shift" in element(s, "graphics-keys")["name"]
            )
            checks.append("three-key chord with extended navigation key")
            start = time.monotonic()
            s = await act({"kind": "wait", "milliseconds": 250}, s)
            assert time.monotonic() - start >= 0.25
            checks.append("bounded wait and fresh post-wait screenshot")
            s = await act({"kind": "screenshot"}, s)
            checks.append("explicit screenshot action")

            # Closely adjacent small controls: exact UIA identity must determine the hit,
            # even if a planner's unused fallback coordinate points elsewhere.
            precision_hits = 0
            for target_width in (940, 1400):
                ctypes.windll.user32.SetWindowPos(
                    ctypes.c_void_p(target["hwnd"]), None, 0, 0, target_width, 710, 0x16
                )
                await asyncio.sleep(0.15)
                for index in list(range(8)) * 2:
                    s = await state()
                    control = element(s, "precision-" + str(index))
                    s = await act({"kind": "click", "element_id": control["element_id"], "x": 1, "y": 1}, s)
                    precision_hits += 1
                    assert element(s, "precision-status")["name"] == f"Target {index}; Hits {precision_hits}"
            checks.append(
                f"{precision_hits}/{precision_hits} element-anchored hits on adjacent 14-DIP controls, including downsampled screenshots"
            )
            ctypes.windll.user32.SetWindowPos(ctypes.c_void_p(target["hwnd"]), None, 0, 0, 940, 710, 0x16)
            await asyncio.sleep(0.15)

            async def rejected(action, code, snapshot=None, **extra):
                snapshot = snapshot or await state()
                try:
                    await pipe.call(
                        {
                            "op": "visual_action",
                            "target": target,
                            "task_id": task,
                            "snapshot_id": snapshot["snapshot_id"],
                            "action": action,
                            **extra,
                        }
                    )
                    raise AssertionError("Invalid graphical action was accepted")
                except Fault as exc:
                    assert exc.code == code, (exc.code, code)

            await rejected({"kind": "click", "x": -1, "y": 1}, "POINT_OUTSIDE_SNAPSHOT")
            await rejected(
                {"kind": "click", "element_id": "not-observed", "x": 1, "y": 1}, "ELEMENT_NOT_OBSERVED"
            )
            await rejected(
                {"kind": "move", "element_id": "not-observed", "x": 1, "y": 1}, "ELEMENT_ANCHOR_UNSUPPORTED"
            )
            checks.append("unknown element identities and unsupported anchor actions are rejected")
            await rejected({"kind": "drag", "path": [{"x": 1, "y": 1}]}, "PATH_OUT_OF_RANGE")
            await rejected({"kind": "keypress", "keys": ["CTRL", "CONTROL"]}, "DUPLICATE_KEY")
            await rejected({"kind": "wait", "milliseconds": 99999}, "WAIT_OUT_OF_RANGE")
            checks.append("out-of-bounds, malformed path, duplicate modifier and excessive wait rejection")
            old = await state()
            await act({"kind": "move", **point(old, "graphics-board")}, old)
            await rejected({"kind": "move", **point(old, "graphics-board")}, "SNAPSHOT_EXPIRED", old)
            checks.append("one-use snapshot replay rejection")
            old = await state()
            c = old["capture"]
            ctypes.windll.user32.SetWindowPos(
                ctypes.c_void_p(target["hwnd"]), None, c["source_x"] + 10, c["source_y"], 0, 0, 0x15
            )
            await rejected({"kind": "click", **point(old, "graphics-board")}, "WINDOW_GEOMETRY_CHANGED", old)
            checks.append("window movement invalidates screenshot coordinates")
            current = await state()
            before_clicks = counts(current)[0]
            ctypes.windll.user32.SetWindowPos(
                ctypes.c_void_p(target["hwnd"]), None, 0, 0, 1400, current["capture"]["source_height"], 0x16
            )
            scaled = await state()
            assert scaled["capture"]["width"] < scaled["capture"]["source_width"], (
                "Scaling test requires downsampled capture"
            )
            scaled = await act({"kind": "click", **point(scaled, "graphics-board", 0.2, 0.3)}, scaled)
            assert counts(scaled)[0] == before_clicks + 1
            checks.append("downsampled screenshot coordinates map to physical window pixels")
            ctypes.windll.user32.SetWindowPos(
                ctypes.c_void_p(target["hwnd"]),
                None,
                0,
                0,
                current["capture"]["source_width"],
                current["capture"]["source_height"],
                0x16,
            )
            s = await state()
            # A semantic action changes pixels without moving the window; the old receipt must fail.
            draft = element(s, "graphics-draft")
            await pipe.call(
                {
                    "op": "action",
                    "target": target,
                    "task_id": task,
                    "observed_at": s["observation"]["observed_at"],
                    "action": {
                        "kind": "set_value",
                        "element_id": draft["element_id"],
                        "expected_name": draft["name"],
                        "expected_automation_id": draft["automation_id"],
                        "text": "Changed after observation",
                    },
                }
            )
            await rejected({"kind": "click", **point(s, "graphics-draft", 0.15, 0.2)}, "SNAPSHOT_CHANGED", s)
            checks.append("changed screen content invalidates old graphical receipt")
            s = await state()
            # Moving to the next control from the draft focuses the password fixture.
            await act({"kind": "click", **point(s, "graphics-draft")}, s)
            await act({"kind": "keypress", "keys": ["CTRL", "TAB"]})
            await rejected({"kind": "type", "text": "must never enter password"}, "PASSWORD_TARGET")
            checks.append("graphical typing into password field is rejected")
            # Verify all synthetic modifiers and buttons have been released.
            assert not any(
                ctypes.windll.user32.GetAsyncKeyState(k) & 0x8000 for k in [1, 2, 4, 16, 17, 18, 91]
            )
            checks.append("no held synthetic modifiers or buttons")
        if "--planner" in sys.argv or "--planner-only" in sys.argv:
            from backend import jev_client
            from backend.capabilities.desktop_visual import run_visual

            class LiveContext:
                def __init__(self):
                    self.task = SimpleNamespace(task_id=task)
                    self.evidence = []

                def check(self):
                    pass

                def dispatch(self):
                    pass

                def emit(self, phase, message, **details):
                    print(json.dumps({"phase": phase, "message": message, **details}), flush=True)
                    if phase == "needs_focus":
                        subprocess.run(
                            [str(executable("Panini.RuntimeHost")), "--focus", json.dumps(target)],
                            capture_output=True,
                            text=True,
                            timeout=3,
                            creationflags=subprocess.CREATE_NO_WINDOW,
                        )

                def record_evidence(self, kind, summary, details):
                    self.evidence.append({"kind": kind, "summary": summary, "details": details})
                    return str(len(self.evidence))

                async def confirm(self, action, *_):
                    # This disposable fixture test is pre-authorized; never auto-approve real app work.
                    assert target["pid"] == fixture.pid
                    print(json.dumps({"fixture_action": action}, ensure_ascii=True), flush=True)

            context = LiveContext()
            await focus()
            s = await state()
            await act({"kind": "click", **point(s, "graphics-draft")}, s)

            class RecordingPipe:
                count = 0

                async def call(self, request):
                    result = await pipe.call(request)
                    snapshot = result if request["op"] == "visual_state" else result.get("after")
                    if snapshot:
                        name = f"planner-{self.count:02}"
                        save(snapshot, name)
                        (out / (name + ".json")).write_text(
                            json.dumps(snapshot["observation"], indent=2), encoding="utf-8"
                        )
                        if request["op"] == "visual_action":
                            frames.append(
                                {
                                    "action": request["action"],
                                    "before": f"planner-{self.count - 1:02}.png",
                                    "after": name + ".png",
                                }
                            )
                        self.count += 1
                    return result

            try:
                result = await asyncio.wait_for(
                    run_visual(
                        SimpleNamespace(pipe=RecordingPipe()),
                        context,
                        "Replace the Graphical draft text with exactly PANINI visual loop verified. Do not save or send.",
                        target,
                    ),
                    240,
                )
                (out / "planner-result.json").write_text(
                    json.dumps(
                        {"outcome": result.outcome, "message": result.message, "evidence": context.evidence},
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                assert result.outcome == "verified_complete", result
                checks.append(
                    "live vision planner plus Jev admission plus Windows executor plus exact UIA readback"
                )
            finally:
                jev_client._client.stop()
        s = await state()
        await pipe.call({"op": "stop", "task_id": task})
        try:
            await pipe.call(
                {
                    "op": "visual_action",
                    "target": target,
                    "task_id": task,
                    "snapshot_id": s["snapshot_id"],
                    "action": {"kind": "move", **point(s, "graphics-board")},
                }
            )
            raise AssertionError("Stop did not revoke the receipt")
        except Fault as exc:
            assert exc.code == "SNAPSHOT_EXPIRED", exc.code
        checks.append("stop revokes graphical snapshot receipts")
        (out / "trace.json").write_text(
            json.dumps({"checks": checks, "frames": frames}, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        exported = [
            {**frame, "before": out.name + "/" + frame["before"], "after": out.name + "/" + frame["after"]}
            for frame in frames
        ]
        (report_dir / "trace.json").write_text(
            json.dumps({"checks": checks, "frames": exported}, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(json.dumps({"passed": checks, "output": str(out)}, indent=2))
    finally:
        for process in (fixture, broker):
            process.terminate()
            process.wait(timeout=5)


if __name__ == "__main__":
    asyncio.run(main())
