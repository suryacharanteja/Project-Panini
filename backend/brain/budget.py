"""Per-run and per-day budgets (docs/panini-enhancements/lld/V2-06-one-brain.md §5.7).

Steps and wall time were already bounded in v1; v2 adds cost. Costs come from usage_log prices on each
model turn's usage. A turn without usage is counted at a fixed upper estimate (fail closed on cost).
"""

import os
import time
from dataclasses import asdict, dataclass

# A model turn with no usage report counts as this many tokens (input, output).
MISSING_USAGE = (30_000, 2_000)


def _number(key, default):
    try:
        return float(os.getenv(key, "") or default)
    except ValueError:
        return float(default)


@dataclass(frozen=True)
class Budget:
    max_steps: int
    max_seconds: float
    max_usd: float
    daily_usd: float

    @classmethod
    def for_mode(cls, background: bool) -> "Budget":
        return cls(
            max_steps=int(_number("PANINI_RUN_MAX_STEPS", 16 if background else 24)),
            max_seconds=_number("PANINI_RUN_MAX_SECONDS", 900),
            max_usd=_number("PANINI_RUN_MAX_USD", 0.20 if background else 0.50),
            daily_usd=_number("PANINI_DAILY_MAX_USD", 5.00),
        )

    def view(self) -> dict:
        return asdict(self)


def turn_cost(model_name: str, usage) -> float:
    from .. import usage_log

    fresh, cached, out = usage_log.price(model_name)
    if not isinstance(usage, dict) or not usage:
        return (MISSING_USAGE[0] * fresh + MISSING_USAGE[1] * out) / 1e6
    inp = int(usage.get("input_tokens") or 0)
    hit = int((usage.get("input_tokens_details") or {}).get("cached_tokens") or 0)
    return ((inp - hit) * fresh + hit * cached + int(usage.get("output_tokens") or 0) * out) / 1e6


class Meter:
    """What one run has used so far, and which limit (if any) it crossed."""

    def __init__(self, budget: Budget, spent_today=lambda: 0.0, clock=time.monotonic):
        self.budget, self.spent_today, self.clock = budget, spent_today, clock
        self.started = clock()
        self.usd, self.steps, self.input_tokens, self.output_tokens = 0.0, 0, 0, 0
        self.daily_approved = False

    def add_turn(self, model_name: str, usage) -> float:
        cost = turn_cost(model_name, usage)
        self.usd += cost
        self.steps += 1
        if isinstance(usage, dict):
            self.input_tokens += int(usage.get("input_tokens") or 0)
            self.output_tokens += int(usage.get("output_tokens") or 0)
        return cost

    def exceeded(self) -> str | None:
        if self.usd > self.budget.max_usd:
            return "cost"
        if self.clock() - self.started > self.budget.max_seconds:
            return "time"
        return None

    def over_daily(self) -> bool:
        return not self.daily_approved and self.spent_today() + self.usd >= self.budget.daily_usd

    def view(self) -> dict:
        return {
            "usd": round(self.usd, 6),
            "steps": self.steps,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "seconds": round(self.clock() - self.started, 2),
        }


STOP_MESSAGES = {
    "cost": "I stopped at the cost limit for this task.",
    "time": "The task reached its time limit before finishing.",
    "daily": "I stopped because today's PANINI spending limit was reached.",
}
