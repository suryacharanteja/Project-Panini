"""Live proof that PANINI can drive Paint: launch it, pick the Pencil, draw, and show its cursor.

Starts the real Windows broker and worker (no model calls), launches Paint from the application
registry, selects the Pencil through its UI Automation element, and draws an elephant with the same
`draw` action the planner uses. It then checks that the canvas really changed, that no mouse button is
left held, and (with the cursor overlay enabled) captures the screen mid-stroke to show the big cursor.

Evidence is written to artifacts/paint-smoke/: before.png, during.png (with the overlay), after.png.
Run:  .venv\\Scripts\\python.exe scripts/panini-paint-smoke.py [--keep]
"""

import asyncio
import base64
import ctypes
import io
import json
import os
import secrets
import subprocess
import sys
import threading
import time
from pathlib import Path
from uuid import uuid4

from PIL import Image, ImageChops, ImageGrab

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
NATIVE = ROOT / "panini-core/capabilities/desktop"
OUT = ROOT / "artifacts/paint-smoke"
VK_BUTTONS = {"left": 0x01, "right": 0x02, "middle": 0x04}


def executable(name):
    return NATIVE / name / "bin/Release/net10.0-windows" / (name + ".exe")


def image(state):
    return Image.open(io.BytesIO(base64.b64decode(state["capture"]["image_base64"]))).convert("RGB")


def buttons_down():
    return [n for n, vk in VK_BUTTONS.items() if ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000]


def elephant(box):
    """Strokes for a simple elephant, laid out inside (x, y, w, h) in screenshot pixels."""
    x, y, w, h = box

    def p(u, v):
        return (round(x + u * w), round(y + v * h))

    def stroke(points, smooth=True, closed=False):
        from backend.capabilities.desktop_visual import Stroke

        return Stroke(xy=[c for pt in points for c in pt], smooth=smooth, closed=closed)

    return [
        stroke(
            [
                p(0.20, 0.45),
                p(0.30, 0.30),
                p(0.52, 0.26),
                p(0.70, 0.34),
                p(0.74, 0.55),
                p(0.66, 0.68),
                p(0.30, 0.68),
                p(0.20, 0.58),
            ],
            closed=True,
        ),  # Body
        stroke(
            [p(0.66, 0.34), p(0.76, 0.22), p(0.90, 0.24), p(0.94, 0.38), p(0.88, 0.52), p(0.74, 0.50)],
            closed=True,
        ),  # Head
        stroke([p(0.90, 0.40), p(0.94, 0.58), p(0.90, 0.74), p(0.84, 0.80), p(0.82, 0.74)]),  # Trunk
        stroke(
            [p(0.72, 0.30), p(0.62, 0.28), p(0.60, 0.42), p(0.68, 0.50), p(0.74, 0.44)], closed=True
        ),  # Ear
        stroke([p(0.28, 0.66), p(0.28, 0.88), p(0.38, 0.88), p(0.38, 0.66)], smooth=False),  # Back leg
        stroke([p(0.54, 0.66), p(0.54, 0.88), p(0.64, 0.88), p(0.64, 0.66)], smooth=False),  # Front leg
        stroke([p(0.20, 0.46), p(0.12, 0.52), p(0.10, 0.66)]),  # Tail
        stroke([p(0.84, 0.32), p(0.85, 0.32)], smooth=False),  # Eye
    ]


