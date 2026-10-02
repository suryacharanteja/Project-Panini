"""PANINI v2 durable interrupts (docs/panini-enhancements/lld/V2-05-interrupts.md)."""

from .. import v2flags
from .service import APPROVAL_SCHEMA, Interrupts, InterruptSpec, decided_by, digest

_service: Interrupts | None = None


def install(service: Interrupts) -> Interrupts:
    global _service
    _service = service
    return service


def active() -> Interrupts | None:
    """The interrupt service when PANINI_V2_INTERRUPTS is on."""
    return _service if _service is not None and v2flags.enabled("PANINI_V2_INTERRUPTS") else None


class Parked(BaseException):  # noqa: N818 -- A control-flow signal like CancelledError, not an error.
    """Raised inside a background run that must wait for the person; the worker parks the task."""

    def __init__(self, interrupt_id: str, title: str):
        super().__init__(title)
        self.interrupt_id, self.title = interrupt_id, title


__all__ = [
    "APPROVAL_SCHEMA",
    "InterruptSpec",
    "Interrupts",
    "Parked",
    "active",
    "decided_by",
    "digest",
    "install",
]
