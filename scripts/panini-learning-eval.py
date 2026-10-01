"""Live learning eval: identity, automatic memory and a lesson learned from a correction.

Real models and Jev, through the real coordinator. Runtime data goes to a temporary folder, so
your memories and lessons are untouched.

    .venv\\Scripts\\python.exe scripts/panini-learning-eval.py
"""

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ["PANINI_DATA_DIR"] = tempfile.mkdtemp(prefix="panini-learn-eval-")

from backend.app import create_app  # noqa: E402
from backend.panini.contracts import Turn  # noqa: E402
from backend.panini.settings import Settings  # noqa: E402


async def main():
    app = create_app(panini_settings=Settings(enabled=True, bootstrap_token="l" * 40))
    c = app.state.panini
    learning = app.state.harness.learning
    _, principal = c.auth.issue("controller")
    s = c.get((await c.create_session(principal, uuid4()))["session_id"])
    await c.acquire(s, principal)
    revision = 0

    async def say(text):
        nonlocal revision
        revision += 1
        c.renew(s, principal, s.lease_epoch, revision)
        await c.submit(s, principal, s.lease_epoch, Turn(command_id=uuid4(), text=text))
        await asyncio.wait_for(s.active.runner, 240)
        await asyncio.gather(*list(c._learning_jobs), return_exceptions=True)  # Let learning finish.
        return s.last_task

    results = []

    def check(name, ok, detail):
        results.append(ok)
        print(f"{'PASS' if ok else 'FAIL'} {name}: {str(detail)[:160]!r}", flush=True)

    who = await say("Who are you?")
    check("identity", "panini" in who.message.lower(), who.message)
    founder = await say("Who founded you and which company built you?")
    check(
        "founder",
        "charan" in founder.message.lower() and "ndimensions" in founder.message.lower().replace(" ", ""),
        founder.message,
    )
    await say("By the way, I'm an AI manager based in Dubai and I prefer short bullet-point answers.")
    auto = [m["text"] for m in learning.memory.list() if m["source"] == "auto"]
    check("auto memory", any("dubai" in m.lower() or "bullet" in m.lower() for m in auto), auto)
    first = await say("Give me three facts about the Eiffel Tower.")
    await say("That was too long. Always keep answers to three short bullet points with no introduction.")
    lessons = [row["text"] for row in learning.lessons("active")]
    check("lesson learned from correction", bool(lessons), lessons)
    second = await say("Give me three facts about the Great Wall of China.")
    shorter = len(second.message) <= len(first.message)
    check("lesson applied", shorter and bool(learning.store.all("SELECT * FROM grades")), second.message)
    print(f"\n{sum(results)}/{len(results)} passed")
    await c.close()


if __name__ == "__main__":
    asyncio.run(main())
