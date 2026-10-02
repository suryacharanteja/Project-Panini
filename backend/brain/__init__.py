"""PANINI v2 one brain: model tiers, budgets, run records and threads
(docs/panini-enhancements/lld/V2-06-one-brain.md)."""

from .. import v2flags
from .budget import STOP_MESSAGES, Budget, Meter
from .models import TIER_QUESTIONS, ModelRouter, choose_tier, tier_models
from .runs import Runs
from .threads import Threads


def enabled() -> bool:
    return v2flags.enabled("PANINI_V2_ONE_BRAIN")


__all__ = [
    "STOP_MESSAGES",
    "TIER_QUESTIONS",
    "Budget",
    "Meter",
    "ModelRouter",
    "Runs",
    "Threads",
    "choose_tier",
    "enabled",
    "tier_models",
]
