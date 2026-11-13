"""Artifacts: screenshots, snapshots, traces, videos and transcripts as files with an index row and
retention (V2-04 §5.6). Large blobs never go into SQLite."""

import hashlib
import os
import time
from datetime import UTC, datetime
from pathlib import Path

from .receipts import new_id

KINDS = {
    "screenshot",
    "snapshot",
    "trace",
    "video",
    "transcript",
    "recording",
    "page_export",
    "call_audio",
    "file",
    "phone-upload",  # V2-15: photos and files sent from the paired phone (untrusted content).
}
EXTENSIONS = {
    "image/webp": ".webp",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "text/plain": ".txt",
    "text/yaml": ".yml",
    "application/zip": ".zip",
    "video/webm": ".webm",
    "application/json": ".json",
    "application/pdf": ".pdf",
    "audio/ogg": ".ogg",
}


class Artifacts:
    def __init__(self, store, root: Path, clock=time.time):
        self.store, self.root, self.clock = store, Path(root), clock

    def put(self, kind: str, data: bytes, mime: str, *, run_id=None, receipt_id=None, pinned=False) -> str:
        if kind not in KINDS:
            raise ValueError(f"Unknown artifact kind {kind!r}")
        artifact_id = new_id()
        month = datetime.fromtimestamp(self.clock(), UTC).strftime("%Y-%m")
        folder = self.root / month
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{artifact_id}{EXTENSIONS.get(mime, '.bin')}"
        path.write_bytes(data)
        self.store.execute(
            "INSERT INTO artifacts(id, kind, mime, sha256, bytes, path, run_id, receipt_id, created, pinned) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                artifact_id,
                kind,
                mime,
                hashlib.sha256(data).hexdigest(),
                len(data),
                str(path),
                run_id,
                receipt_id,
                self.clock(),
                int(pinned),
            ),
        )
        return artifact_id

    def get(self, artifact_id: str) -> tuple[dict, bytes] | None:
        row = self.store.one("SELECT * FROM artifacts WHERE id=?", (artifact_id,))
        if row is None:
            return None
        path = Path(row["path"])
        try:
            path.resolve().relative_to(self.root.resolve())
        except ValueError:
            return None  # Never serve a file outside the artifacts folder.
        if not path.exists():
            return None
        return row, path.read_bytes()

    def pin(self, artifact_id: str, pinned: bool = True):
        self.store.execute("UPDATE artifacts SET pinned=? WHERE id=?", (int(pinned), artifact_id))

    def enforce_retention(self, *, max_days: float | None = None, max_bytes: int | None = None) -> int:
        """Delete unpinned artifacts older than max_days, then oldest first above max_bytes."""
        max_days = max_days if max_days is not None else float(os.getenv("PANINI_ARTIFACT_DAYS", "90"))
        max_bytes = (
            max_bytes
            if max_bytes is not None
            else int(float(os.getenv("PANINI_ARTIFACT_MAX_GB", "2")) * 2**30)
        )
        removed = 0
        cutoff = self.clock() - max_days * 86400
        for row in self.store.all("SELECT id, path FROM artifacts WHERE pinned=0 AND created < ?", (cutoff,)):
            removed += self._delete(row)
        total = self.store.one("SELECT COALESCE(SUM(bytes), 0) AS n FROM artifacts")["n"]
        if total > max_bytes:
            for row in self.store.all(
                "SELECT id, path, bytes FROM artifacts WHERE pinned=0 ORDER BY created"
            ):
                if total <= max_bytes:
                    break
                removed += self._delete(row)
                total -= row["bytes"]
        return removed

    def _delete(self, row) -> int:
        try:
            Path(row["path"]).unlink(missing_ok=True)
        except OSError:
            return 0
        self.store.execute("DELETE FROM artifacts WHERE id=?", (row["id"],))
        return 1
