"""The registry stage that receipts every tool call (V2-04). It runs first, so policy refusals are
receipted too, and it refuses to replay an operation that may already have taken effect."""

import json
from dataclasses import asdict

from ..core_contracts import ToolResult
from ..registry.registry import Hooks
from . import active, current_receipt

QUIET_EFFECTS = {"observe", "navigate"}


class LedgerHook(Hooks):
    async def before(self, ctx, spec, args):
        ledger = active()
        ctx.receipt = None
        if ledger is None:
            return None
        # Occurrence counts identical calls within this attempt, so a resumed attempt reaches the same
        # operation keys for the same steps and replays them instead of repeating their effects.
        from .chain import digest

        seen = ctx.__dict__.setdefault("occurrences", {})
        key = (spec.name, digest(args or {}))
        occurrence = seen.get(key, 0)
        seen[key] = occurrence + 1
        receipt = ledger.begin(
            occurrence=occurrence,
            run_id=ctx.run_id or "adhoc",
            tool=spec.name,
            capability=spec.capability,
            hand=spec.hand,
            args=args,
            effect=str(getattr(spec.effect, "value", spec.effect)),
            principal=asdict(ctx.principal),
        )
        ctx.receipt = receipt["id"]
        verdict, prior = ledger.replay_decision(receipt["operation_key"], exclude=receipt["id"])
        if verdict == "replay":
            ledger.end(receipt["id"], outcome="ok", content=prior["result_view"] or "", data=None)
            ctx.receipt = None  # Already closed.
            return ToolResult(
                "ok",
                prior["result_view"] or "Already done.",
                data={
                    "replayed": True,
                    "result": prior["result_view"] or "",
                },
            )
        if verdict == "unknown":
            message = (
                "An earlier attempt at this exact step may already have taken effect. Check before retrying."
            )
            ledger.end(receipt["id"], outcome="blocked", content=message)
            ctx.receipt = None
            return ToolResult(
                "blocked", message, data={"error": message}, error_code="UNKNOWN_EARLIER_EFFECT"
            )
        return None

    async def around(self, ctx, spec, args, call):
        receipt = getattr(ctx, "receipt", None)
        token = current_receipt.set(receipt)
        try:
            ledger = active()
            effect = str(getattr(spec.effect, "value", spec.effect))
            if ledger and receipt and effect not in QUIET_EFFECTS:
                # Conservative: an effect may leave the process once the hand runs.
                ledger.dispatched(receipt)
            return await call()
        finally:
            current_receipt.reset(token)

    async def parked(self, ctx, spec, args):
        """The run parked on an interrupt inside the hand, before its effect (V2-05)."""
        ledger, receipt = active(), getattr(ctx, "receipt", None)
        if ledger and receipt:
            ledger.undispatch(receipt, "waiting for the person's approval")
            ledger.end(
                receipt, outcome="cancelled", content="Waiting for the person's approval before acting."
            )
            ctx.receipt = None

    async def after(self, ctx, spec, args, result):
        ledger, receipt = active(), getattr(ctx, "receipt", None)
        if not ledger or not receipt:
            return
        decision = getattr(ctx, "decision", None)
        if decision is not None:
            ledger.decision(receipt, decision.view())
        row = ledger.store.one("SELECT evidence FROM receipts WHERE id=?", (receipt,))
        mirrored = bool(row and json.loads(row["evidence"]))
        for item in result.evidence or []:
            if item.get("kind") == "capability" and mirrored:
                continue  # The capability's own evidence was already mirrored onto this receipt.
            ledger.evidence(receipt, item.get("kind", "tool"), item.get("summary", ""), item)
        ledger.end(
            receipt,
            outcome=result.outcome,
            content=result.content,
            data=result.data,
            artifact_ids=result.artifact_ids,
            dispatched=result.dispatched,
        )
        ctx.receipt = None