async def main():
    from backend.capabilities import desktop_visual as visual
    from backend.capabilities.desktop import DesktopPipe

    keep = "--keep" in sys.argv
    OUT.mkdir(parents=True, exist_ok=True)
    os.environ["PANINI_DESKTOP_PIPE"] = "panini-paint-" + uuid4().hex
    os.environ["PANINI_DESKTOP_SECRET"] = secrets.token_urlsafe(32)
    os.environ["PANINI_DESKTOP_WORKER"] = str(executable("Panini.Desktop.Worker"))
    os.environ["PANINI_RUNTIME_ROOT"] = str(ROOT)
    broker = subprocess.Popen(
        [str(executable("Panini.Desktop.Broker"))], creationflags=subprocess.CREATE_NO_WINDOW
    )
    pipe, paint_pid, task = DesktopPipe(), None, "paint-smoke-" + uuid4().hex[:8]
    try:
        for _ in range(60):
            try:
                await pipe.call({"op": "health"})
                break
            except Exception:  # noqa: BLE001 -- The broker is still starting.
                await asyncio.sleep(0.25)
        health = await pipe.call({"op": "health"})
        assert "draw" in health["graphical_actions"], health
        print("health: draw supported, max strokes", health["max_strokes"])

        apps = (await pipe.call({"op": "applications"}))["applications"]
        print("registered applications:", ", ".join(a["application_id"] for a in apps))
        assert any(a["application_id"] == "paint" for a in apps), "Paint is not registered"

        before_ids = {
            t["pid"] for t in (await pipe.call({"op": "targets"}))["targets"] if t["application"] == "mspaint"
        }
        await pipe.call({"op": "launch", "task_id": task, "application_id": "paint"})
        target = None
        for _ in range(60):
            windows = [
                t for t in (await pipe.call({"op": "targets"}))["targets"] if t["application"] == "mspaint"
            ]
            fresh = [t for t in windows if t["pid"] not in before_ids] or windows
            if fresh:
                target = max(fresh, key=lambda t: int(t["created"]))
                break
            await asyncio.sleep(0.5)
        assert target, "Paint window never appeared"
        paint_pid = target["pid"] if target["pid"] not in before_ids else None
        print(
            "target:",
            target["label"],
            "pid",
            target["pid"],
            "(launched by this run)" if paint_pid else "(pre-existing)",
        )
        await asyncio.sleep(1.5)
        for attempt in range(1, 6):
            done = subprocess.run(
                [str(executable("Panini.RuntimeHost")), "--focus", json.dumps(target)],
                creationflags=subprocess.CREATE_NO_WINDOW,
                capture_output=True,
                check=False,
            )
            await asyncio.sleep(0.6)
            probe = await pipe.call({"op": "visual_state", "task_id": task, "target": target})
            print(
                f"focus attempt {attempt}: helper exit {done.returncode}, foreground={probe['observation']['foreground']}"
            )
            if probe["observation"]["foreground"]:
                break
        else:
            raise AssertionError("Could not bring Paint to the foreground")

        async def observe():
            return await pipe.call({"op": "visual_state", "task_id": task, "target": target})

        async def act(state, wire, timeout_ms=None):
            request = {
                "op": "visual_action",
                "task_id": task,
                "target": target,
                "snapshot_id": state["snapshot_id"],
                "action": wire,
            }
            if timeout_ms:
                request["timeout_ms"] = timeout_ms
            return await pipe.call(request)

        state = await observe()
        before = image(state)
        before.save(OUT / "before.png")
        capture = state["capture"]
        print(
            "capture:",
            capture["width"],
            "x",
            capture["height"],
            "of",
            capture["source_width"],
            "x",
            capture["source_height"],
        )

        # 1. Choose the Pencil by its UI Automation identity, exactly as the planner does.
        pencil = next(e for e in state["observation"]["elements"] if e.get("automation_id") == "PencilTool")
        click = visual.ground_element(
            visual.VisualAction(kind="click", element_id=pencil["element_id"], explanation="Pick the Pencil"),
            state,
        )
        result = await act(state, {**click.model_dump(exclude={"strokes", "speed"})})
        state = result["after"]
        print("clicked Pencil at", (click.x, click.y), "dispatched", result["dispatched"])

        # 2. Find the canvas: the large white area below the ribbon.
        w, h = capture["width"], capture["height"]
        box = (int(w * 0.18), int(h * 0.34), int(w * 0.64), int(h * 0.56))
        action = visual.VisualAction(kind="draw", explanation="Draw a simple elephant", strokes=elephant(box))
        visual.validate_points(action, capture)
        paths = visual.expand_strokes(action, capture)
        step_ms = visual.STEP_MS["normal"]
        estimate = visual.estimate_ms(paths, capture, step_ms)
        print(
            f"drawing {len(paths)} strokes / {sum(len(p) for p in paths)} points, estimated {estimate / 1000:.1f}s"
        )
        wire = {
            "kind": "draw",
            "explanation": action.explanation,
            "button": "left",
            "step_ms": step_ms,
            "strokes": [{"path": [{"x": x, "y": y} for x, y in path]} for path in paths],
        }

        # 3. Capture the whole screen while it draws, to show the cursor overlay.
        shot = {}

        def grab():
            time.sleep(min(4.0, estimate / 1000 / 2))
            try:
                shot["image"] = ImageGrab.grab(all_screens=True)
                shot["buttons"] = buttons_down()
            except Exception as exc:  # noqa: BLE001 -- Evidence only.
                shot["error"] = str(exc)

        watcher = threading.Thread(target=grab)
        watcher.start()
        started = time.time()
        result = await act(state, wire, timeout_ms=58000)
        elapsed = time.time() - started
        watcher.join()
        assert result["dispatched"], result
        print(f"draw dispatched in {elapsed:.1f}s")
        if "image" in shot:
            shot["image"].save(OUT / "during.png")
            print("mid-stroke buttons held:", shot["buttons"], "-> saved during.png")

        after_state = result["after"]
        after = image(after_state)
        after.save(OUT / "after.png")
        left, top, right, bottom = box
        crop = (left, top, right, bottom)
        changed = (
            ImageChops.difference(before.crop(crop), after.crop(crop))
            .convert("L")
            .point(lambda v: 255 if v > 40 else 0)
        )
        dark = sum(1 for v in changed.getdata() if v)
        print("pixels drawn inside the canvas box:", dark)
        assert dark > 400, "Paint's canvas did not change: nothing was drawn"
        assert not buttons_down(), "A mouse button was left held"
        print("no mouse button left held")

        # 4. The cursor overlay must not be targetable or block the next action.
        again = await observe()
        assert again["observation"]["foreground"], "Paint lost the foreground after drawing"
        print("PASS: Paint drew an elephant; evidence in", OUT)
    finally:
        try:
            await pipe.call({"op": "overlay", "phase": "done", "message": "Done"})
        except Exception:  # noqa: BLE001
            pass
        await asyncio.sleep(1.4)
        if broker.poll() is None:
            broker.kill()
        if paint_pid and not keep:
            subprocess.run(["taskkill", "/PID", str(paint_pid), "/T", "/F"], capture_output=True, check=False)


if __name__ == "__main__":
    asyncio.run(main())
