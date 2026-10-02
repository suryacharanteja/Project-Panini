from backend.core_contracts import Readiness, ToolResult


class WeatherHand:
    def __init__(self, manifest, runtime):
        self.manifest = manifest

    async def readiness(self):
        return Readiness("ready")

    async def call(self, ctx, spec, args):
        return ToolResult("ok", "clear", data={"city": args["city"], "sky": "clear"})
