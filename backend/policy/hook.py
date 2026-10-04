"""The registry pipeline stage that applies policy to every tool call (V2-03).

shadow  - decide, never act; record where v2 and v1 disagree.
enforce - deny and park are refused; ask goes to the person once (the nested v1 prompts for the same call
          are pre-approved); grant changes and pause stop in-flight calls (invariant I7).
"""

import asyncio
import json

from ..registry.registry import Hooks
from . import current, preapproved, v1_asked


class PolicyHook(Hooks):
    def __init__(self, mode: str | None = None, policy=None):
        self._mode, self._policy = mode, policy

    @property
    def mode(self):
        from .. import v2flags

        return self._mode or v2flags.value("PANINI_V2_POLICY")

    @property
    def policy(self):
        return self._policy or current()

    def enforced(self, ctx, spec=None):
        from ..channels import SURFACES

        return (
            self.mode == "enforce"
            or ctx.principal.kind == "persona"
            or ctx.principal.surface in SURFACES
            or (spec is not None and spec.capability == "android")
        )

    async def before(self, ctx, spec, args):
        if spec.capability == "android":
            ctx.android_policy = None
            ctx.decision = None
            if spec.effect.value != "observe":
                # The Android hand grounds the exact package, field and label from a fresh phone tree
                # before asking once. A ref-only generic approval cannot authorize a changed target.
                return None
        if self.mode == "off" and not self.enforced(ctx, spec):
            return None
        background = bool(getattr(ctx.view, "background", False))
        decision = self.policy.decide(ctx.principal, spec, args, taint=ctx.taint, background=background)
        ctx.decision = decision
        if not self.enforced(ctx, spec):
            return None
        from ..core_contracts import ToolResult

        def refuse(message):
            return ToolResult("blocked", message, data={"error": message}, error_code="POLICY")

        if decision.action == "deny":
            why = (
                "; ".join(decision.reasons)
                or f"{decision.effect.value.replace('_', ' ')} is not allowed here"
            )
            return refuse(f"Not allowed: {why}. The person can do this themselves.")
        from .. import interrupts

        if decision.action == "park" and not (interrupts.active() and ctx.confirm is not None):
            return refuse("This step needs the person's approval; it cannot run in the background yet.")
        if decision.action in {"ask", "park"}:
            if ctx.confirm is None:
                return refuse("This step needs the person's approval, and nobody can be asked here.")
            detail = json.dumps(
                {k: v for k, v in args.items() if k not in decision.redactions}, ensure_ascii=False
            )
            hand = getattr(getattr(getattr(ctx, "agent", None), "registry", None), "hands", {}).get(spec.name)
            if hasattr(hand, "preview"):
                try:  # The hand shows what it is about to touch (a highlighted screenshot for browsers).
                    detail = (await hand.preview(ctx, spec, args)) + "\n" + detail
                except Exception:  # noqa: BLE001 -- A failed preview never blocks asking.
                    pass
            title = f"{spec.title} ({decision.effect.value.replace('_', ' ')})"
            if decision.reasons:
                title += " — " + "; ".join(decision.reasons)[:160]
            if not await ctx.confirm(title[:200], detail[:3000]):
                return refuse("The person did not approve this step. Try a safer approach.")
            ctx.preapproved = True
        return None

    async def around(self, ctx, spec, args, call):
        cell = []
        token_asked = v1_asked.set(cell)
        token_pre = preapproved.set(bool(getattr(ctx, "preapproved", False)))
        try:
            if not self.enforced(ctx, spec):
                return await call()
            return await self._revocable(ctx, spec, args, call)
        finally:
            preapproved.reset(token_pre)
            v1_asked.reset(token_asked)
            ctx.v1_asked = bool(cell)
            ctx.preapproved = False

    async def _revocable(self, ctx, spec, args, call):
        """Run the hand; if PANINI is paused or the call's decision tightens meanwhile, cancel it (I7)."""
        work = asyncio.ensure_future(call())
        while True:
            change = asyncio.ensure_future(self.policy.wait_change())
            done, _ = await asyncio.wait({work, change}, return_when=asyncio.FIRST_COMPLETED)
            if work in done:
                change.cancel()
                return work.result()
            bound = getattr(ctx, "android_policy", None) if spec.capability == "android" else None
            current_spec, current_args = bound or (spec, args)
            again = self.policy.decide(ctx.principal, current_spec, current_args, taint=ctx.taint)
            before = getattr(ctx, "decision", None)
            tightened = again.action == "deny" or (
                again.action != "allow" and before is not None and before.action == "allow"
            )
            if tightened:
                work.cancel()
                from ..core_contracts import ToolResult

                message = (
                    "Stopped: PANINI was paused or a permission changed."
                    if again.action == "deny"
                    else ("Stopped: a permission changed while this step ran.")
                )
                return ToolResult("cancelled", message, data={"error": message}, dispatched=True)

    async def after(self, ctx, spec, args, result):
        decision = getattr(ctx, "decision", None)
        if self.mode == "shadow" and decision is not None:
            self.policy.record_shadow(spec.name, decision, bool(getattr(ctx, "v1_asked", False)))
