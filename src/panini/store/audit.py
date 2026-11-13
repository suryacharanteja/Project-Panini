"""Append-only audit store (DD-065, DD-139..143).

Separate database file from ``panini.db`` so that the filesystem tool's deny
list names one path, durability can differ, and main-store corruption cannot
damage the trail.

``synchronous=FULL`` is slower and correct: an audit entry surviving only in the
page cache is worthless after a crash. This is evidence, not cached data.

Note the API surface — there is no ``delete()`` and no general ``update()``.
The triggers are the enforcement; the missing methods make the intent obvious.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from panini.errors import AuditWriteFailure, StoreCorrupted
from panini.security.redaction import redact
from panini.security.types import GateDecision, Outcome, Provenance

_MIGRATIONS = Path(__file__).parent / "migrations" / "audit"


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True, slots=True)
class AuditEntry:
    id: int
    ts_begin: str
    ts_end: str | None
    turn_id: str
    invocation_id: str
    tool_name: str
    args_redacted: str
    tainted: bool
    taint_sources: list[str]
    declared_tier: int
    effective_tier: int
    approval_required: bool
    approval_reason: str | None
    approval_granted: bool | None
    is_egress: bool
    destination: str | None
    outcome: str
    error: str | None

    @property
    def was_escalated(self) -> bool:
        return self.effective_tier > self.declared_tier


class AuditStore:
    """Writes and queries the audit trail. Cannot delete or rewrite it."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._conn: sqlite3.Connection | None = None
        self._lock = asyncio.Lock()

    # ── lifecycle ────────────────────────────────────────────────────────────

    async def open(self) -> None:
        await asyncio.to_thread(self._open_sync)

    def _open_sync(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._path, isolation_level=None, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")   # DD-140 — durability over speed
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")

        if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            conn.close()
            # DD-143 — running without a working audit trail violates FR-560.
            raise StoreCorrupted(f"audit store integrity check failed: {self._path}")

        self._conn = conn
        self._migrate()

    def _migrate(self) -> None:
        assert self._conn is not None
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_version "
            "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        row = self._conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
        current = row["v"] or 0

        for sql_file in sorted(_MIGRATIONS.glob("*.sql")):
            version = int(sql_file.name.split("_")[0])
            if version <= current:
                continue
            # sqlite3.executescript() issues an implicit COMMIT before running,
            # so it cannot be wrapped in an explicit transaction. Migrations are
            # written idempotently (IF NOT EXISTS) instead, so a crash partway
            # leaves the version unrecorded and the retry is safe.
            self._conn.executescript(sql_file.read_text(encoding="utf-8"))
            self._conn.execute(
                "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                (version, _utcnow()),
            )

    async def close(self) -> None:
        if self._conn is not None:
            await asyncio.to_thread(self._conn.close)
            self._conn = None

    # ── write path ───────────────────────────────────────────────────────────

    async def begin(
        self,
        *,
        session_id: str,
        turn_id: str,
        invocation_id: str,
        tool_name: str,
        args: dict[str, Any],
        provenance: Provenance,
        tainted: bool,
        taint_sources: list[str],
        decision: GateDecision,
    ) -> int:
        """Record a PENDING entry before execution (FR-562).

        Raises AuditWriteFailure on any error, which the gateway propagates to
        abort the invocation. An action that cannot be accounted for does not
        happen (HLD §8).
        """
        try:
            async with self._lock:
                return await asyncio.to_thread(
                    self._begin_sync,
                    session_id, turn_id, invocation_id, tool_name,
                    json.dumps(redact(args), default=str),
                    provenance.value, tainted, json.dumps(taint_sources), decision,
                )
        except Exception as exc:  # noqa: BLE001 — deliberately broad; see docstring
            raise AuditWriteFailure(
                f"could not record audit entry for {tool_name!r}: {exc}"
            ) from exc

    def _begin_sync(
        self,
        session_id: str,
        turn_id: str,
        invocation_id: str,
        tool_name: str,
        args_json: str,
        provenance: str,
        tainted: bool,
        taint_sources_json: str,
        decision: GateDecision,
    ) -> int:
        assert self._conn is not None
        cur = self._conn.execute(
            """
            INSERT INTO audit (
                ts_begin, session_id, turn_id, invocation_id, tool_name,
                args_redacted, provenance, tainted, taint_sources,
                declared_tier, effective_tier, approval_required, approval_reason,
                is_egress, destination, outcome
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'pending')
            """,
            (
                _utcnow(), session_id, turn_id, invocation_id, tool_name,
                args_json, provenance, int(tainted), taint_sources_json,
                int(decision.declared_tier), int(decision.effective_tier),
                int(decision.approval_required),
                decision.reason.value if decision.reason else None,
                int(decision.is_egress), decision.destination,
            ),
        )
        return int(cur.lastrowid or 0)

    async def complete(
        self,
        audit_id: int,
        outcome: Outcome,
        *,
        error: str | None = None,
        approval_granted: bool | None = None,
    ) -> None:
        """Resolve a pending entry. Only these four columns may change."""
        async with self._lock:
            await asyncio.to_thread(
                self._complete_sync, audit_id, outcome.value, error, approval_granted
            )

    def _complete_sync(
        self, audit_id: int, outcome: str, error: str | None, approval_granted: bool | None
    ) -> None:
        assert self._conn is not None
        self._conn.execute(
            "UPDATE audit SET ts_end=?, outcome=?, error=?, approval_granted=? WHERE id=?",
            (_utcnow(), outcome, error,
             None if approval_granted is None else int(approval_granted), audit_id),
        )

    # ── read path ────────────────────────────────────────────────────────────

    async def query(
        self, *, turn_id: str | None = None, limit: int = 100, offset: int = 0
    ) -> list[AuditEntry]:
        return await asyncio.to_thread(self._query_sync, turn_id, limit, offset)

    def _query_sync(self, turn_id: str | None, limit: int, offset: int) -> list[AuditEntry]:
        assert self._conn is not None
        if turn_id:
            rows = self._conn.execute(
                "SELECT * FROM audit WHERE turn_id=? ORDER BY id DESC LIMIT ? OFFSET ?",
                (turn_id, limit, offset),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM audit ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset)
            ).fetchall()
        return [self._row_to_entry(r) for r in rows]

    @staticmethod
    def _row_to_entry(r: sqlite3.Row) -> AuditEntry:
        return AuditEntry(
            id=r["id"],
            ts_begin=r["ts_begin"],
            ts_end=r["ts_end"],
            turn_id=r["turn_id"],
            invocation_id=r["invocation_id"],
            tool_name=r["tool_name"],
            args_redacted=r["args_redacted"],
            tainted=bool(r["tainted"]),
            taint_sources=json.loads(r["taint_sources"] or "[]"),
            declared_tier=r["declared_tier"],
            effective_tier=r["effective_tier"],
            approval_required=bool(r["approval_required"]),
            approval_reason=r["approval_reason"],
            approval_granted=None if r["approval_granted"] is None else bool(r["approval_granted"]),
            is_egress=bool(r["is_egress"]),
            destination=r["destination"],
            outcome=r["outcome"],
            error=r["error"],
        )

    async def size_bytes(self) -> int:
        return self._path.stat().st_size if self._path.exists() else 0
