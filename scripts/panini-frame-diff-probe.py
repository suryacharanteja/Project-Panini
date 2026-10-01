"""Diagnostic: what changes in Paint's window between two idle snapshots after the Pencil is selected?

The Desktop loop refuses to act when the target region differs between planning and acting. This shows
which pixels of a resting Paint window change on their own, so the tolerance can be justified by data.
Run: .venv\\Scripts\\python.exe scripts/panini-frame-diff-probe.py
"""

import asyncio
import base64
import io
import json
import os
import secrets
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

from PIL import Image, ImageChops

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
NATIVE = ROOT / "panini-core/capabilities/desktop"


def executable(name):
    return NATIVE / name / "bin/Release/net10.0-windows" / (name + ".exe")


def decode(state):
    return Image.open(io.BytesIO(base64.b64decode(state["capture"]["image_base64"]))).convert("RGB")


async def main():
    from backend.capabilities import desktop_visual as visual
    from backend.capabilities.desktop import DesktopAdapter

    os.environ["PANINI_DESKTOP_PIPE"] = "panini-diff-" + uuid4().hex
    os.environ["PANINI_DESKTOP_SECRET"] = secrets.token_urlsafe(32)
    os.environ["PANINI_DESKTOP_WORKER"] = str(executable("Panini.Desktop.Worker"))
    os.environ["PANINI_RUNTIME_ROOT"] = str(ROOT)
    os.environ["PANINI_DESKTOP_OVERLAY"] = os.getenv("PANINI_DESKTOP_OVERLAY", "1")
    broker = subprocess.Popen(
        [str(executable("Panini.Desktop.Broker"))], creationflags=subprocess.CREATE_NO_WINDOW
    )
    adapter, task, pid = DesktopAdapter(True), "diff-" + uuid4().hex[:6], None
    try:
        for _ in range(60):
            try:
                await adapter.pipe.call({"op": "health"})
                break
            except Exception:  # noqa: BLE001
                await asyncio.sleep(0.25)
        os.startfile("mspaint")  # noqa: S606
        target = None
        for _ in range(60):
            paint = [c for c in await adapter.targets() if "paint" in c["label"].lower()]
            if paint:
                target = await adapter.select(paint[-1]["choice_id"])
                break
            await asyncio.sleep(0.5)
        pid = target["pid"]
        subprocess.run(
            [str(executable("Panini.RuntimeHost")), "--focus", json.dumps(target)],
            creationflags=subprocess.CREATE_NO_WINDOW,
            check=False,
        )
        await asyncio.sleep(1.5)

        async def state():
            return await adapter.pipe.call({"op": "visual_state", "task_id": task, "target": target})

        first = await state()
        pencil = next(e for e in first["observation"]["elements"] if e.get("automation_id") == "PencilTool")
        click = visual.ground_element(
            visual.VisualAction(kind="click", element_id=pencil["element_id"], explanation="Pencil"), first
        )
        after = await adapter.pipe.call(
            {
                "op": "visual_action",
                "task_id": task,
                "target": target,
                "snapshot_id": first["snapshot_id"],
                "action": click.model_dump(exclude={"strokes", "speed", "layout"}),
            }
        )
        planned = after["after"]
        await asyncio.sleep(9)  # About as long as the planner takes.
        fresh = await state()
        a, b = decode(planned), decode(fresh)
        diff = ImageChops.difference(a, b).convert("L").point(lambda v: 255 if v > 24 else 0)
        box = diff.getbbox()
        changed = sum(1 for v in diff.getdata() if v)
        print("size", a.size, "changed pixels:", changed, "bbox:", box)
        area = visual.blank_area(planned["capture"]["image_base64"])
        print("canvas:", area)
        if area and box:
            inside = diff.crop((area["x0"], area["y0"], area["x1"], area["y1"])).getbbox()
            print("changed inside canvas region:", inside)
        print("foreground:", planned["observation"]["foreground"], fresh["observation"]["foreground"])
        print(
            "focused:",
            planned["observation"].get("focused_element_id"),
            fresh["observation"].get("focused_element_id"),
        )
        diff.save(ROOT / "artifacts/paint-live/frame-diff.png")
    finally:
        if broker.poll() is None:
            broker.kill()
        if pid:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, check=False)


if __name__ == "__main__":
    asyncio.run(main())
