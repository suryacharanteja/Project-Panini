"""V2-12 live check: moving the mouse during a real desktop task takes control instead of stopping it.

Opens a fresh Paint (refuses if Paint is already running), asks PANINI to draw, and plays the person: while
PANINI's stroke holds the left button, it moves the real cursor away. Expected: the task pauses with the person
in control (no stop), the person gives control back, PANINI looks again and finishes. Only the Paint process this
script opened is closed afterwards. Uses the real planner (a few cents of model calls) and the real mouse.

Run: .venv\\Scripts\\python scripts\\panini-desktop-takeover-live.py [--scripted]

--scripted replaces only the model's decisions (the goal, one large slow circle per step, approving judgments and
a final check that passes once control has come back), so it needs no model credits; Paint, the native worker,
the real mouse and the takeover path stay real.
"""

import asyncio
import ctypes
import json
import os
import secrets
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUTPUT = ROOT / "artifacts" / "panini-desktop-takeover"
OUTPUT.mkdir(parents=True, exist_ok=True)
user32 = ctypes.windll.user32
VK_LBUTTON = 0x01


class Point(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def exe(project):
    return (
        ROOT / "panini-core/capabilities/desktop" / project / "bin/Release/net10.0-windows" / f"{project}.exe"
    )


def paint_processes():
    out = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command",
         "@(Get-Process mspaint -ErrorAction SilentlyContinue | Select-Object Id,"
         "@{Name='created';Expression={$_.StartTime.ToUniversalTime().Ticks.ToString()}}) | ConvertTo-Json -Compress"],
        capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW,
    ).stdout.strip()  # fmt: skip
    value = json.loads(out) if out else []
    return value if isinstance(value, list) else [value]


def play_the_person(log, takeover, done):
    """Wait for PANINI to hold the left button mid-stroke, then move the cursor away like a person would.
    After PANINI pauses, wait two seconds and give control back."""
    deadline = time.monotonic() + 240
    while time.monotonic() < deadline and not done.is_set():
        if user32.GetAsyncKeyState(VK_LBUTTON) & 0x8000:
            time.sleep(0.4)  # Let the stroke get going.
            here = Point()
            user32.GetCursorPos(ctypes.byref(here))
            user32.SetCursorPos(here.x + 260, here.y + 180)
            log.append({"person": "moved the mouse", "at": time.time()})
            break
        time.sleep(0.01)
    else:
        log.append({"person": "never saw a stroke"})
        return
    while time.monotonic() < deadline and not done.is_set():
        if takeover.state("desktop", "pc").holder == "human":
            log.append({"person": "has control", "at": time.time()})
            time.sleep(2)
            takeover.release("desktop", "pc")
            log.append({"person": "gave control back", "at": time.time()})
            return
        time.sleep(0.05)
    log.append({"person": "PANINI never paused"})


def script_the_planner(desktop_visual, takeover):
    """Fixed model answers for --scripted runs."""
    import base64
    import io
    import math

    from PIL import Image

    seen = {"handed_back": False}
    original_release = takeover.release

    def release(*args, **kwargs):
        seen["handed_back"] = True
        return original_release(*args, **kwargs)

    async def parse(rules, payload, schema, *, image=None, **kwargs):
        name = schema.__name__
        if name == "VisualGoal":
            return schema(supported=True, explanation="Draw a circle.", field="visual", expected="a circle",
                          automation_id="")  # fmt: skip
        if name == "VisualAssessment":
            return schema(matched=seen["handed_back"], evidence="scripted check")
        width, height = Image.open(io.BytesIO(base64.b64decode(image))).size if image else (1200, 800)
        cx, cy, r = width // 2, int(height * 0.55), int(min(width, height) * 0.18)
        xy = []
        for k in range(24):
            xy += [int(cx + r * math.cos(2 * math.pi * k / 24)), int(cy + r * math.sin(2 * math.pi * k / 24))]
        return schema(kind="draw", explanation="Draw a large circle.", layout=f"circle at {cx},{cy} r {r}",
                      strokes=[{"xy": xy, "smooth": True, "closed": True}], speed="slow")  # fmt: skip

    def ask(question, fields, *args, **kwargs):
        return {"answers": {key: {"probability": 0.95} for key in fields}}

    takeover.release = release
    desktop_visual.parse = parse
    desktop_visual.jev_client.ask = ask


