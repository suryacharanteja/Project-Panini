"""Async pub/sub event bus, consumed by the SSE endpoint (DD-072).

Reimplemented rather than taken from Sapphire, which is AGPL-3.0 (DD-074).

The replay buffer exists because a browser that reconnects mid-turn would
otherwise miss the events that already fired — leaving the UI showing a stale
state with no way to recover short of a reload.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import deque
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

REPLAY_BUFFER_SIZE = 50
SUBSCRIBER_QUEUE_SIZE = 256


@dataclass(frozen=True, slots=True)
class Event:
    type: str
    payload: dict[str, Any] = field(default_factory=dict)
    seq: int = 0
    ts: str = ""


class EventBus:
    """Fan-out bus with bounded per-subscriber queues.

    A slow subscriber drops its oldest events rather than blocking publishers.
    Back-pressure from a stalled browser tab must never stall the turn loop.
    """

    def __init__(self, replay_size: int = REPLAY_BUFFER_SIZE) -> None:
        self._subscribers: set[asyncio.Queue[Event]] = set()
        self._replay: deque[Event] = deque(maxlen=replay_size)
        self._seq = 0
        self._boot_id = datetime.now(UTC).isoformat()

    @property
    def boot_id(self) -> str:
        """Changes on restart so a reconnecting client can detect it."""
        return self._boot_id

    def publish(self, event_type: str, payload: dict[str, Any] | None = None) -> Event:
        self._seq += 1
        event = Event(
            type=event_type,
            payload=payload or {},
            seq=self._seq,
            ts=datetime.now(UTC).isoformat(),
        )
        self._replay.append(event)

        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # Drop the oldest for this subscriber only; never block the publisher.
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
                with contextlib.suppress(asyncio.QueueFull):
                    queue.put_nowait(event)
        return event

    async def subscribe(
        self, types: Iterable[str] | None = None, replay: bool = True
    ) -> AsyncIterator[Event]:
        """Yield events until the consumer stops iterating."""
        wanted = set(types) if types else None
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_SIZE)
        self._subscribers.add(queue)
        try:
            if replay:
                for event in list(self._replay):
                    if wanted is None or event.type in wanted:
                        yield event
            while True:
                event = await queue.get()
                if wanted is None or event.type in wanted:
                    yield event
        finally:
            self._subscribers.discard(queue)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)
