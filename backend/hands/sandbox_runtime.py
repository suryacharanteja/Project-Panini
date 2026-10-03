"""The sandbox runtime (V2-11, ADR-0001): PANINI owns the container engine its sandbox computers run on.

The person never starts infrastructure by hand. PANINI detects an OCI engine (Docker Desktop or Podman), starts it
when a sandbox is needed (asking once, then remembering the answer), builds the pinned images if they are missing,
starts the supervisor and checks its health. The images stay OCI, so the same pinned computer runs on either
engine today and on a hosted provider later; the agent only ever sees the sandbox tools.
"""

import asyncio
import json
import os
import shutil
import subprocess
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from ..config import ROOT

COMPOSE = ROOT / "deployment/computers/compose.yml"
IMAGES = ("panini-computer:b6932d3", "panini-supervisor:b6932d3-panini-auth")
START_TIMEOUT_S = 240
BUILD_TIMEOUT_S = 3600


class RuntimeUnavailable(RuntimeError):
    pass


def _run(args, timeout=60, env=None) -> tuple[int, str]:
    try:
        done = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    return done.returncode, (done.stdout or "") + (done.stderr or "")


def _spawn(args):
    flags = (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP) if os.name == "nt" else 0
    subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)


@dataclass
class Engine:
    """One OCI engine PANINI can drive."""

    name: str  # docker | podman
    cli: str
    desktop: str | None = None  # Docker Desktop executable, started on demand
    run: object = field(default=_run, repr=False)
    spawn: object = field(default=_spawn, repr=False)

    @property
    def socket(self) -> str:
        # The supervisor talks to the engine through its socket, mounted into that one container only.
        return "/var/run/docker.sock" if self.name == "docker" else "/run/podman/podman.sock"

    def compose(self, *args) -> list[str]:
        return [self.cli, "compose", "-f", str(COMPOSE), *args]

    def running(self) -> bool:
        code, _ = self.run([self.cli, "info", "--format", "{{.ServerVersion}}"], 20)
        return code == 0

    def start(self) -> None:
        if self.name == "docker":
            if not self.desktop:
                raise RuntimeUnavailable(
                    "Docker is installed without Docker Desktop; start the Docker engine."
                )
            self.spawn([self.desktop])
        else:
            code, out = self.run([self.cli, "machine", "start"], START_TIMEOUT_S)
            if code != 0 and "already running" not in out.lower():
                raise RuntimeUnavailable(f"Podman could not start its machine: {out.strip()[:200]}")

    def has_images(self) -> bool:
        return all(self.run([self.cli, "image", "inspect", image], 30)[0] == 0 for image in IMAGES)


DOCKER_CLI = [r"C:\Program Files\Docker\Docker\resources\bin\docker.exe"]
DOCKER_DESKTOP = [r"C:\Program Files\Docker\Docker\Docker Desktop.exe"]
PODMAN_CLI = [r"C:\Program Files\RedHat\Podman\podman.exe"]


def _first(paths):
    return next((p for p in paths if Path(p).exists()), None)


def detect(choice: str | None = None, run=_run, spawn=_spawn) -> list[Engine]:
    """Installed engines in preference order (PANINI_SANDBOX_ENGINE=auto|docker|podman)."""
    choice = (choice or os.getenv("PANINI_SANDBOX_ENGINE", "auto")).strip().lower()
    engines = []
    docker = shutil.which("docker") or _first(DOCKER_CLI)
    if docker and choice in ("auto", "docker"):
        engines.append(Engine("docker", docker, _first(DOCKER_DESKTOP), run, spawn))
    podman = shutil.which("podman") or _first(PODMAN_CLI)
    if podman and choice in ("auto", "podman"):
        engines.append(Engine("podman", podman, None, run, spawn))
    return engines


