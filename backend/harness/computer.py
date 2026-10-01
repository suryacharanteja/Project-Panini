"""General computer tools for the agent: list and read files, run commands, open things.

These let the agent do ordinary computer work itself (find a file, check a folder, run a
script, open an app or page) instead of failing when no narrow capability fits.

Safety:
- This PC is PANINI's workspace: files on every local drive can be listed, read and used as a
  working folder. Set PANINI_COMPUTER_ACCESS=restricted to confine it to the person's home folder
  and configured workspaces. Secret-looking files (.env, keys, credentials) are never returned.
- Commands run without a window, with a timeout and bounded output. Destructive, install
  and network commands need the person's approval (backend/capabilities/approvals.py).
- Opening uses the Windows shell for folders, files, registered apps and web URLs only.
"""

import json
import os
import re
import subprocess
from pathlib import Path

MAX_LIST = 300
MAX_READ = 30_000
MAX_OUTPUT = 12_000
SECRET = re.compile(
    r"(^|[\\/])(\.env(\..*)?|id_rsa.*|id_ed25519.*|.*\.(pem|key|pfx|p12|kdbx)|credentials(\.json)?|"
    r"\.git-credentials|\.npmrc|\.pypirc|secrets?\.(json|ya?ml|toml))$",
    re.I,
)
TEMPLATE = re.compile(r"\.env\.(example|sample|template)$", re.I)


class ComputerError(ValueError):
    pass


def drive_roots():
    if os.name != "nt":
        return [Path("/")]
    return [Path(drive) for drive in os.listdrives()]


def roots():
    if os.getenv("PANINI_COMPUTER_ACCESS", "full").strip().lower() != "restricted":
        return [root.resolve() for root in drive_roots()]
    try:
        configured = json.loads(
            os.getenv("PANINI_CLI_WORKSPACES") or os.getenv("PANINI_CLI_WORKSPACES", "[]")
        )
    except ValueError:
        configured = []
    extra = [Path(p) for p in configured if isinstance(p, str) and p.strip()]
    return [Path.home().resolve(), *[p.resolve() for p in extra if p.exists()]]


def resolve(path):
    """A path inside an allowed root. Known folder names (Downloads, Desktop, ...) work too."""
    raw = str(path or "~").strip().strip('"')
    if raw.lower() in {"downloads", "documents", "desktop", "pictures", "music", "videos"}:
        from ..capabilities.desktop import known_folder

        target = known_folder(raw)
    else:
        raw = os.path.expandvars(raw)
        target = Path.home() / raw[1:].lstrip("/\\") if raw.startswith("~") else Path(raw)
        if not target.is_absolute():
            target = Path.home() / target
    target = target.resolve()
    if not any(target == root or root in target.parents for root in roots()):
        raise ComputerError(f"{target} is outside your home folder and workspaces (restricted access mode).")
    if SECRET.search(str(target)) and not TEMPLATE.search(str(target)):
        raise ComputerError("That file looks like it holds secrets, so it is not read.")
    return target


def list_files(path="~", pattern="*"):
    folder = resolve(path)
    if not folder.is_dir():
        raise ComputerError(f"{folder} is not a folder.")
    entries = []
    for p in sorted(folder.glob(pattern or "*"), key=lambda p: p.stat().st_mtime, reverse=True)[:MAX_LIST]:
        try:
            size = "" if p.is_dir() else f"{p.stat().st_size:,} bytes"
        except OSError:
            size = ""
        entries.append(p.name + ("\\" if p.is_dir() else "") + (f"  ({size})" if size else ""))
    return {"folder": str(folder), "count": len(entries), "entries": entries}


def read_file(path):
    target = resolve(path)
    if not target.is_file():
        raise ComputerError(f"{target} is not a file.")
    data = target.read_bytes()[: MAX_READ * 4]
    if b"\0" in data[:4096]:
        return {"path": str(target), "binary": True, "size": target.stat().st_size}
    text = data.decode("utf-8", errors="replace")
    return {"path": str(target), "text": text[:MAX_READ], "truncated": len(text) > MAX_READ}


def run_command(command, timeout=60, cwd="~"):
    folder = resolve(cwd)
    shell = ["powershell", "-NoProfile", "-NonInteractive", "-Command", command] if os.name == "nt" else None
    try:
        done = subprocess.run(
            shell or command,
            shell=shell is None,
            cwd=folder,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(5, min(int(timeout or 60), 300)),
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except subprocess.TimeoutExpired:
        return {"exit_code": None, "output": f"Timed out after {timeout}s."}
    output = (done.stdout or "") + (("\n[stderr]\n" + done.stderr) if done.stderr else "")
    if len(output) > MAX_OUTPUT:
        output = output[:4000] + f"\n…[{len(output) - MAX_OUTPUT:,} characters omitted]…\n" + output[-8000:]
    return {"exit_code": done.returncode, "output": output}


def open_target(target):
    """Open a folder, file, registered app (notepad, calc, ms-settings:) or http(s) URL."""
    value = str(target or "").strip()
    if not value:
        raise ComputerError("Nothing to open.")
    if re.match(r"^https?://", value, re.I):
        pass
    elif re.match(r"^[a-z][a-z0-9+.-]*:(?![\\/])", value, re.I) and not re.match(r"^[a-z]:", value, re.I):
        if not re.match(r"^ms-settings:", value, re.I):
            raise ComputerError("Only web addresses and Windows Settings links can be opened by scheme.")
    elif re.match(r"^[\w .-]+$", value) and not Path(value).suffix:
        pass  # A registered application name such as notepad or calc.
    else:
        value = str(resolve(value))
    if os.name != "nt":
        raise ComputerError("Opening is supported on Windows only.")
    os.startfile(value)  # noqa: S606 -- Validated folder, file, registered app or URL.
    return {"opened": value}
