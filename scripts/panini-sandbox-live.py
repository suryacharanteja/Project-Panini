"""Live checks for V2-11 sandbox computers (needs Docker Desktop and `scripts/panini-sandbox.py build` + `up`).

1. Two computers are isolated: the second cannot list or read the first's files.
2. Files persist across stop/start.
3. Revoking the shell permission during a 30 s command cancels it within 1 s.
4. The browser opens a page and reads it inside the sandbox.

Uses the computers `panini-live-a` and `panini-live-b` and leaves their volumes in place.

    .venv\\Scripts\\python.exe scripts/panini-sandbox-live.py
"""

import asyncio
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.hands.sandbox import SandboxError, SandboxService  # noqa: E402
from backend.harness.store import Store  # noqa: E402

ALL = {"enabled": True, "browser": True, "files": True, "shell": True}


async def main():
    store = Store(Path(tempfile.mkdtemp()) / "live.sqlite3")
    service = SandboxService(store, paused=lambda: False)
    if not service.configured:
        raise SystemExit("Run scripts/panini-sandbox.py setup, build and up first.")
    checks = []
    for computer in ("panini-live-a", "panini-live-b"):
        service.ensure_row(computer)
        service.patch_permissions(computer, ALL)
        state = await service.start(computer)
        assert state["state"] == "running", state
    stamp = str(time.time())
    await service.action("panini-live-a", "files_write", {"path": "secret-note.txt", "contents": stamp})
    listing = await service.action("panini-live-b", "files_list", {"path": ""})
    assert "secret-note.txt" not in str(listing), listing
    try:
        other = await service.action("panini-live-b", "files_read", {"path": "secret-note.txt"})
        assert stamp not in str(other), other
    except SandboxError:
        pass
    checks.append("isolation: computer B cannot see computer A's files")

    await service.stop("panini-live-a")
    await service.start("panini-live-a")
    kept = await service.action("panini-live-a", "files_read", {"path": "secret-note.txt"})
    assert stamp in str(kept), kept
    checks.append("persistence: files survive stop/start")

    loop = asyncio.get_running_loop()
    loop.call_later(0.5, lambda: service.patch_permissions("panini-live-a", {"shell": False}))
    started = loop.time()
    try:
        await service.action("panini-live-a", "exec", {"command": "sleep 30", "timeoutMs": 60000}, "agent")
        raise AssertionError("The command was not cancelled.")
    except SandboxError as exc:
        assert "cancelled" in str(exc), exc
    elapsed = loop.time() - started - 0.5
    assert elapsed < 1.0, elapsed
    checks.append(
        f"revocation: the 30 s command stopped {elapsed:.2f} s after the shell permission was revoked"
    )
    service.patch_permissions("panini-live-a", ALL)

    await service.action("panini-live-a", "navigate", {"url": "https://example.com"}, "agent")
    page = await service.action("panini-live-a", "read", {}, "agent")
    assert "Example Domain" in str(page), str(page)[:300]
    checks.append("browser: example.com opened and read inside the sandbox")
    store.close()
    for check in checks:
        print("PASS", check)
    print(f"{len(checks)}/4 checks passed")


if __name__ == "__main__":
    asyncio.run(main())
