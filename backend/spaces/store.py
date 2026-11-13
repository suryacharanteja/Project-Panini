"""Atomic page mutations. Every write checks access and revision in its transaction."""

import re
import time
from contextlib import contextmanager
from uuid import uuid4

from ..harness.memory import _pack, _unpack, embed


class SpacesError(ValueError):
    def __init__(self, code, message, status=422):
        super().__init__(message)
        self.code, self.status = code, status


class Spaces:
    def __init__(self, store, threads=None, personas=None, clock=time.time):
        self.store, self.threads, self.personas, self.clock = store, threads, personas, clock

    @contextmanager
    def transaction(self):
        with self.store.lock:
            nested = self.store.db.in_transaction
            savepoint = "spaces_" + uuid4().hex
            self.store.db.execute(f"SAVEPOINT {savepoint}" if nested else "BEGIN IMMEDIATE")
            try:
                yield
                self.store.db.execute(f"RELEASE {savepoint}" if nested else "COMMIT")
            except BaseException:
                self.store.db.execute(f"ROLLBACK TO {savepoint}" if nested else "ROLLBACK")
                if nested:
                    self.store.db.execute(f"RELEASE {savepoint}")
                raise

    def authorize(self, space_id, persona_id="panini"):
        if not self.store.one("SELECT id FROM spaces WHERE id=?", (space_id,)):
            raise SpacesError("SPACE_NOT_FOUND", "Space not found.", 404)
        if persona_id != "panini":
            if self.personas:
                self._persona(persona_id)
            if not self.store.one(
                "SELECT 1 FROM space_access WHERE space_id=? AND persona_id=?", (space_id, persona_id)
            ):
                raise SpacesError("SPACE_FORBIDDEN", "This persona cannot access this space.", 403)

    def _persona(self, persona_id):
        try:
            return self.personas.get(persona_id)
        except KeyError:
            raise SpacesError("PERSONA_UNAVAILABLE", "Persona is unavailable.", 403) from None

    def spaces(self, persona_id="panini"):
        if persona_id == "panini":
            return self.store.all("SELECT * FROM spaces ORDER BY updated DESC,id")
        if self.personas:
            self._persona(persona_id)
        return self.store.all(
            "SELECT s.* FROM spaces s JOIN space_access a ON a.space_id=s.id "
            "WHERE a.persona_id=? ORDER BY s.updated DESC,s.id",
            (persona_id,),
        )

    def create_space(self, name, description=""):
        self._text(name, 120, "Space name", nonempty=True)
        self._text(description, 2000, "Description")
        now, sid = self.clock(), uuid4().hex
        self.store.execute("INSERT INTO spaces VALUES(?,?,?,?,?)", (sid, name.strip(), description, now, now))
        return self.store.one("SELECT * FROM spaces WHERE id=?", (sid,))

    def grant(self, space_id, persona_id, allowed):
        if persona_id == "panini":
            raise SpacesError("OWNER_ACCESS", "The owner always has access.")
        with self.transaction():
            self.authorize(space_id)
            if self.personas and allowed:
                self._persona(persona_id)
            if allowed:
                self.store.execute("INSERT OR IGNORE INTO space_access VALUES(?,?)", (space_id, persona_id))
            else:
                self.store.execute(
                    "DELETE FROM space_access WHERE space_id=? AND persona_id=?", (space_id, persona_id)
                )
                if self.personas:
                    self.store.execute(
                        "UPDATE personas SET default_space_id=NULL WHERE id=? AND default_space_id=?",
                        (persona_id, space_id),
                    )
            if self.personas:
                self.personas.policy.bump()

    def read(self, page_id, persona_id="panini", *, deleted=False):
        row = self.store.one("SELECT * FROM pages WHERE id=?", (page_id,))
        if not row:
            raise SpacesError("PAGE_NOT_FOUND", "Page not found.", 404)
        self.authorize(row["space_id"], persona_id)
        if row["deleted_at"] is not None and not deleted:
            raise SpacesError("PAGE_NOT_FOUND", "Page is in the trash.", 404)
        row["url"] = f"/spaces/{row['space_id']}/pages/{row['id']}"
        return row

    def pages(self, space_id, persona_id="panini", *, deleted=False):
        self.authorize(space_id, persona_id)
        return self.store.all(
            "SELECT id,space_id,parent_id,title,revision,created,updated,deleted_at FROM pages "
            "WHERE space_id=? AND deleted_at IS "
            + ("NOT NULL" if deleted else "NULL")
            + " ORDER BY title,id",
            (space_id,),
        )

    @staticmethod
    def _text(value, limit, label, nonempty=False):
        if not isinstance(value, str) or len(value) > limit or (nonempty and not value.strip()):
            raise SpacesError(
                "INVALID_PAGE",
                f"{label} must be {'nonempty ' if nonempty else ''}text of at most {limit} characters.",
            )

    def _parent(self, space_id, parent_id, page_id=None):
        seen = {page_id} if page_id else set()
        while parent_id:
            if parent_id in seen:
                raise SpacesError("PAGE_CYCLE", "A page cannot contain itself.")
            seen.add(parent_id)
            parent = self.read(parent_id)
            if parent["space_id"] != space_id:
                raise SpacesError("INVALID_PARENT", "The parent must be in the same space.")
            parent_id = parent["parent_id"]

    def _snapshot(self, row):
        self.store.execute(
            "INSERT OR REPLACE INTO page_vectors VALUES(?,?,?)",
            (row["id"], row["revision"], _pack(embed(row["title"] + "\n" + row["content"]))),
        )
        self.store.execute(
            "INSERT INTO page_revisions VALUES(?,?,?,?,?,?,?)",
            (
                row["id"],
                row["revision"],
                row["title"],
                row["content"],
                row["parent_id"],
                row["deleted_at"],
                self.clock(),
            ),
        )
        self.store.execute(
            "DELETE FROM page_revisions WHERE page_id=? AND revision<=?", (row["id"], row["revision"] - 50)
        )

    def create(
        self, space_id, title, content="", *, parent_id=None, persona_id="panini", source_thread_id=None
    ):
        self._text(title, 240, "Title", True)
        self._text(content, 200000, "Content")
        with self.transaction():
            self.authorize(space_id, persona_id)
            self._parent(space_id, parent_id)
            pid, now = uuid4().hex, self.clock()
            self.store.execute(
                "INSERT INTO pages VALUES(?,?,?,?,?,?,?,?,?,?)",
                (pid, space_id, parent_id, title.strip(), content, 1, source_thread_id, now, now, None),
            )
            row = self.read(pid, persona_id)
            self._snapshot(row)
            return row

    def edit(
        self,
        page_id,
        expected_revision,
        *,
        persona_id="panini",
        title=None,
        content=None,
        parent_id=None,
        move=False,
        trash=False,
        restore=False,
    ):
        if sum(map(bool, (move, trash, restore))) > 1:
            raise SpacesError("INVALID_PAGE", "Move, delete and restore are separate actions.")
        with self.transaction():
            row = self.read(page_id, persona_id, deleted=restore)
            if type(expected_revision) is not int or expected_revision != row["revision"]:
                raise SpacesError(
                    "PAGE_CHANGED", f"Page changed; current revision is {row['revision']}.", 409
                )
            title = row["title"] if title is None else title
            content = row["content"] if content is None else content
            self._text(title, 240, "Title", True)
            self._text(content, 200000, "Content")
            parent_id = parent_id if move else row["parent_id"]
            self._parent(row["space_id"], parent_id, page_id)
            if trash and self.store.one(
                "SELECT id FROM pages WHERE parent_id=? AND deleted_at IS NULL", (page_id,)
            ):
                raise SpacesError("PAGE_HAS_CHILDREN", "Move or delete the subpages first.", 409)
            if restore and (row["deleted_at"] is None or self.clock() - row["deleted_at"] > 30 * 86400):
                raise SpacesError("RESTORE_EXPIRED", "This page cannot be restored.", 409)
            deleted_at = self.clock() if trash else (None if restore else row["deleted_at"])
            self.store.execute(
                "UPDATE pages SET title=?,content=?,parent_id=?,revision=revision+1,updated=?,deleted_at=? WHERE id=?",
                (title.strip(), content, parent_id, self.clock(), deleted_at, page_id),
            )
            row = self.read(page_id, persona_id, deleted=trash)
            self._snapshot(row)
            return row

    def revisions(self, page_id, persona_id="panini"):
        self.read(page_id, persona_id, deleted=True)
        return self.store.all(
            "SELECT * FROM page_revisions WHERE page_id=? ORDER BY revision DESC", (page_id,)
        )

    def restore_revision(self, page_id, expected_revision, revision):
        with self.transaction():
            self.read(page_id)
            old = self.store.one(
                "SELECT * FROM page_revisions WHERE page_id=? AND revision=?", (page_id, revision)
            )
            if not old:
                raise SpacesError("REVISION_NOT_FOUND", "This revision is no longer retained.", 404)
            # Restore text as a new revision; never silently resurrect or move a page.
            return self.edit(page_id, expected_revision, title=old["title"], content=old["content"])

    def search(self, query, persona_id="panini", space_id=None):
        self._text(query, 500, "Search")
        words = re.findall(r"\w+", query, re.UNICODE)[:30]
        if not words:
            return []
        allowed = [s["id"] for s in self.spaces(persona_id)]
        if space_id:
            self.authorize(space_id, persona_id)
            allowed = [space_id]
        if not allowed:
            return []
        match = " AND ".join('"' + word + '"' for word in words)
        placeholders = ",".join("?" for _ in allowed)
        matches = self.store.all(
            "SELECT p.id,p.space_id,p.title,p.revision,snippet(pages_fts,1,'','', ' … ',32) AS excerpt "
            "FROM pages_fts JOIN pages p ON p.rowid=pages_fts.rowid WHERE pages_fts MATCH ? "
            f"AND p.deleted_at IS NULL AND p.space_id IN ({placeholders}) ORDER BY rank,p.id LIMIT 50",
            (match, *allowed),
        )
        if matches or any(word in {"OR", "AND", "NOT", "NEAR"} for word in words):
            for row in matches:
                row["url"] = f"/spaces/{row['space_id']}/pages/{row['id']}"
            return matches
        # Typo-tolerant fallback stays local and filters space access before scoring. FTS
        # covers the full library; hashed fallback considers the latest 2,000 active pages.
        target = embed(query)
        candidates = self.store.all(
            "SELECT p.id,p.space_id,p.title,p.revision,substr(p.content,1,500) AS excerpt,v.vector "
            "FROM pages p JOIN page_vectors v ON v.page_id=p.id AND v.revision=p.revision "
            f"WHERE p.deleted_at IS NULL AND p.space_id IN ({placeholders}) ORDER BY p.updated DESC LIMIT 2000",
            tuple(allowed),
        )
        scored = []
        for candidate in candidates:
            score = sum(a * b for a, b in zip(target, _unpack(candidate.pop("vector")), strict=True))
            if score > 0.2:
                scored.append((score, candidate))
        scored.sort(key=lambda entry: (-entry[0], entry[1]["id"]))
        result = [row for _, row in scored[:50]]
        for row in result:
            row["url"] = f"/spaces/{row['space_id']}/pages/{row['id']}"
        return result

    def thread(self, page_id, persona_id="panini"):
        if not self.threads:
            raise SpacesError("THREAD_UNAVAILABLE", "Page conversations are unavailable.", 503)
        with self.transaction():
            page = self.read(page_id, persona_id)
            thread_id = self.threads.bind(
                "page", f"{page_id}:{persona_id}", title=page["title"], continue_recent=False
            )
            self.store.execute(
                "INSERT OR IGNORE INTO thread_bindings(surface,external_id,thread_id,created) VALUES('agui',?,?,?)",
                (f"page:{page_id}:{persona_id}", thread_id, self.clock()),
            )
            return thread_id
