"""Bound paid work independently of the lifetime of an HTTP connection."""

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar

T = TypeVar("T")


class CapacityError(Exception):
    pass


class WorkPool:
    """Reject overload immediately; a disconnected caller cannot free a busy slot."""

    def __init__(self, limit: int):
        if limit < 1:
            raise ValueError("Work limit must be positive")
        self.limit = limit
        self._tasks: set[asyncio.Task] = set()
        self._closing = False

    @property
    def active(self) -> int:
        return len(self._tasks)

    async def run(self, operation: Callable[[], Coroutine[Any, Any, T]]) -> T:
        if self._closing or self.active >= self.limit:
            raise CapacityError("This service is busy. Please retry shortly.")
        # No await between checking capacity and registering the owned task.
        task = asyncio.create_task(operation())
        self._tasks.add(task)
        task.add_done_callback(self._finished)
        return await asyncio.shield(task)

    def _finished(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if not task.cancelled():
            task.exception()  # Retrieve failures even after the caller disconnects.

    async def close(self) -> None:
        self._closing = True
        await asyncio.gather(*self._tasks, return_exceptions=True)
