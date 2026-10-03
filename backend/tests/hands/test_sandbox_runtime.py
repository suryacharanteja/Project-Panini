"""ADR-0001 sandbox runtime: PANINI starts the container engine, builds the pinned images and brings up the
supervisor itself, asking once; the person never starts infrastructure by hand."""

import asyncio
from types import SimpleNamespace

import httpx
import pytest

from backend.hands.sandbox import SandboxError, SandboxService
from backend.hands.sandbox_runtime import Engine, RuntimeUnavailable, SandboxRuntime, detect
from backend.harness.store import Store


class FakeEngine:
    """Records the commands an Engine would run."""

    def __init__(self, running=False, images=True, build_ok=True):
        self.is_running, self.images, self.build_ok = running, images, build_ok
        self.commands, self.started = [], 0

    def run(self, args, timeout=60, env=None):
        self.commands.append((args, env))
        joined = " ".join(map(str, args))
        if "info" in args:
            return (0 if self.is_running else 1), ""
        if "image" in args and "inspect" in args:
            return (0 if self.images else 1), ""
        if "build" in args:
            if self.build_ok:
                self.images = True
                return 0, "built"
            return 1, "network error while fetching"
        if "machine" in joined:
            self.is_running = True
            return 0, "started"
        return 0, ""

    def spawn(self, args):
        self.started += 1
        self.is_running = True


def config():
    store = {}
    return SimpleNamespace(
        supervisor_url="http://127.0.0.1:4312",
        supervisor_token="s" * 24,
        computer_token="m" * 24,
        namespace="panini",
        secrets=SimpleNamespace(get=store.get, set=lambda k, v: store.__setitem__(k, v)),
        generate=lambda: None,
        store=store,
    )


def runtime(fake, name="docker", healthy=None, **kw):
    engine = Engine(
        name, f"C:/{name}.exe", "C:/Docker Desktop.exe" if name == "docker" else None, fake.run, fake.spawn
    )
    state = {"healthy": False} if healthy is None else healthy

    async def no_sleep(_):
        return None

    rt = SandboxRuntime(config(), engines=[engine], sleep=no_sleep, health=lambda: state["healthy"], **kw)
    return rt, engine, state


@pytest.fixture(autouse=True)
def ask(monkeypatch):
    monkeypatch.setenv("PANINI_SANDBOX_AUTOSTART", "ask")


def test_no_engine_installed_says_what_to_install():
    rt = SandboxRuntime(config(), engines=[])
    with pytest.raises(RuntimeUnavailable, match="Docker Desktop or Podman"):
        asyncio.run(rt.ensure_engine())


def test_a_running_engine_is_used_as_is():
    fake = FakeEngine(running=True)
    rt, engine, _ = runtime(fake)
    assert asyncio.run(rt.ensure_engine()) is engine and fake.started == 0


def test_the_engine_starts_on_demand_after_one_approval():
    fake = FakeEngine(running=False)
    rt, engine, _ = runtime(fake)
    with pytest.raises(RuntimeUnavailable, match="not started"):
        asyncio.run(rt.ensure_engine())  # Nobody to ask: nothing starts.
    asked = []

    async def decline(summary, detail):
        asked.append(summary)
        return False

    with pytest.raises(RuntimeUnavailable):
        asyncio.run(rt.ensure_engine(decline))
    assert fake.started == 0 and asked == ["Start Docker for PANINI's sandbox computer?"]

    async def approve(summary, detail):
        asked.append(summary)
        return True

    assert asyncio.run(rt.ensure_engine(approve)) is engine and fake.started == 1
    fake.is_running = False  # Docker was closed again later: no second question.
    asyncio.run(rt.ensure_engine(None))
    assert fake.started == 2 and len(asked) == 2


def test_automatic_start_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("PANINI_SANDBOX_AUTOSTART", "off")
    rt, _, _ = runtime(FakeEngine(running=False))

    async def approve(summary, detail):
        return True

    with pytest.raises(RuntimeUnavailable, match="automatic start is off"):
        asyncio.run(rt.ensure_engine(approve))


def test_podman_starts_its_machine_and_mounts_its_own_socket(monkeypatch):
    monkeypatch.setenv("PANINI_SANDBOX_AUTOSTART", "on")
    fake = FakeEngine(running=False)
    rt, engine, state = runtime(fake, name="podman")
    asyncio.run(rt.ensure_engine())
    assert any(args[1:3] == ["machine", "start"] for args, _ in fake.commands)
    assert engine.socket == "/run/podman/podman.sock" and rt.env(engine)["ENGINE_SOCKET"] == engine.socket
    assert engine.compose("up")[:2] == ["C:/podman.exe", "compose"]


