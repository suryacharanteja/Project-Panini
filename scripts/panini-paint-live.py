"""Run PANINI's real Desktop planner against a real Paint window, with every step printed.

Unlike panini-paint-smoke.py (fixed strokes, no models) this uses the live planner, vision model and Jev
checks, the same code path the avatar uses, so it shows exactly what the model proposes and where a run
stops. It takes the real mouse and costs a few cents of model calls. Confirmations are auto-approved.

Run:  .venv\\Scripts\\python.exe scripts/panini-paint-live.py ["In Paint, draw a simple elephant"] [--keep]
"""

import asyncio
import ctypes
import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
NATIVE = ROOT / "panini-core/capabilities/desktop"
OUT = ROOT / "artifacts/paint-live"


def executable(name):
    return NATIVE / name / "bin/Release/net10.0-windows" / (name + ".exe")


class Context:
    """Just enough of a task context to run the planner loop standalone, narrating as it goes."""

    def __init__(self):
        self.task = SimpleNamespace(task_id="paint-live-" + uuid4().hex[:8])
        self.started = time.time()
        self.confirmations = []

    def stamp(self, text):
        print(f"[{time.time() - self.started:6.1f}s] {text}", flush=True)

    def check(self):
        pass

    def emit(self, phase, message, **details):
        self.stamp(f"{phase}: {message}" + (f" {details}" if details else ""))

    def dispatch(self):
        self.stamp("-> dispatching real input")

    def record_evidence(self, kind, message, data=None):
        self.stamp(f"evidence {kind}: {message}")
        return kind

    async def confirm(self, action, label, explanation, review):
        self.confirmations.append(explanation)
        self.stamp(f"auto-approved: {explanation}")

    def computer_activity(self, activity):
        action = activity.get("action") or {}
        self.stamp(f"activity {activity['phase']}: {str(activity['message'])[:90]} {action.get('kind', '')}")


async def main():
    from backend.capabilities.desktop import DesktopAdapter
    from backend.capabilities.desktop_visual import run_visual

    keep = "--keep" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    instruction = args[0] if args else "In Paint, draw a simple elephant"
    OUT.mkdir(parents=True, exist_ok=True)
    os.environ["PANINI_DESKTOP_PIPE"] = "panini-live-" + uuid4().hex
    os.environ["PANINI_DESKTOP_SECRET"] = secrets.token_urlsafe(32)
    os.environ["PANINI_DESKTOP_WORKER"] = str(executable("Panini.Desktop.Worker"))
    os.environ["PANINI_RUNTIME_ROOT"] = str(ROOT)
    broker = subprocess.Popen(
        [str(executable("Panini.Desktop.Broker"))], creationflags=subprocess.CREATE_NO_WINDOW
    )
    adapter = DesktopAdapter(True)
    paint_pid = None
    try:
        for _ in range(60):
            try:
                await adapter.pipe.call({"op": "health"})
                break
            except Exception:  # noqa: BLE001 -- The broker is still starting.
                await asyncio.sleep(0.25)
        before = {
            t["pid"]
            for t in (await adapter.pipe.call({"op": "targets"}))["targets"]
            if t["application"] == "mspaint"
        }
        os.startfile("mspaint")  # noqa: S606 -- The agent's own "open" does exactly this.
        target = None
        for _ in range(60):
            choices = await adapter.targets()
            paint = [c for c in choices if "paint" in c["label"].lower()]
            if paint:
                target = await adapter.select(paint[-1]["choice_id"])
                break
            await asyncio.sleep(0.5)
        assert target, "Paint never appeared as a Desktop target"
        paint_pid = target["pid"] if target["pid"] not in before else None
        print("target:", target["label"], "| application:", target["application"])
        # The avatar shell raises the window on `needs_focus`; do the same here.
        subprocess.run(
            [str(executable("Panini.RuntimeHost")), "--focus", json.dumps(target)],
            creationflags=subprocess.CREATE_NO_WINDOW,
            check=False,
        )
        await asyncio.sleep(1.0)
        context = Context()
        outcome = await asyncio.wait_for(run_visual(adapter, context, instruction, target), 420)
        print("OUTCOME:", outcome.outcome, "-", outcome.message)
        from PIL import ImageGrab

        ImageGrab.grab(all_screens=True).save(OUT / "final.png")
        print("saved", OUT / "final.png")
    finally:
        await adapter.overlay("done", "Done")
        await asyncio.sleep(1.4)
        if broker.poll() is None:
            broker.kill()
        if paint_pid and not keep:
            subprocess.run(["taskkill", "/PID", str(paint_pid), "/T", "/F"], capture_output=True, check=False)
        ctypes.windll.user32.mouse_event(4, 0, 0, 0, 0)  # Never leave a button held.


if __name__ == "__main__":
    asyncio.run(main())
