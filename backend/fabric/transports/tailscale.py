"""Tailscale transport (V2-14 §5.4): HTTPS from your tailnet to the fabric listener, and Funnel for webhooks.

PANINI drives the Tailscale CLI: it reads `tailscale status --json` for the MagicDNS name and the owner login,
maps https://<pc>.<tailnet>.ts.net to http://127.0.0.1:<port> with `tailscale serve`, and checks the result with
`tailscale serve status --json`. If the CLI is missing, logged out or the result does not match, the mode reports
needs_setup with the manual step instead of guessing.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

CLI_PATHS = [r"C:\Program Files\Tailscale\tailscale.exe"]


def _run(args, timeout=30):
    try:
        done = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                              creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)  # fmt: skip
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    return done.returncode, (done.stdout or "") + (done.stderr or "")


class Tailscale:
    def __init__(self, *, run=_run, cli=None):
        self.run = run
        self.cli = cli or shutil.which("tailscale") or next((p for p in CLI_PATHS if Path(p).exists()), None)

    def status(self) -> dict:
        if not self.cli:
            return {"state": "needs_setup", "reason": "Install Tailscale on this PC and sign in."}
        code, out = self.run([self.cli, "status", "--json"])
        try:
            data = json.loads(out) if code == 0 else {}
        except ValueError:
            data = {}
        if data.get("BackendState") != "Running":
            return {"state": "needs_setup", "reason": "Sign in to Tailscale on this PC (tailscale up)."}
        me = data.get("Self") or {}
        user = (data.get("User") or {}).get(str(me.get("UserID")), {})
        name = str(me.get("DNSName", "")).rstrip(".")
        return {
            "state": "ready",
            "origin": f"https://{name}",
            "dns": name,
            "owner_login": user.get("LoginName", ""),
        }

    def serve(self, port: int, *, funnel: bool = False) -> dict:
        found = self.status()
        if found["state"] != "ready":
            return found
        verb = "funnel" if funnel else "serve"
        code, out = self.run([self.cli, verb, "--bg", "--https=443", f"http://127.0.0.1:{port}"])
        if code != 0:
            return {"state": "needs_setup", "reason": f"tailscale {verb} failed: {out.strip()[:200]}"}
        code, out = self.run([self.cli, "serve", "status", "--json"])
        if code != 0 or f"127.0.0.1:{port}" not in out:
            return {"state": "needs_setup",
                    "reason": f"Could not confirm the Tailscale {verb} setting. Run: tailscale {verb} --bg --https=443 http://127.0.0.1:{port}"}  # fmt: skip
        return {**found, "funnel": funnel}

    def unserve(self) -> None:
        if self.cli:
            self.run([self.cli, "serve", "reset"])
