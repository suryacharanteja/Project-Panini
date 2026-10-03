"""Takeover (V2-12 §5.4): one state machine for every hand, after OpenBot's control API.

State per (hand, target): holder agent|human, requested, transitioning, resume_snapshot_required, request.
- take: the owner holds the hand; agent tool calls on it are blocked (the run keeps its lease; Stop still works).
- release: the agent holds it again, but its next effect on that hand is refused until it has looked again
  (a snapshot or other observe tool), so it never acts on a page you changed.
- request_takeover: the agent asks you to take over (for example a sign-in); resolving it releases.
"""

import secrets
import time
from dataclasses import dataclass, field


class ControlBlocked(RuntimeError):
    pass


HAND_LABELS = {
    "chrome": "your Chrome",
    "managed": "PANINI's browser",
    "sandbox": "the sandbox computer",
    "desktop": "the desktop",
    "android": "your phone",
}


@dataclass
class Control:
    holder: str = "agent"
    requested: bool = False
    transitioning: bool = False
    resume_snapshot_required: bool = False
    request: dict | None = None
    since: float = field(default_factory=time.time)

    def view(self) -> dict:
        return {
            "holder": self.holder,
            "requested": self.requested,
            "transitioning": self.transitioning,
            "resumeSnapshotRequired": self.resume_snapshot_required,
            "request": self.request,
            "since": self.since,
        }


class Takeover:
    def __init__(self, clock=time.time, on_change=None):
        self.clock = clock
        # (hand, target, view) -> None or awaitable: live events and hands that mirror the state (OpenBot).
        self.listeners = [on_change] if on_change else []
        self._state: dict[tuple[str, str], Control] = {}

    def listen(self, fn) -> None:
        self.listeners.append(fn)

    def state(self, hand: str, target: str) -> Control:
        return self._state.setdefault((hand, target), Control())

    def _changed(self, hand, target):
        import asyncio
        import inspect

        view = self.state(hand, target).view()
        for listener in self.listeners:
            outcome = listener(hand, target, view)
            if inspect.isawaitable(outcome):
                asyncio.ensure_future(outcome)

    def take(self, hand: str, target: str, *, notify: bool = True) -> dict:
        state = self.state(hand, target)
        state.holder, state.requested, state.since = "human", False, self.clock()
        if state.request:
            state.request = {**state.request, "status": "taken"}
        if notify:
            self._changed(hand, target)
        return state.view()

    def release(self, hand: str, target: str, *, notify: bool = True) -> dict:
        state = self.state(hand, target)
        state.holder, state.since = "agent", self.clock()
        state.resume_snapshot_required = True
        state.requested, state.request = False, None
        if notify:
            self._changed(hand, target)
        return state.view()

    def request_takeover(self, hand: str, target: str, reason: str) -> dict:
        state = self.state(hand, target)
        state.requested = True
        state.request = {"id": secrets.token_hex(6), "status": "waiting", "reason": reason[:300]}
        self._changed(hand, target)
        return state.view()

    def gate(self, hand: str, target: str, *, observe: bool) -> None:
        """Called before an agent tool runs on a hand. Observe tools clear the resume-snapshot requirement."""
        state = self._state.get((hand, target))
        if state is None:
            return
        if state.holder == "human":
            raise ControlBlocked(f"You have control of {HAND_LABELS.get(hand, hand)}. PANINI is waiting.")
        if state.resume_snapshot_required:
            if observe:
                state.resume_snapshot_required = False
                return
            raise ControlBlocked(
                f"You handed {HAND_LABELS.get(hand, hand)} back. Look at it again (take a snapshot) before acting."
            )

    def holding(self) -> list[dict]:
        return [
            {"hand": h, "target": t, **s.view()} for (h, t), s in self._state.items() if s.holder == "human"
        ]