def test_images_build_once_then_the_supervisor_starts_and_becomes_healthy(monkeypatch):
    monkeypatch.setenv("PANINI_SANDBOX_AUTOSTART", "on")
    fake = FakeEngine(running=True, images=False)
    rt, engine, state = runtime(fake)
    with pytest.raises(RuntimeUnavailable, match="not built yet"):
        asyncio.run(rt.ensure_ready())  # Building is a setup step, never a surprise during a task.

    original = fake.run

    def run(args, timeout=60, env=None):
        result = original(args, timeout, env)
        if "up" in args:
            state["healthy"] = True
        return result

    engine.run = run
    asyncio.run(rt.ensure_ready(build=True))
    steps = [args for args, _ in fake.commands if "compose" in args]
    assert "build" in steps[0] and "up" in steps[1]
    env = next(env for args, env in fake.commands if "up" in args)
    assert (
        env["PANINI_SANDBOX_SUPERVISOR_TOKEN"] == "s" * 24 and env["ENGINE_SOCKET"] == "/var/run/docker.sock"
    )


def test_one_click_setup_reports_its_phases():
    fake = FakeEngine(running=False, images=False)
    rt, engine, state = runtime(fake)
    original = fake.run

    def run(args, timeout=60, env=None):
        result = original(args, timeout, env)
        if "up" in args:
            state["healthy"] = True
        return result

    engine.run = run
    result = asyncio.run(rt.setup())
    assert result["ok"] is True and result["phase"] == "ready"
    assert [line.split(":")[0] for line in result["log"]] == ["engine", "build", "supervisor", "ready"]
    assert rt.approved()  # The owner's click counts as the consent from now on.

    failing = FakeEngine(running=True, images=False, build_ok=False)
    rt2, _, _ = runtime(failing)
    failed = asyncio.run(rt2.setup())
    assert failed["ok"] is False and "did not build" in failed["message"]


def test_detect_honours_the_engine_setting(monkeypatch):
    assert all(e.name == "docker" for e in detect("docker"))
    assert all(e.name == "podman" for e in detect("podman"))


def test_a_start_with_the_supervisor_down_brings_the_runtime_up_first(tmp_path):
    store = Store(tmp_path / "r.sqlite3")
    calls = {"up": False}

    async def route(request):
        if not calls["up"]:
            raise httpx.ConnectError("refused")
        url = str(request.url)
        if url.endswith("/ensure"):
            return httpx.Response(
                200,
                json={
                    "botId": "panini",
                    "container": "panini-computer-panini",
                    "status": "running",
                    "url": "http://panini-computer-panini:4100",
                },
            )
        if url.endswith("/computers"):
            return httpx.Response(
                200,
                json={
                    "computers": [
                        {
                            "botId": "panini",
                            "container": "panini-computer-panini",
                            "status": "running",
                            "url": "http://panini-computer-panini:4100",
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "holder": "bot",
                "requested": False,
                "transitioning": False,
                "resumeSnapshotRequired": False,
            },
        )

    class Runtime:
        async def ensure_ready(self, consent=None, build=False):
            assert consent is not None and await consent("Start Docker?", "")
            calls["up"] = True

        def describe(self):
            return {}

    service = SandboxService(
        store, config=config(), transport=httpx.MockTransport(route), paused=lambda: False, runtime=Runtime()
    )
    service.ensure_row("panini")
    service.patch_permissions("panini", {"enabled": True})
    assert asyncio.run(service.start("panini"))["state"] == "running"

    class Down(Runtime):
        async def ensure_ready(self, consent=None, build=False):
            raise RuntimeUnavailable("Docker is not running (automatic start is off).")

    calls["up"] = False
    service._runtime = Down()
    with pytest.raises(SandboxError, match="automatic start is off"):
        asyncio.run(service.start("panini"))
    store.close()


def test_a_rootless_podman_machine_is_switched_to_rootful_once_approved(monkeypatch):
    monkeypatch.setenv("PANINI_SANDBOX_AUTOSTART", "on")
    fake = FakeEngine(running=True)
    state = {"rootless": True}
    original = fake.run

    def run(args, timeout=60, env=None):
        if "{{.Host.Security.Rootless}}" in args:
            return 0, "true" if state["rootless"] else "false"
        if args[1:4] == ["machine", "set", "--rootful"]:
            state["rootless"] = False
        return original(args, timeout, env)

    rt, engine, _ = runtime(fake, name="podman")
    engine.run = run
    asyncio.run(rt.ensure_engine())
    assert state["rootless"] is False
    assert [a[1:3] for a, _ in fake.commands if "machine" in a] == [
        ["machine", "stop"],
        ["machine", "set"],
        ["machine", "start"],
    ]
