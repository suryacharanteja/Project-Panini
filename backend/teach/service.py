"""Teach-by-demonstration composition and the model's permission-neutral use_skill tool."""

from .. import v2flags
from ..core_contracts import Readiness, ToolResult
from .bundle import Bundles
from .recorder import Recorder
from .replay import Replayer


class Teach:
    def __init__(self, harness):
        self.h = harness
        self.bundles = Bundles(harness)
        self.replayer = Replayer(harness, self.bundles)
        self.recorder = Recorder(harness)
        harness.skills.on_delete = self.bundles.retire

    def valid_binding(self, ctx):
        binding = getattr(ctx, "teach_binding", None)
        if not binding:
            return True
        row = self.h.store.one("SELECT state FROM teach_versions WHERE name=? AND version=?", binding)
        return bool(row and row["state"] == "approved")

    def allowed(self):
        return (
            all(v2flags.enabled(key) for key in ("PANINI_TEACH", "PANINI_V2_LEDGER", "PANINI_V2_INTERRUPTS"))
            and not self.h.policy.paused()
        )

    def require(self):
        if not self.allowed():
            raise ValueError("Enable Teach, the action ledger and durable reviews; PANINI must be unpaused.")

    async def close(self):
        await self.replayer.stop()
        await self.recorder.stop(discard=True)


class TeachHand:
    def __init__(self, manifest, runtime):
        self.h = runtime.get("harness")

    async def readiness(self, capability=None):
        return Readiness("ready" if self.h and self.h.teach.allowed() else "needs_setup")

    async def call(self, ctx, spec, args):
        try:
            if not self.h:
                return ToolResult("blocked", "The harness is unavailable.")
            self.h.teach.require()
            if ctx.principal.kind not in {"owner", "persona"} or not ctx.view.private or ctx.view.background:
                return ToolResult("blocked", "Reviewed demonstrations are reserved for private owner tasks.")
            if self.h.teach.recorder.current:
                return ToolResult("blocked", "The owner is recording. Stop recording before replaying.")
            return await self.h.teach.replayer.run(ctx, args["name"], args["inputs"])
        except ValueError as exc:
            return ToolResult("blocked", str(exc)[:500])
