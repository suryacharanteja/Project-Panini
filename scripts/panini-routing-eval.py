"""Opt-in real-model routing evaluation. No executor is connected; no app input occurs."""

import asyncio
import json
import os
import sys
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if "--configured" not in sys.argv:
    os.environ.setdefault("PANINI_PLANNER_MODEL", "gpt-4.1-mini")

from backend import config  # noqa: E402
from backend.panini.contracts import Turn  # noqa: E402
from backend.panini.coordinator import Session  # noqa: E402
from backend.panini.events import EventHub  # noqa: E402
from backend.panini.planning import route  # noqa: E402


async def main():
    session = Session("eval", 0, EventHub("eval", "eval"))
    session.inventory = {
        "code": {"state": "ready", "targets": [{"choice_id": "workspace", "label": "D:/Projects/approved"}]},
        "desktop": {
            "state": "ready",
            "targets": [],
            "applications": [
                {"application_id": "vscode", "label": "Visual Studio Code", "application": "code"}
            ],
        },
        "search": {"state": "ready", "targets": []},
        "browser": {
            "state": "ready",
            "targets": [{"choice_id": "browser", "label": "PANINI managed browser"}],
        },
    }
    checks = []

    async def check(name, text, predicate):
        turn = Turn(command_id=uuid4(), text=text)
        plan = await route(session, turn)
        passed = bool(predicate(plan))
        checks.append({"name": name, "passed": passed, "plan": plan})
        print(f"{'PASS' if passed else 'FAIL'}: {name}", flush=True)
        return plan

    first = await check(
        "Missing simulation subject asks a useful question",
        "Run a simulation in Python using VS Code.",
        lambda p: (
            p.get("clarification")
            and not p["steps"]
            and not any(w in p["reply"].lower() for w in ("select the window", "choose a window"))
        ),
    )
    session.history.extend(
        [
            {"role": "user", "text": "Run a simulation in Python using VS Code."},
            {"role": "assistant", "text": first["reply"]},
        ]
    )
    session.pending = {"instruction": "Run a simulation in Python using VS Code.", "question": first["reply"]}
    await check(
        "Clarification proceeds to Code without an editor target",
        "Simulate 100 dice rolls with random seed 42 and print the counts. Use only the standard library. You can run it without opening an editor; do not open VS Code for this task.",
        lambda p: (
            bool(p["steps"])
            and all(
                s["capability"] == "code" and s["target_query"] in {"", "D:/Projects/approved"}
                for s in p["steps"]
            )
        ),
    )
    session.history.clear()
    session.pending = None
    await check(
        "Explicit app opening uses a registered ID",
        "Open Visual Studio Code.",
        lambda p: (
            len(p["steps"]) == 1
            and p["steps"][0]["operation"] == "open_application"
            and p["steps"][0]["application_id"] == "vscode"
        ),
    )
    await check(
        "Mixed capability request becomes sequential work",
        "Find the official Python documentation, then open its website in the browser.",
        lambda p: [s["capability"] for s in p["steps"]] == ["search", "browser"],
    )
    await check(
        "Complete coding request needs no manual target selection",
        "Run a Python calculation of 12 * 13 in my configured workspace and report the output. I use VS Code.",
        lambda p: (
            len(p["steps"]) == 1
            and p["steps"][0]["capability"] == "code"
            and p["steps"][0]["target_query"] in {"", "D:/Projects/approved"}
        ),
    )
    await check(
        "Unavailable named workspace is not silently replaced",
        "Run tests only in D:/Projects/finance, never in another workspace.",
        lambda p: (
            p.get("clarification")
            or (bool(p["steps"]) and all("finance" in s["target_query"].lower() for s in p["steps"]))
        ),
    )
    output = ROOT / "artifacts/panini-orchestration"
    output.mkdir(parents=True, exist_ok=True)
    passed = all(row["passed"] for row in checks)
    (
        output / ("routing-eval-configured.json" if "--configured" in sys.argv else "routing-eval.json")
    ).write_text(
        json.dumps(
            {
                "status": "passed" if passed else "failed",
                "model": os.getenv("PANINI_PLANNER_MODEL", config.EXTENSION_PLANNER_MODEL),
                "checks": checks,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