async def main():
    os.environ["PANINI_LIVE_VIEW"] = "true"
    from backend import jev_client, live
    from backend.capabilities import desktop, desktop_visual

    if "--scripted" in sys.argv:
        script_the_planner(desktop_visual, live.takeover())

    if paint_processes():
        raise RuntimeError("Paint is already running; this test only uses a Paint it opens itself.")
    os.environ["PANINI_DESKTOP_PIPE"] = f"panini-test-{uuid4()}"
    os.environ["PANINI_DESKTOP_SECRET"] = secrets.token_urlsafe(32)
    os.environ["PANINI_DESKTOP_WORKER"] = str(exe("Panini.Desktop.Worker"))
    env = {
        k: v
        for k, v in os.environ.items()
        if not any(p in k.upper() for p in ("KEY", "TOKEN", "SECRET", "PASSWORD"))
    }
    env["PANINI_DESKTOP_SECRET"] = os.environ["PANINI_DESKTOP_SECRET"]
    owned, log, activity = [], [], []
    done = threading.Event()
    broker = subprocess.Popen(
        [str(exe("Panini.Desktop.Broker"))], env=env, creationflags=subprocess.CREATE_NO_WINDOW
    )
    try:
        # Modern Paint is a Store app: start it through its app alias.
        subprocess.Popen(
            ["cmd.exe", "/d", "/c", "start", "", "mspaint"], creationflags=subprocess.CREATE_NO_WINDOW
        )
        adapter = desktop.DesktopAdapter(True)
        target = None
        for _ in range(40):
            await asyncio.sleep(0.4)
            response = await adapter.pipe.call({"op": "targets"})
            candidates = [t for t in response["targets"] if "paint" in t["application"].lower()]
            owned = [p for p in paint_processes()]
            if candidates and owned:
                target = candidates[0]
                break
        if not target:
            raise RuntimeError("The fresh Paint window did not become available.")

        class Context:
            task = SimpleNamespace(task_id=str(uuid4()))

            def check(self):
                pass

            def dispatch(self):
                pass

            def emit(self, phase, message, **details):
                log.append({"phase": phase, "message": message})
                if phase == "needs_focus":
                    subprocess.run([str(exe("Panini.RuntimeHost")), "--focus", json.dumps(target)],
                                   capture_output=True, timeout=2, creationflags=subprocess.CREATE_NO_WINDOW)  # fmt: skip

            def computer_activity(self, item):
                activity.append({"phase": item.get("phase"), "message": item.get("message")})

            async def confirm(self, action, application, effect, data):
                if "paint" not in str(application).lower():
                    raise RuntimeError("This test approves actions in its own Paint only.")
                log.append({"approved": action.get("kind")})

            def record_evidence(self, *values):
                return str(uuid4())

        person = threading.Thread(target=play_the_person, args=(log, live.takeover(), done), daemon=True)
        person.start()
        outcome = await asyncio.wait_for(
            adapter.run(
                Context(), "Draw a large circle in the middle of the canvas with the pencil.", target
            ),
            420,
        )
        done.set()
        phases = [a["phase"] for a in activity]
        result = {
            "outcome": outcome.outcome,
            "message": outcome.message,
            "person": [e for e in log if "person" in e],
            "paused_then_resumed": "paused" in phases and "resumed" in phases,
            "frames_in_live_view": (live.hub().latest("desktop", "pc") or (b"", {"seq": 0}))[1]["seq"],
        }
        print(json.dumps(result, indent=2))
        if not result["paused_then_resumed"]:
            raise RuntimeError("PANINI did not pause for the person and resume.")
        if outcome.outcome not in ("verified_complete", "partial"):
            raise RuntimeError(f"The task ended {outcome.outcome} after the takeover.")
    finally:
        done.set()
        (OUTPUT / "trace.json").write_text(
            json.dumps({"log": log, "activity": activity}, indent=2, default=str)
        )
        for item in owned:
            code = (f"$p = Get-Process -Id {int(item['Id'])} -ErrorAction SilentlyContinue; "
                    f"if ($p -and $p.StartTime.ToUniversalTime().Ticks.ToString() -eq '{int(item['created'])}') "
                    "{ Stop-Process -Id $p.Id }")  # fmt: skip
            subprocess.run(
                ["powershell.exe", "-NoProfile", "-Command", code], creationflags=subprocess.CREATE_NO_WINDOW
            )
        broker.terminate()
        broker.wait(timeout=5)
        jev_client.stop()


if __name__ == "__main__":
    asyncio.run(main())
