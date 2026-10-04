"""Bounded real-provider research-to-writing team check on synthetic notebook evidence."""

import asyncio
import json
import os
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [
    str(ROOT),
    str(ROOT / "panini-core/capabilities/cli/src"),
    str(ROOT / "panini-core/voice/live-spoken-conversation"),
]


async def main():
    from backend.brain.budget import Budget
    from backend.brain.control_tools import run_team
    from backend.harness.runtime import Harness, default_model
    from backend.harness.skills import Skills
    from backend.harness.store import Store

    os.environ["PANINI_PERSONAS"] = "true"
    os.environ["PANINI_SPACES"] = "true"
    os.environ["PANINI_JEV_GATES"] = "false"
    report = {
        "at": datetime.now(UTC).isoformat(),
        "passed": False,
        "kind": "live synthetic notebook research-to-writing team",
        "calls": [],
    }

    def model():
        instance = default_model()
        stream = instance._stream

        async def bounded(request, on_text):
            return await stream({**request, "max_output_tokens": 3000}, on_text)

        instance._stream = bounded
        report["model"] = getattr(instance, "model", None)
        return instance

    with tempfile.TemporaryDirectory(prefix="panini-team-live-") as temporary:
        directory = Path(temporary)
        h = Harness(
            Store(directory / "h.db"), model_factory=model, skills=Skills(user_root=directory / "skills")
        )
        try:
            shared = h.spaces.create_space("Synthetic team evidence")
            page = h.spaces.create(
                shared["id"],
                "Lumen mission facts",
                "The Lumen probe launches on 14 November 2042 to Europa Station. Its payload is 73 kilograms.",
            )
            h.personas.create(
                "scout",
                name="Scout",
                instructions="Research notebook facts using pages_search and pages_read. Return concise source facts with page ID and revision. Never invent facts.",
            )
            h.personas.create(
                "quill",
                name="Quill",
                instructions="Write a two-sentence mission brief from the preceding Scout results in shared context. Cite the source page ID and revision. Do not invent facts.",
            )
            for persona in ("scout", "quill"):
                h.personas.set_permissions(persona, ["spaces_read"])
                h.spaces.grant(shared["id"], persona, True)

            async def refuse(*args):
                return {"outcome": "blocked", "message": "This check uses only notebook evidence."}

            def observe(event, **data):
                if event in {"tool.started", "subagent.started", "subagent.finished"}:
                    report["calls"].append(
                        {
                            "event": event,
                            **{
                                k: data.get(k)
                                for k in ("name", "persona_id", "outcome")
                                if data.get(k) is not None
                            },
                        }
                    )

            lead = h.agent(delegate=refuse, capabilities=(), max_steps=20, deadline_seconds=160)
            lead.thread_id = h.sessions.create("Synthetic team check")
            lead.goal = "Scout: research Lumen mission facts from the notebook. Quill: write a two-sentence brief using Scout results. Include launch date, destination, payload, source page ID and revision."
            lead.budget_override = Budget(max_steps=20, max_seconds=160, max_usd=0.20, daily_usd=0.50)
            lead.durable = True
            lead.observe = observe
            lead._start_run(None)
            async with asyncio.timeout(170):
                result = await run_team(lead, lead.goal, ["scout", "quill"])
            report["result"] = result
            report["page_id"] = page["id"]
            report["messages"] = h.store.all(
                "SELECT persona_id,text FROM persona_messages WHERE thread_id=?", (lead.thread_id,)
            )
            report["usd"] = lead.brain["meter"].usd
            members = result["results"]
            report["passed"] = (
                len(members) == 2
                and [r["persona_id"] for r in members] == ["scout", "quill"]
                and all(r["outcome"] in {"reply", "verified_complete"} for r in members)
                and all(
                    page["id"] in r["message"]
                    and "2042" in r["message"]
                    and "Europa" in r["message"]
                    and "73" in r["message"]
                    for r in members
                )
                and any(c.get("name") in {"pages_search", "pages_read"} for c in report["calls"])
                and {r["persona_id"] for r in report["messages"]} == {"scout", "quill"}
            )
            h.runs.finish(
                lead.brain["run"],
                "completed" if report["passed"] else "failed",
                result={
                    "outcome": "reply" if report["passed"] else "failed",
                    "message": "Synthetic qualification",
                },
                usage=lead.brain["meter"].view(),
            )
        except Exception as exc:
            report["error"] = type(exc).__name__
            from backend.ledger.redact import redact_result, scrub

            report["detail"] = redact_result(scrub(str(exc), [os.getenv("OPENAI_API_KEY", "")]))
        finally:
            h.store.close()
    output = ROOT / "artifacts/personas/team-live.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        prior = json.loads(output.read_text())
        attempts = prior.get("attempts", [])
    except (OSError, ValueError):
        attempts = []
    output.write_text(json.dumps({**report, "attempts": [*attempts, report]}, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
