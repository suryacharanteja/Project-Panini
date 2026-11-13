"""Bounded live notebook citation probe against synthetic pages in a throwaway database."""

import asyncio
import json
import os
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "panini-core/capabilities/cli/src"))
sys.path.insert(0, str(ROOT / "panini-core/voice/live-spoken-conversation"))


async def main():
    from backend.harness.runtime import Harness
    from backend.harness.skills import Skills
    from backend.harness.store import Store

    os.environ["PANINI_SPACES"] = "true"
    os.environ["PANINI_JEV_GATES"] = "false"
    report = {
        "at": datetime.now(UTC).isoformat(),
        "kind": "live synthetic notebook citation",
        "passed": False,
    }
    with tempfile.TemporaryDirectory(prefix="panini-spaces-live-") as temporary:
        directory = Path(temporary)
        h = Harness(Store(directory / "h.db"), skills=Skills(user_root=directory / "skills"))
        try:
            space = h.spaces.create_space("Synthetic qualification")
            page = h.spaces.create(
                space["id"],
                "Lumen launch",
                "Lumen's launch date is 14 November 2042. The approved destination is Europa Station.",
            )
            calls = []

            async def refuse(capability, goal):
                return {"outcome": "blocked", "message": "Only notebook tools are available in this probe."}

            def emit(event, message=None, **data):
                if event == "tool.started":
                    calls.append(data.get("name"))

            agent = h.agent(
                delegate=refuse, emit=emit, capabilities=(), private=True, max_steps=8, deadline_seconds=80
            )
            agent.durable = True
            agent.observe = emit
            report["model"] = getattr(agent.model, "model", None)
            # Explicitly bound this qualification request, independently of provider defaults.
            stream = agent.model._stream

            async def bounded_stream(request, on_text):
                return await stream({**request, "max_output_tokens": 4096}, on_text)

            agent.model._stream = bounded_stream
            async with asyncio.timeout(100):
                result = await agent.run(
                    "Search my notebook using pages_search for Lumen. What is its approved launch date and destination? Cite the page ID and revision in your answer. Use only notebook evidence."
                )
            report.update(
                {
                    "outcome": result.outcome,
                    "message": result.message,
                    "calls": calls,
                    "page_id": page["id"],
                    "revision": 1,
                }
            )
            report["passed"] = (
                "pages_search" in calls
                and page["id"] in result.message
                and "2042" in result.message
                and "Europa" in result.message
                and result.outcome in {"reply", "verified_complete"}
            )
        except Exception as exc:
            report["error"] = type(exc).__name__
            from backend.ledger.redact import redact_result, scrub

            report["detail"] = redact_result(scrub(str(exc), [os.getenv("OPENAI_API_KEY", "")]))
        finally:
            h.store.close()
    output = ROOT / "artifacts/spaces/live-citation.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
