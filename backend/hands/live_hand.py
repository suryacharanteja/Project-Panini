"""request_takeover (V2-12 §5.4): the agent asks the person to take over a hand and waits until it is handed back.

It opens a durable `takeover` interrupt (any surface can answer it, from the TakeoverCard) and marks the hand as
requested in the shared takeover state, so live views show the request. It returns when:
- the person answers the card ("Done"): control comes back to PANINI, which must look again before acting;
- the person takes control in a live view and then gives it back;
- the person declines, or the request expires (30 minutes).
"""

import asyncio
import time

from .. import live
from ..core_contracts import Readiness, ToolResult

TTL_S = 30 * 60
TARGETS = {"chrome": "attached", "managed": "default", "desktop": "pc"}


class LiveHand:
    lenient_required = False

    def __init__(self, manifest, runtime, *, clock=time.monotonic, poll_s=0.5):
        self.manifest, self.runtime = manifest, runtime
        self.clock, self.poll_s = clock, poll_s

    async def readiness(self, capability=None):
        if not live.enabled():
            return Readiness("unavailable", "Live view is off (PANINI_LIVE_VIEW).")
        return Readiness("ready")

    @staticmethod
    def target_for(ctx, hand: str) -> str:
        if hand == "sandbox":
            from .sandbox import SandboxHand

            return SandboxHand.computer_for(ctx)
        return TARGETS[hand]

    async def call(self, ctx, spec, args):
        from ..interrupts import InterruptSpec, active

        hand, reason = str(args.get("hand", "")), str(args.get("reason", "")).strip()[:300]
        if hand not in TARGETS and hand != "sandbox":
            return ToolResult("blocked", "Choose chrome, managed, sandbox or desktop.")
        target = self.target_for(ctx, hand)
        takeover = live.takeover()
        takeover.request_takeover(hand, target, reason)
        service = active()
        interrupt = None
        if service is not None:
            interrupt = service.open(
                InterruptSpec(
                    "takeover",
                    "agent_request",
                    f"Please take over: {reason}"[:200],
                    reason,
                    preview={"hand": hand, "target": target},
                    level="tap",
                    ttl_s=TTL_S,
                ),
                run_id=str(getattr(ctx, "run_id", "") or "live"),
                owner_principal={"surface": "live"},
            )
        took = False
        deadline = self.clock() + TTL_S
        while True:
            state = takeover.state(hand, target)
            took = took or state.holder == "human"
            if took and state.holder == "agent":  # Taken in a live view and given back.
                if interrupt is not None:
                    service.cancel(interrupt["id"], "Handed back in the live view.")
                return self._back(hand)
            if interrupt is not None:
                status = service.get(interrupt["id"])["status"]
                if status == "resolved":
                    takeover.release(hand, target)
                    return self._back(hand)
                if status in ("cancelled", "expired"):
                    takeover.release(hand, target, notify=False)
                    return ToolResult(
                        "blocked", "The person did not take over. Do not do this step yourself."
                    )
            if self.clock() > deadline:
                takeover.release(hand, target, notify=False)
                return ToolResult("blocked", "Nobody took over in 30 minutes. Do not do this step yourself.")
            await asyncio.sleep(self.poll_s)

    @staticmethod
    def _back(hand):
        label = live.control.HAND_LABELS.get(hand, hand)
        return ToolResult(
            "ok",
            f"The person is done and handed {label} back. Take a snapshot before acting: the page may have changed.",
            data={"hand": hand, "handed_back": True},
        )
