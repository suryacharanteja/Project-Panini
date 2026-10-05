"""Dry-run one local coordinator route; no desktop action is dispatched."""

import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


async def main():
    from backend.panini import planning

    # Keep this probe offline even if .env or Credential Manager has cloud keys.
    os.environ.update(
        PANINI_LOCAL_MODE="fallback",
        OPENAI_API_KEY="",
        AI_GATEWAY_API_KEY="",
        PANINI_V2_ONE_BRAIN="false",
        PANINI_AGENT_DEFAULT="false",
    )
    model = os.getenv("PANINI_LOCAL_MODEL", "").strip()
    if not model:
        raise SystemExit("Set PANINI_LOCAL_MODEL to a loopback model before running the probe.")
    session = SimpleNamespace(
        history=[],
        selected={},
        results={},
        last_task=None,
        pending=None,
        inventory={
            "desktop": {
                "state": "ready",
                "applications": [{"application_id": "notepad", "label": "Notepad"}],
            },
            "agent": {"state": "unavailable"},
            "code": {"state": "unavailable"},
            "browser": {"state": "unavailable"},
            "search": {"state": "unavailable"},
        },
    )
    turn = SimpleNamespace(
        text="Open Notepad and type hello without saving",
        capability_hint=None,
        selected_result_id=None,
    )
    started = time.monotonic()
    report = {
        "time_utc": datetime.now(timezone.utc).isoformat(),
        "model": model,
        "case": turn.text,
        "scope": "planner dry run; installed-app inventory is synthetic; no OS action",
        "attempts": [],
    }
    original_parse = planning.parse

    async def trace_parse(rules, payload, schema, **kwargs):
        try:
            proposal = await original_parse(rules, payload, schema, **kwargs)
            report["attempts"].append(
                {"feedback": payload.get("schema_feedback"), "proposal": proposal.model_dump()}
            )
            return proposal
        except Exception as exc:  # noqa: BLE001 -- The probe preserves rejected proposals as errors.
            report["attempts"].append({"feedback": payload.get("schema_feedback"), "error": str(exc)[:500]})
            raise

    planning.parse = trace_parse
    try:
        plan = await planning.route(session, turn)
        report["outcome"] = "planned"
        report["plan"] = plan
    except Exception as exc:  # noqa: BLE001 -- This probe records even rejected local proposals.
        report["outcome"] = "rejected"
        report["error_type"] = type(exc).__name__
        report["error_code"] = getattr(exc, "code", "")
        report["error"] = str(exc)[:500]
    report["elapsed_seconds"] = round(time.monotonic() - started, 3)
    output = ROOT / "artifacts" / "local-eval" / "notepad-route.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    if report["outcome"] != "planned":
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
