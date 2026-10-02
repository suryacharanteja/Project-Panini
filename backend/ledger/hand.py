"""activity_search: "What did you do yesterday?" answered from PANINI's own receipts (V2-04 §5.10)."""

import time

from ..core_contracts import Readiness, ToolResult
from . import active


class ActivityHand:
    def __init__(self, manifest, runtime):
        self.manifest = manifest

    async def readiness(self):
        return Readiness("ready" if active() else "unavailable", "" if active() else "The ledger is off.")

    async def call(self, ctx, spec, args):
        ledger = active()
        if ledger is None:
            return ToolResult(
                "blocked", "The activity ledger is off.", data={"error": "The activity ledger is off."}
            )
        hours = max(1, min(int(args.get("since_hours") or 24), 24 * 90))
        rows = ledger.query(
            text=(args.get("query") or "").strip() or None, since=time.time() - hours * 3600, limit=30
        )
        items = [
            {
                "when": time.strftime("%Y-%m-%d %H:%M", time.localtime(r["started_at"])),
                "tool": r["tool"],
                "effect": r["effect"],
                "outcome": r["outcome"],
                "detail": (r.get("result_view") or "")[:200],
            }
            for r in rows
            if r["tool"] not in {"run", "activity_search"}
        ]
        return ToolResult("ok", f"{len(items)} actions", data={"actions": items, "hours": hours})
