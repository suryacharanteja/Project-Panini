"""PANINI's sandbox account: the native browser runs as its own standard Windows user (ADR-0001 §5).

Codex's elevated Windows sandbox runs agent work as dedicated local users. PANINI does the same for its browser: a
hidden standard account (not an administrator, not shown at sign-in) whose password is random, never shown, and kept
only in PANINI's DPAPI secret store. The browser then cannot open the person's files at all (their profile grants
only them), while Chromium keeps its own renderer sandbox inside.

Creating the account changes Windows security settings, so the owner runs it once, as administrator:

    python -m backend.hands.sandbox_user setup      # or: remove
"""

import ctypes
import secrets
import subprocess
import sys
from pathlib import Path

ACCOUNT = "panini-sandbox"
SECRET = "sandbox_account_password"


def _store():
    from ..extension_pairing import data_directory
    from ..harness.secrets_store import SecretStore

    return SecretStore(data_directory() / "sandbox-secrets.bin")


def credentials(store=None) -> tuple[str, str] | None:
    """(account, password) when the owner has set the account up; else None."""
    try:
        password = (store or _store()).get(SECRET)
    except Exception:  # noqa: BLE001 -- An unreadable store means not set up.
        return None
    return (ACCOUNT, password) if password else None


def _powershell(script: str, stdin: str = "") -> subprocess.CompletedProcess:
    return subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        input=stdin, capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW,
    )  # fmt: skip


def grant(folder: Path, rights: str = "F") -> None:
    subprocess.run(["icacls", str(folder), "/grant", f"{ACCOUNT}:(OI)(CI){rights}", "/Q"],
                   capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW, check=True)  # fmt: skip


def setup(root: Path) -> str:
    if not ctypes.windll.shell32.IsUserAnAdmin():
        return "Run this once from an administrator terminal: python -m backend.hands.sandbox_user setup"
    password = secrets.token_urlsafe(24) + "aA1!"  # Meets Windows complexity rules.
    # The password goes through stdin, never a command line.
    script = f"""
$p = [Console]::In.ReadLine() | ConvertTo-SecureString -AsPlainText -Force
if (Get-LocalUser -Name '{ACCOUNT}' -ErrorAction SilentlyContinue) {{ Set-LocalUser -Name '{ACCOUNT}' -Password $p }}
else {{ New-LocalUser -Name '{ACCOUNT}' -Password $p -AccountNeverExpires -PasswordNeverExpires `
        -UserMayNotChangePassword -Description 'PANINI sandbox browser (no admin rights)' | Out-Null }}
$users = (New-Object Security.Principal.SecurityIdentifier 'S-1-5-32-545').Translate([Security.Principal.NTAccount]).Value.Split('\\')[-1]
Add-LocalGroupMember -Group $users -Member '{ACCOUNT}' -ErrorAction SilentlyContinue
$key = 'HKLM:\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Winlogon\\SpecialAccounts\\UserList'
New-Item -Path $key -Force | Out-Null
New-ItemProperty -Path $key -Name '{ACCOUNT}' -Value 0 -PropertyType DWord -Force | Out-Null
"""
    done = _powershell(script, password + "\n")
    if done.returncode != 0:
        return f"The account could not be created: {done.stderr.strip()[:300]}"
    _store().set(SECRET, password)
    # It needs to read PANINI's browser driver; its own workspaces are granted when they are made.
    grant(root / "node_modules", "RX")
    return f"PANINI's sandbox account '{ACCOUNT}' is ready; the native browser now runs as it."


def remove() -> str:
    if not ctypes.windll.shell32.IsUserAnAdmin():
        return "Run this from an administrator terminal: python -m backend.hands.sandbox_user remove"
    _powershell(f"Remove-LocalUser -Name '{ACCOUNT}' -ErrorAction SilentlyContinue")
    _store().set(SECRET, "")
    return f"Removed '{ACCOUNT}'; the native browser falls back to the job-limited box."


def elevate(verb: str) -> str:
    """Ask Windows to rerun this as administrator (the UAC prompt is the owner's consent) and wait for it."""
    import os
    import time

    from ..extension_pairing import data_directory

    result = data_directory() / "sandbox-account-result.txt"
    result.unlink(missing_ok=True)
    params = f'-m backend.hands.sandbox_user {verb} --result "{result}"'
    started = ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, os.getcwd(), 0)
    if started <= 32:
        return "Windows did not start the administrator step (the prompt was declined or failed)."
    for _ in range(240):
        if result.exists():
            return result.read_text(encoding="utf-8")
        time.sleep(0.5)
    return "The administrator step did not finish in two minutes."


if __name__ == "__main__":
    from ..config import ROOT

    verb = sys.argv[1] if len(sys.argv) > 1 else ""
    if verb not in ("setup", "remove"):
        print(__doc__)
    elif not ctypes.windll.shell32.IsUserAnAdmin():
        print(elevate(verb))
    else:
        message = setup(ROOT) if verb == "setup" else remove()
        if "--result" in sys.argv:
            Path(sys.argv[sys.argv.index("--result") + 1]).write_text(message, encoding="utf-8")
        print(message)
