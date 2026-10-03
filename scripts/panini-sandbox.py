"""Build and run PANINI's sandbox computers (V2-11) with Docker Compose.

    .venv\\Scripts\\python.exe scripts/panini-sandbox.py setup|build|up|down|status

The supervisor and master tokens live in PANINI's DPAPI secret store; this script hands them to
`docker compose` through the environment of that one process, never through a file.
"""

import json
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
COMPOSE = ROOT / "deployment/computers/compose.yml"
DOCKER_PATHS = [r"C:\Program Files\Docker\Docker\resources\bin\docker.exe"]


def docker() -> str:
    found = shutil.which("docker") or next((p for p in DOCKER_PATHS if Path(p).exists()), None)
    if not found:
        raise SystemExit("Docker is not installed. Install Docker Desktop first.")
    return found


def environment() -> dict:
    from backend.hands.sandbox import SandboxConfig

    config = SandboxConfig()
    if not (config.supervisor_token and config.computer_token):
        raise SystemExit("Run `setup` first (or PANINI Admin → Computers → Set up the sandbox).")
    return {
        **os.environ,
        "PANINI_SANDBOX_SUPERVISOR_TOKEN": config.supervisor_token,
        "PANINI_SANDBOX_COMPUTER_TOKEN": config.computer_token,
        "PANINI_SANDBOX_NAMESPACE": config.namespace,
    }


def compose(*args, env=None) -> int:
    return subprocess.call([docker(), "compose", "-f", str(COMPOSE), *args], env=env or environment())


def main():
    command = sys.argv[1] if len(sys.argv) > 1 else "status"
    if command == "setup":
        from backend.hands.sandbox import SandboxConfig

        SandboxConfig().generate()
        print("Sandbox tokens are in PANINI's secret store.")
        return 0
    if command == "build":
        return compose("--profile", "build-computers", "build")
    if command == "up":
        return compose("up", "-d", "computer-supervisor")
    if command == "down":
        return compose("stop", "computer-supervisor")  # Stops; never removes volumes.
    if command == "status":
        code = subprocess.call(
            [docker(), "info", "--format", "{{.ServerVersion}}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if code != 0:
            print("Docker is not running. Start Docker Desktop.")
            return 1
        try:
            health = json.loads(urllib.request.urlopen("http://127.0.0.1:4312/health", timeout=5).read())
            print(f"Supervisor: healthy={bool(health.get('docker'))}")
        except OSError:
            print("Supervisor: not running (run `up`).")
        return 0
    raise SystemExit("Use setup, build, up, down or status.")


if __name__ == "__main__":
    raise SystemExit(main())
