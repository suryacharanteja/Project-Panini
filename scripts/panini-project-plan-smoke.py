"""Live, isolated smoke check for project planning and first-step queueing."""

import asyncio
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.harness.connectors import Connectors  # noqa: E402
from backend.harness.runtime import Harness  # noqa: E402
from backend.harness.store import Store  # noqa: E402


async def main():
    with tempfile.TemporaryDirectory(prefix="panini-plan-smoke-") as directory:
        h = Harness(
            Store(Path(directory) / "h.sqlite3"),
            connectors=Connectors(Path(directory) / "connectors.json"),
        )
        try:
            project = h.projects.create(
                "Research launch changes",
                "Find the latest official release notes for Python and summarise the most relevant changes.",
                "Use primary sources and cite what you find.",
            )
            draft = await h.project_planner.draft(project["id"])
            assert draft["status"] == "draft" and 1 <= len(draft["steps"]) <= 6
            assert not h.tasks.list(), "Drafting must not dispatch work."
            approved = h.project_planner.approve(project["id"], draft["revision"])
            assert approved["status"] == "running" and len(h.tasks.list()) == 1
            print(
                f"Project plan smoke passed: {len(draft['steps'])} proposed steps; first queued after approval."
            )
        finally:
            await h.close()


if __name__ == "__main__":
    asyncio.run(main())
