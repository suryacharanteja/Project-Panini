"""Native shell OS state notices; no free-form payload crosses the boundary."""

from ..rules import KINDS, TriggerEvent


async def receive(bus, kind):
    if kind not in KINDS["os"]:
        raise ValueError("Unsupported OS event.")
    now = bus.clock()
    return await bus.emit(TriggerEvent("os", kind, {"state": kind}, now, f"{kind}:{int(now // 2)}", False))
