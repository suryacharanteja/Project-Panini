"""Hands: the code that carries out a tool call.

In this first registry release most hands wrap PANINI's existing implementations so behaviour stays
identical (V2-02 parity): AgentMethodHand runs the v1 agent's own handler for the tool, and
LegacyAdapterHand runs a v1 capability adapter (search, browser, desktop, code) through the caller's
delegate. Later items replace these with native hands (Playwright, sandbox, Android, ...).
"""

from ..core_contracts import Readiness, ToolResult


class AgentMethodHand:
    """Runs the v1 agent's handler for a tool (harness/agent.py Agent._call_v1)."""

    # v1 handlers default missing arguments (a.get(...)); strict schemas still check types and extras.
    lenient_required = True

    def __init__(self, manifest, runtime):
        self.manifest, self.runtime = manifest, runtime

    async def readiness(self):
        return Readiness("ready")

    async def call(self, ctx, spec, args):
        if ctx.agent is None:
            return ToolResult("blocked", f"{spec.name} is only available inside the PANINI agent.")
        from ..harness.memory import MemoryError

        before = len(ctx.agent.evidence)
        try:
            output, final = await ctx.agent._call_v1(spec.name, args)
        except (MemoryError, ValueError, KeyError) as exc:  # The v1 agent's call() turns these into errors.
            output, final = {"error": str(exc).strip("'")}, None
        if final is not None:
            ctx.final = final
        error = output.get("error") if isinstance(output, dict) else None
        # What the v1 handler verified itself (a command's exit and output, an opened target, a saved memory)
        # is this receipt's evidence, so the evidence rule (I2) sees it. Capability evidence ids (no colon) are
        # usually mirrored into the ledger by the coordinator already; the ledger hook skips them then.
        evidence = [
            {"id": e, "kind": "agent" if ":" in str(e) else "capability"} for e in ctx.agent.evidence[before:]
        ]
        return ToolResult("failed" if error else "ok", str(error or ""), data=output, evidence=evidence)


class ControlHand(AgentMethodHand):
    """update_plan, ask_user, finish and run_subagents belong to the agent loop itself."""


class LegacyAdapterHand:
    """A v1 capability adapter as one coarse tool (<capability>_run), with v1 semantics."""

    def __init__(self, manifest, runtime):
        self.manifest, self.runtime = manifest, runtime

    def adapter(self, capability):
        return (self.runtime.get("adapters") or {}).get(capability)

    async def readiness(self, capability=None):
        adapter = self.adapter(capability) if capability else None
        if adapter is None:
            return Readiness("unavailable", f"{capability} is not installed.")
        state = await adapter.availability()
        return Readiness(
            state["state"] if state["state"] == "ready" else "unavailable", state.get("reason", "")
        )

    async def call(self, ctx, spec, args):
        capability = spec.name.removesuffix("_run")
        if ctx.delegate is None:
            return ToolResult("blocked", f"{capability} cannot run in this context.")
        outcome = await ctx.delegate(capability, args["instruction"][:5000])
        ok = outcome.get("outcome") == "verified_complete"
        return ToolResult(
            "ok" if ok else ("blocked" if outcome.get("outcome") == "blocked" else "failed"),
            str(outcome.get("message", "")),
            data={k: outcome.get(k) for k in ("outcome", "message", "results")},
            evidence=[{"id": e} for e in outcome.get("evidence_ids", [])],
            dispatched=outcome.get("outcome") == "unknown_effect",
        )
