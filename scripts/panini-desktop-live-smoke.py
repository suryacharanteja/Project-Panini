"""Real planner/Jev/native verification against a newly opened Windows test app.

Refuses to use an already-running Calculator or Notepad. Trace data is confined
to the selected synthetic test task. No user's documents or existing app are used.
"""

import asyncio
import json
import os
import secrets
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
APP = "notepad" if "--notepad" in sys.argv else "calculator"
OUTPUT = ROOT / "artifacts" / f"panini-native-{APP}"
OUTPUT.mkdir(parents=True, exist_ok=True)


def exe(project):
    return (
        ROOT / "panini-core/capabilities/desktop" / project / "bin/Release/net10.0-windows" / f"{project}.exe"
    )


def processes():
    names = "Notepad" if APP == "notepad" else "CalculatorApp,Calculator"
    output = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-Command",
            f"@(Get-Process {names} -ErrorAction SilentlyContinue | Select-Object Id,@{{Name='created';Expression={{$_.StartTime.ToUniversalTime().Ticks.ToString()}}}}) | ConvertTo-Json -Compress",
        ],
        capture_output=True,
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    ).stdout.strip()
    value = json.loads(output) if output else []
    return value if isinstance(value, list) else [value]


async def main():
    from backend import jev_client
    from backend.capabilities import desktop

    if processes():
        raise RuntimeError(f"An existing {APP} process is running; this test will not modify it.")
    os.environ["PANINI_DESKTOP_PIPE"] = f"panini-test-{uuid4()}"
    os.environ["PANINI_DESKTOP_SECRET"] = secrets.token_urlsafe(32)
    os.environ["PANINI_DESKTOP_WORKER"] = str(exe("Panini.Desktop.Worker"))
    env = {
        key: value
        for key, value in os.environ.items()
        if not any(part in key.upper() for part in ("KEY", "TOKEN", "SECRET", "PASSWORD"))
    }
    env["PANINI_DESKTOP_SECRET"] = os.environ["PANINI_DESKTOP_SECRET"]
    broker = subprocess.Popen(
        [str(exe("Panini.Desktop.Broker"))], env=env, creationflags=subprocess.CREATE_NO_WINDOW
    )
    draft = OUTPUT / f"panini-verification-{uuid4()}.txt"
    sentinel = f"PANINI owned test draft {uuid4()}"
    if APP == "notepad":
        draft.write_text(sentinel, encoding="utf-8")
    launcher = subprocess.Popen(
        [
            str(
                Path(os.environ["SystemRoot"])
                / "System32"
                / ("notepad.exe" if APP == "notepad" else "calc.exe")
            ),
            *([str(draft)] if APP == "notepad" else []),
        ],
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    owned = []
    trace = []
    adapter = desktop.DesktopAdapter(True)
    original_parse = desktop.parse

    async def traced_parse(rules, payload, schema):
        result = await original_parse(rules, payload, schema)
        trace.append({"schema": schema.__name__, "input": payload, "output": result.model_dump()})
        return result

    desktop.parse = traced_parse
    target = None
    try:
        for _ in range(30):
            await asyncio.sleep(0.3)
            candidates = processes()
            if len(candidates) == 1 or any(item["Id"] == launcher.pid for item in candidates):
                owned = candidates
            response = await adapter.pipe.call({"op": "targets"})
            candidates = [item for item in response["targets"] if APP in item["application"].lower()]
            if APP == "notepad":
                # Modern Notepad may keep the application title generic. Bind the
                # test to its unique active document value, never a restored tab.
                for candidate in candidates:
                    page = await adapter.pipe.call({"op": "observe", "target": candidate})
                    if any(e.get("value") == sentinel for e in page["elements"]):
                        target = candidate
                        break
            elif candidates:
                target = candidates[0]
            if target:
                selected_pid = target.get("hosted_pid") or target["pid"]
                owned = [item for item in processes() if item["Id"] in {launcher.pid, selected_pid}]
            if target and owned:
                break
        if not target or not owned:
            raise RuntimeError("The isolated app window did not become available")
        if APP == "notepad":
            observed = await adapter.pipe.call({"op": "observe", "target": target})
            if not any(e.get("value") == sentinel for e in observed["elements"]):
                raise RuntimeError(
                    "The active Notepad document is not the owned synthetic file; no input is allowed"
                )

        class Context:
            task = SimpleNamespace(task_id=str(uuid4()))

            def check(self):
                pass

            def dispatch(self):
                pass

            def emit(self, phase, message, **details):
                trace.append({"phase": phase, "message": message, **details})
                if phase == "needs_focus":
                    focused = subprocess.run(
                        [str(exe("Panini.RuntimeHost")), "--focus", json.dumps(target)],
                        capture_output=True,
                        text=True,
                        timeout=2,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                    trace.append({"focus_exit": focused.returncode, "detail": focused.stdout})

            async def confirm(self, action, application, effect, data):
                if APP != "notepad" or action["kind"] != "set_value" or data != "PANINI Windows verification":
                    raise RuntimeError("This test can approve only its exact Notepad draft")
                trace.append({"confirmed": "exact synthetic Notepad draft"})

            def record_evidence(self, *values):
                trace.append({"evidence": values})
                return str(uuid4())

        instruction = (
            "Set the document text to exactly PANINI Windows verification. Do not save a file."
            if APP == "notepad"
            else "In Standard Calculator, calculate 17 multiplied by 23. Verify the displayed result is 391."
        )
        outcome = await asyncio.wait_for(adapter.run(Context(), instruction, target), 300)
        print(
            json.dumps(
                {
                    "app": APP,
                    "outcome": outcome.outcome,
                    "message": outcome.message,
                    "evidence_count": len(outcome.evidence_ids),
                }
            )
        )
        if outcome.outcome != "verified_complete":
            raise RuntimeError("Windows task did not verify; inspect the isolated trace")
    finally:
        desktop.parse = original_parse
        (OUTPUT / "trace.json").write_text(json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8")
        for item in owned:
            code = f"$owned = Get-Process -Id {int(item['Id'])} -ErrorAction SilentlyContinue; if ($owned -and $owned.StartTime.ToUniversalTime().Ticks.ToString() -eq '{int(item['created'])}') {{ Stop-Process -Id $owned.Id }}"
            subprocess.run(
                ["powershell.exe", "-NoProfile", "-Command", code], creationflags=subprocess.CREATE_NO_WINDOW
            )
        broker.terminate()
        broker.wait(timeout=5)
        jev_client.stop()


if __name__ == "__main__":
    asyncio.run(main())
