"""Live view and takeover (V2-12): one hub of frames and one takeover state machine shared by every hand."""

from .control import ControlBlocked, Takeover
from .hub import LiveHub

_hub: LiveHub | None = None
_takeover: Takeover | None = None


def hub() -> LiveHub:
    global _hub
    if _hub is None:
        _hub = LiveHub()
    return _hub


def takeover() -> Takeover:
    global _takeover
    if _takeover is None:
        _takeover = Takeover()
    return _takeover


def enabled() -> bool:
    from .. import v2flags

    return v2flags.enabled("PANINI_LIVE_VIEW")


__all__ = ["ControlBlocked", "LiveHub", "Takeover", "enabled", "hub", "takeover"]
