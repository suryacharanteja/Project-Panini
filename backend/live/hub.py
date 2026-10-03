"""Live view hub (V2-12): the latest frame per hand and target, fanned out to viewers at a bounded rate.

In memory only. A frame source (a Playwright page's screencast, a sandbox computer's screenshots, the desktop's
activity thumbnails) publishes; viewers subscribe and get at most `max_fps` frames, always the newest one (older
frames are dropped, never queued). Sources ask `viewers()` so they only produce frames while someone watches.
"""

import asyncio
import base64
import secrets
import time
from collections import deque
from dataclasses import dataclass, field

HANDS = ("chrome", "managed", "sandbox", "desktop", "android")


@dataclass
class _Target:
    jpeg: bytes = b""
    meta: dict = field(default_factory=dict)
    seq: int = 0
    history: deque = field(default_factory=lambda: deque(maxlen=20))
    viewers: int = 0
    waiters: set = field(default_factory=set)  # (loop, event) per subscriber; woken safely from any thread.
    last_view: float = 0.0


class LiveHub:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self._targets: dict[tuple[str, str], _Target] = {}
        self._tokens: dict[str, tuple[str, str]] = {}  # ingest token -> (hand, target)
        # Pull sources: hand -> (async fn(target) -> (image, meta) | None, interval seconds), polled while watched.
        self._pollers: dict[str, tuple] = {}
        self._polling: set[tuple[str, str]] = set()

    def register_poller(self, hand: str, fn, interval_s: float) -> None:
        self._pollers[hand] = (fn, interval_s)

    async def _poll(self, hand: str, target: str) -> None:
        fn, interval = self._pollers[hand]
        try:
            while self.watched(hand, target):
                try:
                    found = await fn(target)
                except Exception:  # noqa: BLE001 -- A failed frame never breaks the live view.
                    found = None
                if found:
                    self.publish(None, hand, target, found[0], found[1])
                await asyncio.sleep(interval)
        finally:
            self._polling.discard((hand, target))

    def _target(self, hand: str, target: str) -> _Target:
        if hand not in HANDS:
            raise ValueError(f"Unknown hand {hand!r}.")
        return self._targets.setdefault((hand, target), _Target())

    # Sources --------------------------------------------------------------------------------------
    def ingest_token(self, hand: str, target: str) -> str:
        """A per-session secret a frame source (for example a Playwright process) posts frames with."""
        self._target(hand, target)
        token = secrets.token_urlsafe(24)
        self._tokens[token] = (hand, target)
        return token

    def resolve(self, token: str) -> tuple[str, str] | None:
        for known, key in self._tokens.items():
            if secrets.compare_digest(known, token or ""):
                return key
        return None

    def revoke(self, token: str) -> None:
        self._tokens.pop(token, None)

    def publish(
        self, run_id: str | None, hand: str, target: str, jpeg: bytes, meta: dict | None = None
    ) -> int:
        entry = self._target(hand, target)
        entry.seq += 1
        entry.jpeg = jpeg
        entry.meta = {**(meta or {}), "run_id": run_id, "ts": time.time(), "seq": entry.seq}
        entry.history.append({k: v for k, v in entry.meta.items()})
        for loop, event in list(entry.waiters):
            try:
                loop.call_soon_threadsafe(event.set)
            except RuntimeError:  # That viewer's loop has closed.
                entry.waiters.discard((loop, event))
        return entry.seq

    def viewers(self, hand: str, target: str) -> int:
        return self._target(hand, target).viewers

    def watched(self, hand: str, target: str, grace_s: float = 5.0) -> bool:
        """Someone is watching, or watched within the grace period (rates drop within 5 s of leaving)."""
        entry = self._target(hand, target)
        return entry.viewers > 0 or self.clock() - entry.last_view < grace_s

    # Viewers --------------------------------------------------------------------------------------
    def touch(self, hand: str, target: str) -> None:
        """A polling viewer (for example the desktop shell reading latest frames) counts as watching."""
        entry = self._target(hand, target)
        entry.last_view = self.clock()
        if hand in self._pollers and (hand, target) not in self._polling:
            self._polling.add((hand, target))
            asyncio.ensure_future(self._poll(hand, target))

    def latest(self, hand: str, target: str) -> tuple[bytes, dict] | None:
        entry = self._targets.get((hand, target))
        if not entry or not entry.jpeg:
            return None
        return entry.jpeg, dict(entry.meta)

    def history(self, hand: str, target: str) -> list[dict]:
        entry = self._targets.get((hand, target))
        return list(entry.history) if entry else []

    async def subscribe(self, hand: str, target: str, *, max_fps: float = 4.0):
        """Yields {seq, ts, url, title, width, height, frame (base64 JPEG)}: newest frame, at most max_fps."""
        entry = self._target(hand, target)
        entry.viewers += 1
        interval = 1.0 / max(0.1, max_fps)
        sent = 0
        waiter = (asyncio.get_running_loop(), asyncio.Event())
        entry.waiters.add(waiter)
        if hand in self._pollers and (hand, target) not in self._polling:
            self._polling.add((hand, target))
            asyncio.ensure_future(self._poll(hand, target))
        try:
            while True:
                if entry.seq == sent:
                    waiter[1].clear()
                    if entry.seq == sent:
                        await waiter[1].wait()
                started = self.clock()
                sent = entry.seq
                yield {**entry.meta, "frame": base64.b64encode(entry.jpeg).decode()}
                wait = interval - (self.clock() - started)
                if wait > 0:
                    await asyncio.sleep(wait)
        finally:
            entry.waiters.discard(waiter)
            entry.viewers -= 1
            entry.last_view = self.clock()

    def describe(self) -> list[dict]:
        return [
            {"hand": h, "target": t, "seq": e.seq, "viewers": e.viewers, "url": e.meta.get("url")}
            for (h, t), e in self._targets.items()
        ]
