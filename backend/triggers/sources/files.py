"""Watch only explicitly reviewed folders, with two-second debounce."""

import asyncio
import hashlib
import logging
import time
from pathlib import Path

from ..rules import TriggerEvent

logger = logging.getLogger(__name__)


async def watch(bus, root):
    from watchfiles import Change, awatch

    folder = Path(root).resolve()
    try:
        async for changes in awatch(folder, debounce=2000, step=100, recursive=True):
            if not bus.enabled:
                return
            for change, raw in changes:
                path = Path(raw).resolve()
                if not path.is_relative_to(folder):
                    continue
                kind = {Change.added: "created", Change.modified: "modified", Change.deleted: "deleted"}.get(
                    change
                )
                if not kind:
                    continue
                try:
                    details = path.stat() if kind != "deleted" else None
                    stamp = details.st_mtime_ns if details else int(time.time() * 1e9)
                    data = {"path": str(path), "name": path.name, "size": details.st_size if details else 0}
                    key = hashlib.sha256(f"{path}:{kind}:{stamp}".encode()).hexdigest()
                    await bus.emit(TriggerEvent("files", kind, data, bus.clock(), key, True))
                except (OSError, ValueError):
                    logger.debug("Skipped an invalid file event", exc_info=True)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Folder watcher stopped: %s", folder)
