"""Threads and surface bindings (docs/panini-enhancements/lld/V2-06-one-brain.md §5.8).

A thread is a harness session (items, summary, compaction). A binding maps one surface's conversation (an
avatar session, a phone conversation, a call, a channel thread) to a thread, durably, so a restart or another
device continues the same conversation.
"""

import os
import time

CONTINUE_WINDOW_S = 4 * 3600


class Threads:
    def __init__(self, store, sessions, clock=time.time):
        self.store, self.sessions, self.clock = store, sessions, clock

    def bind(
        self,
        surface: str,
        external_id: str,
        *,
        title: str = "",
        origin: str | None = None,
        continue_recent: bool = True,
    ) -> str:
        row = self.store.one(
            "SELECT b.thread_id FROM thread_bindings b JOIN sessions s ON s.id=b.thread_id "
            "WHERE b.surface=? AND b.external_id=?",
            (surface, external_id),
        )
        if row:
            return row["thread_id"]
        thread_id = (self._continue(surface) if continue_recent else None) or self._create(
            title, origin or surface
        )
        self.store.execute(
            "INSERT OR REPLACE INTO thread_bindings(surface, external_id, thread_id, created) VALUES (?,?,?,?)",
            (surface, external_id, thread_id, self.clock()),
        )
        return thread_id

    def _continue(self, surface):
        """The avatar resumes its latest recent thread after a restart (PANINI_AVATAR_THREAD=continue)."""
        if surface != "avatar" or os.getenv("PANINI_AVATAR_THREAD", "continue").strip().lower() == "new":
            return None
        row = self.store.one(
            "SELECT s.id FROM thread_bindings b JOIN sessions s ON s.id=b.thread_id "
            "WHERE b.surface='avatar' AND s.updated >= ? ORDER BY s.updated DESC LIMIT 1",
            (self.clock() - CONTINUE_WINDOW_S,),
        )
        return row["id"] if row else None

    def _create(self, title, origin):
        thread_id = self.sessions.create(title[:80])
        self.store.execute("UPDATE sessions SET origin=? WHERE id=?", (origin, thread_id))
        return thread_id

    def bindings(self, thread_id: str) -> list[dict]:
        return [
            dict(r)
            for r in self.store.all(
                "SELECT surface, external_id, created FROM thread_bindings WHERE thread_id=? ORDER BY created",
                (thread_id,),
            )
        ]

    def unbind(self, surface: str, external_id: str) -> bool:
        return bool(
            self.store.execute(
                "DELETE FROM thread_bindings WHERE surface=? AND external_id=?", (surface, external_id)
            ).rowcount
        )
