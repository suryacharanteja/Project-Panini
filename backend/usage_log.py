"""Per-call token and cost telemetry for every LLM and Jev call PANINI makes.

One place records usage so the Admin portal and the token benchmark can compare surfaces
(browser proposer, Jev decisions, router, voice, desktop) before and after optimisation.
Kept in memory: totals since start plus the most recent calls.
"""

import threading
import time
from collections import deque

# USD per million tokens (input, cached input, output). Unknown models cost 0 so the
# numbers never overstate; tokens are always recorded.
PRICES = {
    "typesafe-ai/jev": (0.042, 0.042, 0.0),
    "gpt-4.1": (2.0, 0.5, 8.0),
    "gpt-4.1-mini": (0.4, 0.1, 1.6),
    "gpt-4.1-nano": (0.1, 0.025, 0.4),
}

_lock = threading.Lock()
_recent = deque(maxlen=500)
_totals = {}


def price(model):
    name = str(model or "")
    for key in sorted(PRICES, key=len, reverse=True):
        if name == key or name.startswith(key + "-"):
            return PRICES[key]
    return (0.0, 0.0, 0.0)


def record(surface, model, input_tokens=0, output_tokens=0, cached_tokens=0, cost=None):
    """Record one call. Returns the cost in USD."""
    input_tokens, output_tokens, cached_tokens = (
        int(input_tokens or 0),
        int(output_tokens or 0),
        int(cached_tokens or 0),
    )
    if cost is None:
        fresh, cached, out = price(model)
        cost = ((input_tokens - cached_tokens) * fresh + cached_tokens * cached + output_tokens * out) / 1e6
    entry = {
        "at": time.time(),
        "surface": surface,
        "model": model,
        "input_tokens": input_tokens,
        "cached_tokens": cached_tokens,
        "output_tokens": output_tokens,
        "cost": float(cost),
    }
    with _lock:
        _recent.append(entry)
        total = _totals.setdefault(
            surface,
            {
                "calls": 0,
                "input_tokens": 0,
                "cached_tokens": 0,
                "output_tokens": 0,
                "cost": 0.0,
                "models": set(),
            },
        )
        total["calls"] += 1
        for key in ("input_tokens", "cached_tokens", "output_tokens", "cost"):
            total[key] += entry[key]
        total["models"].add(str(model))
    return entry["cost"]


def record_openai(surface, model, usage):
    """Record a Chat Completions or Responses API `usage` object."""
    if usage is None:
        return record(surface, model)
    inp = getattr(usage, "prompt_tokens", None) or getattr(usage, "input_tokens", 0)
    out = getattr(usage, "completion_tokens", None) or getattr(usage, "output_tokens", 0)
    details = getattr(usage, "prompt_tokens_details", None) or getattr(usage, "input_tokens_details", None)
    cached = getattr(details, "cached_tokens", 0) if details else 0
    return record(surface, model, inp, out, cached or 0)


def record_jev(surface, answer):
    """Record a Jev sidecar response (`usage` and, when present, the gateway `cost`)."""
    usage = (answer or {}).get("usage") or {}
    tokens = int(usage.get("inputTokens") or usage.get("totalTokens") or 0)
    return record(surface, "typesafe-ai/jev", tokens, 0, 0, (answer or {}).get("cost"))


def summary():
    with _lock:
        surfaces = {
            name: {**{k: v for k, v in t.items() if k != "models"}, "models": sorted(t["models"])}
            for name, t in _totals.items()
        }
        recent = list(_recent)[-50:]
    return {
        "surfaces": surfaces,
        "total": {
            "calls": sum(s["calls"] for s in surfaces.values()),
            "input_tokens": sum(s["input_tokens"] for s in surfaces.values()),
            "output_tokens": sum(s["output_tokens"] for s in surfaces.values()),
            "cost": sum(s["cost"] for s in surfaces.values()),
        },
        "recent": recent,
    }


def reset():
    with _lock:
        _recent.clear()
        _totals.clear()
