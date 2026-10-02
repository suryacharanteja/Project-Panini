"""PANINI v2 receipts and artifacts ledger (docs/panini-enhancements/lld/V2-04-receipts-ledger.md).

`install()` sets the process-wide ledger (the harness does it); `current_receipt` names the tool call in
progress so hands deep inside a call (TaskContext.dispatch/record_evidence) attach to the right receipt.
"""

from contextvars import ContextVar

from .. import v2flags
from .artifacts import Artifacts
from .receipts import Ledger

_ledger: Ledger | None = None
_artifacts: Artifacts | None = None
current_receipt: ContextVar[str | None] = ContextVar("panini_current_receipt", default=None)


def install(ledger: Ledger, artifacts: Artifacts | None = None):
    global _ledger, _artifacts
    _ledger, _artifacts = ledger, artifacts
    return ledger


def active() -> Ledger | None:
    """The ledger when PANINI_V2_LEDGER is on."""
    return _ledger if _ledger is not None and v2flags.enabled("PANINI_V2_LEDGER") else None


def artifacts() -> Artifacts | None:
    return _artifacts if active() else None


def mark_dispatched():
    """Called by hands right before an effect leaves the process (TaskContext.dispatch)."""
    ledger, receipt = active(), current_receipt.get()
    if ledger and receipt:
        ledger.dispatched(receipt)


def mirror_evidence(run_id, capability, kind, summary, details):
    """Attach evidence to the current receipt, or to a run-level receipt outside a tool call."""
    ledger = active()
    if not ledger:
        return None
    receipt = current_receipt.get()
    if receipt is None:
        row = ledger.store.one("SELECT id FROM receipts WHERE run_id=? AND tool='run'", (run_id,))
        if row is None:
            row = ledger.begin(
                run_id=run_id,
                tool="run",
                capability=capability or "run",
                hand="run",
                args={},
                effect="observe",
                principal={"kind": "system"},
            )
            ledger.end(row["id"], outcome="ok", content="Run-level evidence.")
        receipt = row["id"]
    return ledger.evidence(receipt, kind, summary, details)


__all__ = [
    "Artifacts",
    "Ledger",
    "active",
    "artifacts",
    "current_receipt",
    "install",
    "mark_dispatched",
    "mirror_evidence",
]
