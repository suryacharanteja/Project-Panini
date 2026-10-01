"""Opt-in provider integration; uses a short public documentation query."""

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.capabilities.search import SearchAdapter


async def main():
    adapter = SearchAdapter()
    task_id = str(uuid4())
    context = SimpleNamespace(
        task=SimpleNamespace(task_id=task_id),
        check=lambda: None,
        emit=lambda *args, **kwargs: None,
        record_evidence=lambda *args: str(uuid4()),
    )
    try:
        outcome = await asyncio.wait_for(
            adapter.run(
                context, "Find Microsoft's official Windows UI Automation overview documentation", None
            ),
            95,
        )
        assert outcome.evidence_ids and outcome.results, "No source-backed results were returned"
        assert all(item["url"].startswith(("https://", "http://")) for item in outcome.results)
        print(
            json.dumps(
                {
                    "outcome": outcome.outcome,
                    "result_count": len(outcome.results),
                    "evidence_count": len(outcome.evidence_ids),
                    "sources": [item["url"] for item in outcome.results[:3]],
                }
            )
        )
    finally:
        await adapter.close()


if __name__ == "__main__":
    asyncio.run(main())
