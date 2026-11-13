"""Long-term fact store — memory tier 3 (DD-118, DD-120, DD-121).

Facts are explicit and reviewable rather than implicit and embedded. The user
can read every statement Panini holds about them and edit or delete any of it —
the same transparency posture as the audit log.

**The security-critical part:** taint resets each turn (FR-512), but memory
crosses turns by definition. Without provenance, a fact extracted while reading
an injected email would re-enter a later *clean* turn as trusted background,
defeating taint tracking through its own memory system.

Two rules close that path, and both are required:

* **M-1** — a fact from a tainted turn is stored ``confirmed = 0`` and is
  unreachable by the retrieval query until the user confirms it.
* **M-2** — even after confirmation it keeps ``origin_tainted = 1``, and loading
  it taints the turn it enters.
"""

from __future__ import annotations

import asyncio
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from panini.errors import StoreCorrupted

_MIGRATIONS = Path(__file__).parent / "migrations" / "main"


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True, slots=True)
class Fact:
    id: str
    subject: str
    statement: str
    created_at: str
    updated_at: str | None
    last_used_at: str | None
    use_count: int
    source_turn_id: str | None
    origin_tainted: bool
    confirmed: bool
    edited_by_user: bool

    @property
    def taints_on_use(self) -> bool:
        """Whether loading this fact should taint the turn (DD-121 M-2).

        A fact the user has rewritten no longer carries untrusted origin: they
        read it and authored the replacement (DD-137).
        """
        return self.origin_tainted and not self.edited_by_user

    @property
    def taint_source(self) -> str:
        return f"memory:{self.id}"


class MainStore:
    """Connection and migrations for ``panini.db``."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._conn: sqlite3.Connection | None = None
        self._lock = asyncio.Lock()

    async def open(self) -> None:
        await asyncio.to_thread(self._open_sync)

    def _open_sync(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._path, isolation_level=None, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")   # DD-140 — audit gets FULL, this does not
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")

        if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            conn.close()
            raise StoreCorrupted(f"main store integrity check failed: {self._path}")

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
            self._conn.executescript(sql_file.read_text(encoding="utf-8"))
            self._conn.execute(
                "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                (version, _utcnow()),
            )

    async def close(self) -> None:
        if self._conn is not None:
            await asyncio.to_thread(self._conn.close)
            self._conn = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise StoreCorrupted("main store is not open")
        return self._conn

    @property
    def lock(self) -> asyncio.Lock:
        return self._lock


class FactStore:
    """Reads and writes long-term facts."""

    def __init__(self, store: MainStore) -> None:
        self._store = store

    async def add(
        self,
        subject: str,
        statement: str,
        *,
        source_turn_id: str | None,
        origin_tainted: bool,
    ) -> Fact:
        """Store a fact.

        A fact from a tainted turn is stored unconfirmed, so it cannot reach
        context until the user has reviewed it (M-1).
        """
        fact = Fact(
            id=uuid.uuid4().hex,
            subject=subject.strip().lower(),
            statement=statement.strip(),
            created_at=_utcnow(),
            updated_at=None,
            last_used_at=None,
            use_count=0,
            source_turn_id=source_turn_id,
            origin_tainted=origin_tainted,
            confirmed=not origin_tainted,
            edited_by_user=False,
        )
        async with self._store.lock:
            await asyncio.to_thread(self._insert, fact)
        return fact

    def _insert(self, f: Fact) -> None:
        self._store.conn.execute(
            "INSERT INTO facts (id, subject, statement, created_at, source_turn_id,"
            " origin_tainted, confirmed, edited_by_user, use_count)"
            " VALUES (?,?,?,?,?,?,?,0,0)",
            (f.id, f.subject, f.statement, f.created_at, f.source_turn_id,
             int(f.origin_tainted), int(f.confirmed)),
        )

    async def load_confirmed(self) -> list[Fact]:
        """Facts eligible for retrieval.

        The ``confirmed = 1`` filter is applied in SQL rather than in calling
        code: an unconfirmed fact is unreachable by this path by construction,
        not by remembering to check (M-1).
        """
        return await asyncio.to_thread(
            self._query, "SELECT * FROM facts WHERE confirmed = 1 ORDER BY last_used_at DESC"
        )

    async def load_unconfirmed(self) -> list[Fact]:
        """Facts awaiting review in the Memory view."""
        return await asyncio.to_thread(
            self._query, "SELECT * FROM facts WHERE confirmed = 0 ORDER BY created_at DESC"
        )

    async def load_all(self) -> list[Fact]:
        return await asyncio.to_thread(self._query, "SELECT * FROM facts ORDER BY created_at DESC")

    def _query(self, sql: str) -> list[Fact]:
        return [self._row(r) for r in self._store.conn.execute(sql).fetchall()]

    async def confirm(self, fact_id: str) -> None:
        """Approve a fact for retrieval.

        ``origin_tainted`` is deliberately not cleared: the user has approved
        its use, not rewritten its content, so it still taints on use (M-2).
        """
        async with self._store.lock:
            await asyncio.to_thread(
                self._exec,
                "UPDATE facts SET confirmed = 1, updated_at = ? WHERE id = ?",
                (_utcnow(), fact_id),
            )

    async def edit(self, fact_id: str, statement: str) -> None:
        """Replace a fact's text.

        Editing clears the untrusted origin (DD-137): the user has read the
        statement and authored the replacement, so authorship transfers.
        """
        async with self._store.lock:
            await asyncio.to_thread(
                self._exec,
                "UPDATE facts SET statement = ?, edited_by_user = 1, origin_tainted = 0,"
                " confirmed = 1, updated_at = ? WHERE id = ?",
                (statement.strip(), _utcnow(), fact_id),
            )

    async def delete(self, fact_id: str) -> None:
        async with self._store.lock:
            await asyncio.to_thread(self._exec, "DELETE FROM facts WHERE id = ?", (fact_id,))

    async def touch(self, fact_ids: list[str]) -> None:
        """Record that facts were used, for recency scoring."""
        if not fact_ids:
            return
        placeholders = ",".join("?" for _ in fact_ids)
        async with self._store.lock:
            await asyncio.to_thread(
                self._exec,
                f"UPDATE facts SET last_used_at = ?, use_count = use_count + 1"
                f" WHERE id IN ({placeholders})",
                (_utcnow(), *fact_ids),
            )

    def _exec(self, sql: str, params: tuple) -> None:
        self._store.conn.execute(sql, params)

    @staticmethod
    def _row(r: sqlite3.Row) -> Fact:
        return Fact(
            id=r["id"],
            subject=r["subject"],
            statement=r["statement"],
            created_at=r["created_at"],
            updated_at=r["updated_at"],
            last_used_at=r["last_used_at"],
            use_count=r["use_count"],
            source_turn_id=r["source_turn_id"],
            origin_tainted=bool(r["origin_tainted"]),
            confirmed=bool(r["confirmed"]),
            edited_by_user=bool(r["edited_by_user"]),
        )
