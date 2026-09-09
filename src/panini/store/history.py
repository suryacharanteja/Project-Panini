"""Conversation persistence (FR-205, FR-206).

Segments are stored individually rather than as one response blob: storing the
rendered text would lose content-type boundaries and force re-parsing on reload,
which could produce a different result than the original render.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from panini.core.turn import Modality, OutputSegment, Turn, TurnInput, TurnState
from panini.store.facts import MainStore


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True, slots=True)
class StoredTurn:
    """A turn as loaded from history.

    Deliberately not a live ``Turn``: rehydrating taint state would carry it
    across a restart, and taint is per-turn by design (FR-512). The flags are
    retained for forensics only.
    """

    id: str
    seq: int
    started_at: str
    input_text: str
    modality: str
    state: str
    was_tainted: bool
    taint_sources: list[str]
    segments: list[OutputSegment]

    def assistant_text(self) -> str:
        return "\n".join(s.content for s in self.segments if s.content_type == "text")


class HistoryStore:
    def __init__(self, store: MainStore) -> None:
        self._store = store

    async def ensure_session(self, session_id: str, persona_id: str | None = None) -> None:
        async with self._store.lock:
            await asyncio.to_thread(
                self._exec,
                "INSERT OR IGNORE INTO sessions (id, started_at, persona_id) VALUES (?,?,?)",
                (session_id, _utcnow(), persona_id),
            )

    async def append_turn(self, turn: Turn) -> None:
        await self.ensure_session(turn.session_id)
        async with self._store.lock:
            await asyncio.to_thread(self._append_sync, turn)

    def _append_sync(self, turn: Turn) -> None:
        conn = self._store.conn
        row = conn.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 AS next FROM turns WHERE session_id = ?",
            (turn.session_id,),
        ).fetchone()
        seq = row["next"]

        conn.execute(
            "INSERT OR REPLACE INTO turns (id, session_id, seq, started_at, ended_at,"
            " modality, input_text, state, tainted, taint_sources)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                turn.id, turn.session_id, seq, turn.started_at, turn.ended_at,
                str(turn.input.modality), turn.input.text, str(turn.state),
                int(turn.tainted), json.dumps(turn.taint_sources),
            ),
        )
        conn.execute("DELETE FROM segments WHERE turn_id = ?", (turn.id,))
        for i, segment in enumerate(turn.segments):
            conn.execute(
                "INSERT INTO segments (turn_id, seq, content_type, content, metadata)"
                " VALUES (?,?,?,?,?)",
                (turn.id, i, segment.content_type, segment.content,
                 json.dumps(segment.metadata)),
            )

    async def recent_turns(self, session_id: str, limit: int = 6) -> list[StoredTurn]:
        """Most recent turns, oldest first — the order they belong in context."""
        return await asyncio.to_thread(self._recent_sync, session_id, limit)

    def _recent_sync(self, session_id: str, limit: int) -> list[StoredTurn]:
        conn = self._store.conn
        rows = conn.execute(
            "SELECT * FROM turns WHERE session_id = ? AND state = 'completed'"
            " ORDER BY seq DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()

        turns: list[StoredTurn] = []
        for r in reversed(rows):
            segments = [
                OutputSegment(
                    content_type=s["content_type"],
                    content=s["content"],
                    metadata=json.loads(s["metadata"] or "{}"),
                )
                for s in conn.execute(
                    "SELECT * FROM segments WHERE turn_id = ? ORDER BY seq", (r["id"],)
                ).fetchall()
            ]
            turns.append(
                StoredTurn(
                    id=r["id"], seq=r["seq"], started_at=r["started_at"],
                    input_text=r["input_text"], modality=r["modality"], state=r["state"],
                    was_tainted=bool(r["tainted"]),
                    taint_sources=json.loads(r["taint_sources"] or "[]"),
                    segments=segments,
                )
            )
        return turns

    async def session_summary(self, session_id: str) -> str | None:
        return await asyncio.to_thread(self._summary_sync, session_id)

    def _summary_sync(self, session_id: str) -> str | None:
        row = self._store.conn.execute(
            "SELECT summary FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        return row["summary"] if row else None

    async def set_session_summary(self, session_id: str, summary: str) -> None:
        async with self._store.lock:
            await asyncio.to_thread(
                self._exec,
                "UPDATE sessions SET summary = ? WHERE id = ?",
                (summary, session_id),
            )

    async def turn_count(self, session_id: str) -> int:
        return await asyncio.to_thread(self._count_sync, session_id)

    def _count_sync(self, session_id: str) -> int:
        row = self._store.conn.execute(
            "SELECT COUNT(*) AS n FROM turns WHERE session_id = ?", (session_id,)
        ).fetchone()
        return int(row["n"])

    def _exec(self, sql: str, params: tuple[Any, ...]) -> None:
        self._store.conn.execute(sql, params)


def restore_turn(stored: StoredTurn, session_id: str) -> Turn:
    """Rebuild a Turn object from history.

    The restored turn is **clean**: taint is not rehydrated, so a fresh session
    never inherits it (FR-512). ``was_tainted`` on the stored record remains
    available for forensics.
    """
    turn = Turn(
        session_id=session_id,
        input=TurnInput(stored.input_text, Modality(stored.modality)),
        id=stored.id,
        started_at=stored.started_at,
    )
    turn.segments = list(stored.segments)
    turn.state = TurnState(stored.state)
    return turn
