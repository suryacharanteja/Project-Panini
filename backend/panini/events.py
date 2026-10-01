"""Bounded ordered history; subscriber lifetime never owns work."""

import asyncio
import json
from collections import deque
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime

from .contracts import Fault


@dataclass(eq=False)
class Subscriber:
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=128))
    bytes: int = 0
    overflow: bool = False


class EventHub:
    def __init__(self, instance_id, session_id, max_events=1000, max_bytes=4 * 1024 * 1024):
        self.instance_id, self.session_id = instance_id, session_id
        self.max_events, self.max_bytes = max_events, max_bytes
        self.history, self.subscribers = deque(), set()
        self.sequence, self.bytes = 0, 0

    def publish(
        self, kind, data=None, *, task_id=None, task_revision=None, capability=None, target_label=None
    ):
        event = {
            "schema_version": 1,
            "instance_id": self.instance_id,
            "session_id": self.session_id,
            "sequence": self.sequence + 1,
            "timestamp": datetime.now(UTC).isoformat(),
            "type": kind,
            "task_id": task_id,
            "task_revision": task_revision,
            "capability": capability,
            "target_label": target_label,
            "data": deepcopy(data or {}),
        }
        size = len(json.dumps(event, ensure_ascii=False).encode())
        if size > 65536:
            raise Fault("EVENT_TOO_LARGE", "Activity detail exceeded its allowed size.", 413)
        self.sequence += 1
        self.history.append((event, size))
        self.bytes += size
        while len(self.history) > self.max_events or self.bytes > self.max_bytes:
            self.bytes -= self.history.popleft()[1]
        for sub in list(self.subscribers):
            if sub.queue.full() or sub.bytes + size > 512 * 1024:
                sub.overflow = True
                self.subscribers.discard(sub)
                continue
            sub.queue.put_nowait((event, size))
            sub.bytes += size
        return event

    def subscribe(self, after):
        oldest = self.history[0][0]["sequence"] if self.history else self.sequence + 1
        if after < oldest - 1 or after > self.sequence:
            raise Fault("CURSOR_EXPIRED", "Reload the activity snapshot to reconnect.", 409, "resync")
        if len(self.subscribers) >= 8:
            raise Fault("SUBSCRIBER_LIMIT", "Too many activity connections.", 429)
        sub = Subscriber()
        # No await between capturing backlog and registering: atomic in the owner event loop.
        backlog = [event for event, _ in self.history if event["sequence"] > after]
        self.subscribers.add(sub)
        return sub, backlog

    def unsubscribe(self, sub):
        self.subscribers.discard(sub)

    @staticmethod
    def project(event, principal):
        result = deepcopy(event)
        if (
            principal.role == "observer"
            and event["type"].startswith("caption.")
            and not principal.share_captions
        ):
            result["data"] = {"redacted": True}
        return result
