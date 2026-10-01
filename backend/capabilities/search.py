"""Existing retrieval and Jev ranking, isolated behind a private Node worker."""

import asyncio
import json
import logging
import os
import subprocess

from .. import config
from ..panini.contracts import Fault
from .contracts import Outcome, UnavailableAdapter

logger = logging.getLogger(__name__)


class SearchAdapter(UnavailableAdapter):
    def __init__(self):
        super().__init__(
            "search", "Configure OpenAI and AI Gateway credentials, then build the Search worker."
        )
        self.worker = config.ROOT / "panini-core/capabilities/search/dist-worker/worker.mjs"
        self.processes = {}

    async def availability(self):
        ready = self.worker.is_file() and all(
            config.configured(key) for key in ("OPENAI_API_KEY", "AI_GATEWAY_API_KEY")
        )
        return {
            "capability": self.name,
            "state": "ready" if ready else "unavailable",
            "reason": "" if ready else self.reason,
        }

    async def run(self, context, instruction, target):
        if len(instruction) > 300:
            raise Fault("SEARCH_TOO_LONG", "Use a Search instruction of 300 characters or fewer.", 422)
        context.check()
        process = await asyncio.create_subprocess_exec(
            os.getenv("PANINI_NODE", "node"),
            str(self.worker),
            cwd=config.ROOT,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            limit=1024 * 1024,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        task_id = context.task.task_id
        self.processes[task_id] = process
        process.stdin.write(
            (json.dumps({"op": "search", "id": task_id, "request": instruction}) + "\n").encode()
        )
        await process.stdin.drain()
        rows, evidence, successful, failures, ended = {}, [], 0, 0, False
        context.emit("running", "Retrieving and ranking web results.")
        while line := await process.stdout.readline():
            context.check()
            event = json.loads(line)
            if event.get("id") != task_id:
                raise Fault("WORKER_PROTOCOL", "The Search worker returned an invalid response.")
            kind = event.get("type")
            if kind == "error":
                # The person sees a generic message; the log keeps the provider's reason.
                logger.warning("Search worker error: %s", str(event.get("message") or event)[:500])
                raise Fault(
                    "SEARCH_UNAVAILABLE",
                    "Search did not complete. Check provider availability and retry.",
                    503,
                )
            if kind == "lane":
                if event.get("error"):
                    failures += 1
                    continue
                successful += 1
                evidence.append(
                    context.record_evidence(
                        "retrieved_source",
                        "Received ranked source results.",
                        {
                            "source": event.get("source"),
                            "engine": event.get("engine"),
                            "results": [
                                {
                                    "url": item.get("url", "")[:2048],
                                    "title": item.get("title", "")[:200],
                                    "ranked": item.get("ranked", False),
                                }
                                for item in event.get("items", [])[:4]
                            ],
                        },
                    )
                )
                for item in event.get("items", []):
                    if item.get("ranked") and item.get("url", "").startswith(("http://", "https://")):
                        rows[item["url"]] = item
                context.emit("running", f"Received {len(rows)} ranked results.")
            if kind == "end":
                ended = True
                break
        if not ended:
            raise Fault(
                "SEARCH_WORKER_FAILED", "The Search worker disconnected before completing the request.", 503
            )
        if not successful:
            return Outcome("partial", "No source completed successfully. Try the search again.")
        ordered = sorted(rows.values(), key=lambda v: v.get("relevance", 0), reverse=True)[:10]
        return Outcome(
            "partial" if failures else "verified_complete",
            f"Retrieved {len(ordered)} ranked results."
            + (f" {failures} source queries failed." if failures else ""),
            [{"title": row["title"], "url": row["url"], "detail": row.get("snippet", "")} for row in ordered],
            evidence,
        )

    async def stop(self, task_id):
        process = self.processes.get(task_id)
        if process and process.returncode is None:
            process.kill()
            await asyncio.wait_for(process.wait(), 3)

    async def release(self, task_id):
        await self.stop(task_id)
        self.processes.pop(task_id, None)

    async def close(self):
        await asyncio.gather(*(self.release(key) for key in list(self.processes)))
