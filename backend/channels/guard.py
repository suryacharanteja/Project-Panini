"""Stop channel work when account access is removed, including between resumed tool calls."""

import asyncio

from ..core_contracts import ToolResult
from ..registry.registry import Hooks


class ChannelGuard(Hooks):
    def __init__(self, manager):
        self.manager = manager

    def allowed(self, ctx):
        job = self.manager.job(ctx.run_id)
        if not job:
            return True
        task = self.manager.h.tasks.get(ctx.run_id)
        return self.manager.permitted(job) and task["status"] == "running"

    async def before(self, ctx, spec, args):
        if not self.allowed(ctx):
            return ToolResult(
                "blocked", "This channel task is no longer authorized.", error_code="CHANNEL_DISABLED"
            )

    async def around(self, ctx, spec, args, call):
        if not self.manager.job(ctx.run_id):
            return await call()
        work = asyncio.create_task(call())
        try:
            while not work.done():
                await asyncio.wait({work}, timeout=0.1)
                if not self.allowed(ctx):
                    work.cancel()
                    await asyncio.gather(work, return_exceptions=True)
                    return ToolResult("cancelled", "Channel access was removed.", dispatched=True)
            return work.result()
        finally:
            if not work.done():
                work.cancel()
                await asyncio.gather(work, return_exceptions=True)