class SandboxRuntime:
    """Starts what the sandbox needs, on demand: engine → images → supervisor → healthy."""

    def __init__(self, config, *, engines=None, sleep=asyncio.sleep, clock=time.monotonic, health=None):
        self.config = config
        self._engines = engines
        self.sleep, self.clock = sleep, clock
        self._health = health
        self.setup_state = {"phase": "idle", "message": "", "ok": None, "log": []}
        self._lock = asyncio.Lock()

    @property
    def engines(self) -> list[Engine]:
        return self._engines if self._engines is not None else detect()

    def autostart(self) -> str:
        return os.getenv("PANINI_SANDBOX_AUTOSTART", "ask").strip().lower()

    def approved(self) -> bool:
        if self.autostart() == "on":
            return True
        try:
            return bool(self.config.secrets.get("autostart_approved"))
        except (AttributeError, OSError):
            return False

    def remember_approval(self):
        self.config.secrets.set("autostart_approved", True)

    def healthy(self) -> bool:
        if self._health is not None:
            return self._health()
        url = self.config.supervisor_url.rstrip("/") + "/health"
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                return bool(json.loads(response.read() or b"{}").get("docker"))
        except (OSError, ValueError):
            return False

    def env(self, engine: Engine) -> dict:
        return {
            **os.environ,
            "PANINI_SANDBOX_SUPERVISOR_TOKEN": self.config.supervisor_token,
            "PANINI_SANDBOX_COMPUTER_TOKEN": self.config.computer_token,
            "PANINI_SANDBOX_NAMESPACE": self.config.namespace,
            "ENGINE_SOCKET": engine.socket,
        }

    def _note(self, phase, message, ok=None):
        self.setup_state = {
            "phase": phase,
            "message": message,
            "ok": ok,
            "log": [*self.setup_state["log"][-30:], f"{phase}: {message}"],
        }

    async def _wait(self, check, seconds, what):
        deadline = self.clock() + seconds
        while self.clock() < deadline:
            if await asyncio.to_thread(check):
                return
            await self.sleep(2)
        raise RuntimeUnavailable(f"{what} did not become ready in {seconds} s.")

    async def ensure_engine(self, consent=None) -> Engine:
        engines = self.engines
        if not engines:
            raise RuntimeUnavailable(
                "No container engine is installed. Install Docker Desktop or Podman Desktop for PANINI's sandbox."
            )
        for engine in engines:
            if await asyncio.to_thread(engine.running):
                return engine
        if self.autostart() == "off":
            raise RuntimeUnavailable(f"{engines[0].name.title()} is not running (automatic start is off).")
        if not self.approved():
            if consent is None or not await consent(
                f"Start {engines[0].name.title()} for PANINI's sandbox computer?",
                "PANINI starts the container engine when its sandbox is needed. Approving remembers your answer.",
            ):
                raise RuntimeUnavailable(f"{engines[0].name.title()} is not running and was not started.")
            self.remember_approval()
        engine = engines[0]
        self._note("engine", f"Starting {engine.name}…")
        await asyncio.to_thread(engine.start)
        await self._wait(engine.running, START_TIMEOUT_S, engine.name.title())
        return engine

    async def ensure_supervisor(self, engine: Engine, *, build=False) -> None:
        if await asyncio.to_thread(self.healthy):
            return
        if not (self.config.supervisor_token and self.config.computer_token):
            self.config.generate()
        if not await asyncio.to_thread(engine.has_images):
            if not build:
                raise RuntimeUnavailable(
                    "The sandbox images are not built yet. Set up the sandbox in Admin → Computers."
                )
            self._note("build", "Building the pinned sandbox images (the first time takes several minutes)…")
            code, out = await asyncio.to_thread(
                engine.run,
                engine.compose("--profile", "build-computers", "build"),
                BUILD_TIMEOUT_S,
                self.env(engine),
            )
            if code != 0:
                raise RuntimeUnavailable(f"The sandbox images did not build: {out.strip()[-300:]}")
        self._note("supervisor", "Starting the sandbox supervisor…")
        code, out = await asyncio.to_thread(
            engine.run, engine.compose("up", "-d", "computer-supervisor"), 300, self.env(engine)
        )
        if code != 0:
            raise RuntimeUnavailable(f"The sandbox supervisor did not start: {out.strip()[-300:]}")
        await self._wait(self.healthy, 120, "The sandbox supervisor")

    async def ensure_ready(self, consent=None, *, build=False) -> Engine:
        """Everything the sandbox needs, started in order; safe to call often."""
        async with self._lock:
            engine = await self.ensure_engine(consent)
            await self.ensure_supervisor(engine, build=build)
            return engine

    async def setup(self) -> dict:
        """Admin's one click: tokens, engine, images, supervisor, health. The owner's click is the consent."""
        self.setup_state = {"phase": "starting", "message": "Setting up the sandbox…", "ok": None, "log": []}
        try:
            self.config.generate()
            self.remember_approval()
            engine = await self.ensure_ready(build=True)
            self._note("ready", f"The sandbox is running on {engine.name}.", ok=True)
        except RuntimeUnavailable as exc:
            self._note("failed", str(exc), ok=False)
        return self.setup_state

    def describe(self) -> dict:
        engines = self.engines
        return {
            "engines": [e.name for e in engines],
            "autostart": self.autostart(),
            "approved": self.approved(),
            "setup": self.setup_state,
        }
