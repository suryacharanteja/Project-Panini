"""Adapters do not own session lifetime or subscriber connections."""

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class Outcome:
    outcome: str
    message: str
    results: list = field(default_factory=list)
    evidence_ids: list = field(default_factory=list)


class Adapter(Protocol):
    async def availability(self) -> dict: ...
    async def targets(self) -> list: ...
    async def select(self, choice_id: str) -> dict: ...
    async def run(self, context, instruction: str, target: dict | None) -> Outcome: ...
    async def stop(self, task_id: str) -> None: ...
    async def release(self, task_id: str) -> None: ...
    async def close(self) -> None: ...


class UnavailableAdapter:
    def __init__(self, name, reason):
        self.name, self.reason = name, reason

    async def availability(self):
        return {"capability": self.name, "state": "unavailable", "reason": self.reason}

    async def targets(self):
        return []

    async def select(self, choice_id):
        from ..panini.contracts import Fault

        raise Fault("CAPABILITY_UNAVAILABLE", self.reason, 503)

    async def run(self, context, instruction, target):
        return Outcome("blocked", self.reason)

    async def stop(self, task_id):
        pass

    async def release(self, task_id):
        pass

    async def close(self):
        pass
