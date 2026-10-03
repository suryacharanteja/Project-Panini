"""V2-11 sandbox computers: OpenDots' ComputerService behaviours ported (tests/computer.test.ts) plus PANINI's
tools, host file exchange, untrusted-link routing and owner routes. HTTP is an httpx.MockTransport."""

import asyncio
import hashlib
import hmac
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from backend.hands import sandbox as sb
from backend.hands.sandbox import SandboxError, SandboxHand, SandboxService, validate
from backend.harness.store import Store

MANIFEST = Path(__file__).resolve().parents[3] / "panini-core/capabilities/sandbox/manifests/30-sandbox.json"


@pytest.fixture(autouse=True)
def _container_engine(monkeypatch):
    # These tests cover the container path; native mode has its own tests below.
    monkeypatch.setenv("PANINI_SANDBOX_ENGINE", "docker")


class World:
    def __init__(self, tmp_path, deadline=1.0, namespace="panini"):
        self.store = Store(tmp_path / "s.sqlite3")
        self.calls = []
        self.endpoint = None
        self.paused = False
        self.namespace = namespace
        self.config = SimpleNamespace(
            supervisor_url="http://127.0.0.1:4312",
            supervisor_token="supervisor-secret-0123456789",
            computer_token="master-secret-0123456789abc",
            namespace=namespace,
        )
        self.handler = lambda request: httpx.Response(200, json={"text": "result"})
        self.service = SandboxService(
            self.store,
            config=self.config,
            transport=httpx.MockTransport(self._route),
            deadline_s=deadline,
            paused=lambda: self.paused,
        )
        for computer in ("panini", "other"):
            self.service.ensure_row(computer)
            self.service.patch_permissions(
                computer, {"enabled": True, "browser": True, "files": True, "shell": True}
            )

    async def _route(self, request: httpx.Request):
        url = str(request.url)
        self.calls.append(request)
        container = lambda c: f"{self.namespace}-computer-{c}"  # noqa: E731
        if url.endswith("/computers"):
            return httpx.Response(
                200,
                json={
                    "computers": [
                        {
                            "botId": c,
                            "container": container(c),
                            "status": "running",
                            **({"url": self.endpoint} if self.endpoint else {}),
                        }
                        for c in ("panini", "other")
                    ]
                },
            )
        if url.endswith("/ensure"):
            return httpx.Response(
                200,
                json={
                    "botId": "panini",
                    "container": container("panini"),
                    "status": "running",
                    "url": f"http://{container('panini')}:4100",
                },
            )
        if url.endswith("/control"):
            return httpx.Response(
                200,
                json={
                    "holder": "human",
                    "requested": False,
                    "transitioning": False,
                    "resumeSnapshotRequired": False,
                    "request": {"id": "request", "status": "taken"},
                },
            )
        result = self.handler(request)
        return await result if asyncio.iscoroutine(result) else result

    def run(self, coroutine):
        return asyncio.run(coroutine)


@pytest.fixture
def world(tmp_path):
    w = World(tmp_path)
    yield w
    w.store.close()


def test_permissions_default_off_and_persist_across_restart(tmp_path):
    path = tmp_path / "p.sqlite3"
    store = Store(path)
    service = SandboxService(store, config=SimpleNamespace())
    service.ensure_row("panini")
    assert service.permissions("panini") == {
        "enabled": False,
        "browser": False,
        "files": False,
        "shell": False,
    }
    service.patch_permissions("panini", {"enabled": True, "files": True})
    store.close()
    store = Store(path)
    assert SandboxService(store, config=SimpleNamespace()).permissions("panini")["files"] is True
    with pytest.raises(SandboxError):
        SandboxService(store, config=SimpleNamespace()).patch_permissions("panini", {"root": True})
    store.close()


def test_status_never_provisions_and_actions_use_per_computer_credentials(world):
    world.run(world.service.status("panini"))
    assert not any(str(c.url).endswith("/ensure") for c in world.calls)
    world.handler = lambda r: httpx.Response(200, json={"text": world.config.computer_token})
    assert world.run(world.service.action("panini", "files_read", {"path": "note.txt"}, "agent")) == {
        "text": "[redacted]"
    }
    request = next(c for c in world.calls if str(c.url).endswith("/files/read"))
    expected = hmac.new(
        world.config.computer_token.encode(), b"panini-computer:panini", hashlib.sha256
    ).hexdigest()
    assert request.headers["authorization"] == f"Bearer {expected}"
    assert request.headers["x-openbot-bot-id"] == "panini"
    world.handler = lambda r: httpx.Response(200, json={"text": "result"})
    world.run(world.service.action("other", "files_read", {"path": "note.txt"}))
    assert world.calls[-1].headers["authorization"] != request.headers["authorization"]
    assert "other" in str(world.calls[-1].url)


def test_rejects_foreign_targets_unknown_computers_traversal_and_agent_human_controls(world):
    world.endpoint = "http://attacker.test:4100"
    with pytest.raises(SandboxError, match="endpoint"):
        world.run(world.service.action("panini", "read", {}))
    assert len(world.calls) == 1
    with pytest.raises(SandboxError, match="not found"):
        world.run(world.service.action("missing", "read", {}))
    for path in ("../secret", "a/../../secret", "/etc/passwd", "a\\secret", "a\0b"):
        with pytest.raises(SandboxError):
            validate("files_read", {"path": path})
    with pytest.raises(SandboxError):
        validate("exec", {"command": "pwd", "timeoutMs": 60001})
    with pytest.raises(SandboxError, match="Unexpected"):
        validate("read", {"url": "http://elsewhere"})
    with pytest.raises(SandboxError, match="owner-only"):
        world.run(world.service.action("panini", "human_type", {"text": "secret"}, "agent"))


def test_checks_permissions_and_pause_before_any_request(world):
    world.service.patch_permissions("panini", {"shell": False})
    with pytest.raises(SandboxError, match="permission"):
        world.run(world.service.action("panini", "exec", {"command": "sensitive command"}, "agent"))
    world.paused = True
    with pytest.raises(SandboxError, match="paused"):
        world.run(world.service.action("panini", "read", {}, "agent"))
    assert world.calls == []


def test_revocation_cancels_in_flight_work_quickly_and_deadlines_are_bounded(tmp_path):
    w = World(tmp_path, deadline=5.0)

    async def hang(request):
        await asyncio.sleep(30)
        return httpx.Response(200, json={})

    w.handler = hang

    async def scenario():
        loop = asyncio.get_running_loop()
        loop.call_later(0.05, lambda: w.service.patch_permissions("panini", {"shell": False}))
        started = loop.time()
        with pytest.raises(SandboxError, match="cancelled or timed out"):
            await w.service.action("panini", "exec", {"command": "sleep 30", "timeoutMs": 60000}, "agent")
        return loop.time() - started

    assert w.run(scenario()) < 1.0  # Acceptance: revoking the shell permission cancels within 1 s.
    short = World(tmp_path / "short", deadline=0.2)
    short.handler = hang
    with pytest.raises(SandboxError, match="cancelled or timed out"):
        short.run(short.service.action("panini", "read", {}))
    w.store.close()
    short.store.close()


def test_handback_works_after_revocation_and_upstream_failures_are_sanitized(world):
    world.service.patch_permissions("panini", {"enabled": False, "browser": False})
    world.run(world.service.control("panini", "release"))
    assert any(str(c.url).endswith("/control/release") for c in world.calls)
    world.service.patch_permissions("panini", {"enabled": True, "browser": True})
    world.handler = lambda r: httpx.Response(500, json={"error": "secret upstream detail"})
    with pytest.raises(SandboxError, match="HTTP 500") as failure:
        world.run(world.service.action("panini", "read", {}))
    assert "secret upstream" not in str(failure.value)
    world.handler = lambda r: httpx.Response(200, content=b"x" * 4_000_001)
    with pytest.raises(SandboxError, match="size limit"):
        world.run(world.service.action("panini", "read", {}))


def test_conflicts_give_a_safe_recovery_instruction(world):
    world.handler = lambda r: httpx.Response(409, json={"error": world.config.computer_token})
    with pytest.raises(SandboxError) as failure:
        world.run(world.service.action("panini", "navigate", {"url": "https://example.com"}, "agent"))
    assert "sandbox_snapshot" in str(failure.value) and world.config.computer_token not in str(failure.value)


def test_uppercase_namespace_keeps_exact_container_identity(tmp_path):
    w = World(tmp_path, namespace="MyPanini")
    w.endpoint = "http://MyPanini-computer-panini:4100"
    assert w.run(w.service.action("panini", "read", {})) == {"text": "result"}
    assert str(w.calls[-1].url) == "http://mypanini-computer-panini:4100/read"
    w.store.close()


def test_exec_results_never_echo_the_command(world):
    world.handler = lambda r: httpx.Response(
        200, json={"command": "secret command", "exitCode": 0, "stdout": "ok"}
    )
    assert world.run(world.service.action("panini", "exec", {"command": "secret command"}, "agent")) == {
        "exitCode": 0,
        "stdout": "ok",
    }


# Tools ----------------------------------------------------------------------------------------------
def test_the_tools_are_bound_to_the_persona_computer_without_human_or_permission_controls(world):
    names = {t["name"] for t in json.loads(MANIFEST.read_text(encoding="utf-8"))["tools"]}
    assert not any("human" in n or "permission" in n or "control" in n for n in names)
    hand = SandboxHand(None, {"harness": SimpleNamespace(sandbox=world.service)})
    ctx = SimpleNamespace(principal=SimpleNamespace(persona=None))
    spec = lambda name: SimpleNamespace(name=name)  # noqa: E731
    result = world.run(hand.call(ctx, spec("sandbox_files_read"), {"path": "notes.txt"}))
    assert (
        result.outcome == "ok"
        and "/computers" in str(world.calls[0].url)
        and "panini" in str(world.calls[-1].url)
    )
    world.handler = lambda r: httpx.Response(200, json={"exitCode": 0, "stdout": "hi"})
    ran = world.run(hand.call(ctx, spec("sandbox_exec"), {"command": "echo hi", "timeoutMs": 5000}))
    assert ran.evidence[0]["exit_code"] == 0
    world.handler = lambda r: httpx.Response(409, json={})
    assert (
        "sandbox_snapshot"
        in world.run(hand.call(ctx, spec("sandbox_click"), {"ref": "e1", "snapshotId": 1})).content
    )


def test_files_move_between_this_pc_and_the_sandbox_safely(world, tmp_path, monkeypatch):
    hand = SandboxHand(None, {"harness": SimpleNamespace(sandbox=world.service)})
    ctx = SimpleNamespace(principal=SimpleNamespace(persona=None))
    spec = lambda name: SimpleNamespace(name=name)  # noqa: E731
    note = tmp_path / "plan.txt"
    note.write_text("hello sandbox", encoding="utf-8")
    copied = world.run(hand.call(ctx, spec("sandbox_import"), {"host_path": str(note), "dest": ""}))
    assert copied.outcome == "ok"
    sent = json.loads(world.calls[-1].content)
    assert sent == {"path": "plan.txt", "contents": "hello sandbox"}
    secret = tmp_path / "id_rsa"
    secret.write_text("KEY")
    assert (
        "secret"
        in world.run(hand.call(ctx, spec("sandbox_import"), {"host_path": str(secret), "dest": ""})).content
    )
    monkeypatch.setattr(sb.Path, "home", classmethod(lambda cls: tmp_path))
    world.handler = lambda r: httpx.Response(200, json={"contents": "result data"})
    first = world.run(hand.call(ctx, spec("sandbox_export"), {"path": "out/report.txt", "name": ""}))
    second = world.run(hand.call(ctx, spec("sandbox_export"), {"path": "out/report.txt", "name": ""}))
    assert Path(first.data["saved"]).read_text() == "result data"
    assert first.data["saved"] != second.data["saved"]  # Never overwrites.
    assert Path(first.data["saved"]).parent == tmp_path / "Downloads" / "PANINI sandbox"


def test_nothing_falls_back_to_this_pc_when_the_sandbox_is_down(tmp_path):
    store = Store(tmp_path / "d.sqlite3")
    config = SimpleNamespace(
        supervisor_url="http://127.0.0.1:4312",
        supervisor_token="s" * 24,
        computer_token="m" * 24,
        namespace="panini",
    )

    def refuse(request):
        raise httpx.ConnectError("refused")

    service = SandboxService(
        store, config=config, transport=httpx.MockTransport(refuse), paused=lambda: False
    )
    service.ensure_row("panini")
    status = asyncio.run(service.status("panini"))
    assert status["state"] == "unavailable" and "Docker Desktop" in status["error"]
    assert (
        asyncio.run(
            SandboxService(
                store,
                config=SimpleNamespace(
                    supervisor_url="", supervisor_token="", computer_token="", namespace="panini"
                ),
            ).status("panini")
        )["state"]
        == "not_configured"
    )
    store.close()


def test_untrusted_links_go_to_the_sandbox(monkeypatch):
    from backend.hands.browser_v2 import BrowserHand

    monkeypatch.setenv("PANINI_UNTRUSTED_BROWSING", "sandbox")
    monkeypatch.setenv("PANINI_SANDBOX", "true")
    ctx = SimpleNamespace(taint={"email"})
    assert "sandbox_navigate" in BrowserHand._untrusted(ctx, "managed", "browser_navigate")
    assert BrowserHand._untrusted(SimpleNamespace(taint={"web"}), "managed", "browser_navigate") is None
    monkeypatch.setenv("PANINI_UNTRUSTED_BROWSING", "isolated")
    assert BrowserHand._untrusted(ctx, "managed", "browser_navigate") is None


def test_sandbox_tools_are_offered_with_the_flag_even_in_the_background(tmp_path, monkeypatch):
    from panini_cli.agent.model import ScriptedModel

    from backend.harness.runtime import Harness
    from backend.harness.skills import Skills

    monkeypatch.setenv("PANINI_V2_REGISTRY", "true")
    h = Harness(
        Store(tmp_path / "h.sqlite3"),
        model_factory=lambda: ScriptedModel([]),
        skills=Skills(user_root=tmp_path / "s"),
    )
    assert not any(t["name"].startswith("sandbox_") for t in h.agent(delegate=None, capabilities=()).tools())
    monkeypatch.setenv("PANINI_SANDBOX", "true")
    names = {t["name"] for t in h.agent(delegate=None, capabilities=(), background=True).tools()}
    assert {"sandbox_navigate", "sandbox_exec", "sandbox_files_write"} <= names
    assert h.sandbox.permissions("panini") == {
        "enabled": False,
        "browser": False,
        "files": False,
        "shell": False,
    }
    h.store.close()


def test_owner_routes_are_receipted(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from panini_cli.agent.model import ScriptedModel

    from backend.app import create_app
    from backend.harness.runtime import Harness
    from backend.harness.skills import Skills

    monkeypatch.setenv("PANINI_V2_LEDGER", "true")
    h = Harness(
        Store(tmp_path / "h.sqlite3"),
        model_factory=lambda: ScriptedModel([]),
        skills=Skills(user_root=tmp_path / "s"),
    )
    w = World(tmp_path / "w")
    h.sandbox = SandboxService(
        h.store, config=w.config, transport=httpx.MockTransport(w._route), paused=lambda: False
    )
    h.sandbox.ensure_row("panini")
    app = create_app(harness_factory=lambda: h, console_token="t" * 32)
    headers = {"x-panini-console": "t" * 32}
    with TestClient(app, base_url="http://127.0.0.1") as client:
        listed = client.get("/api/harness/computers", headers=headers).json()
        assert listed["computers"][0]["permissions"]["enabled"] is False
        refused = client.post("/api/harness/computers/panini/start", headers=headers)
        assert refused.status_code == 422 and "permission" in refused.json()["detail"]
        client.patch(
            "/api/harness/computers/panini/permissions",
            json={"enabled": True, "files": True},
            headers=headers,
        )
        assert (
            client.post("/api/harness/computers/panini/start", headers=headers).json()["state"] == "running"
        )
        owner = client.post(
            "/api/harness/computers/panini/actions/files_list", json={"path": ""}, headers=headers
        )
        assert owner.status_code == 200
        receipts = client.get("/api/harness/receipts?tool=sandbox_start", headers=headers).json()["receipts"]
        assert {r["outcome"] for r in receipts} == {"failed", "ok"}
    w.store.close()


class _FakeNative:
    """Stands in for the AppContainer sandbox (its real behaviour is in test_native_sandbox.py)."""

    def __init__(self):
        self.files = {}

    def run(self, computer, command, *, timeout_s):
        return {
            "exitCode": 0,
            "timedOut": False,
            "stdout": f"{computer}:{command}",
            "stderr": "",
            "seconds": 0,
        }

    def files_write(self, computer, path, contents, append=False):
        self.files[path] = contents
        return {"ok": True}

    def files_read(self, computer, path):
        return {"contents": self.files[path]}

    def files_list(self, computer, path=""):
        return {"entries": [{"name": n} for n in self.files]}


class _FakeBrowser:
    async def action(self, computer, action, args):
        return {"browser": computer, "action": action}

    async def close(self, computer):
        pass


def test_native_mode_runs_shell_and_files_without_an_engine_and_keeps_permissions(tmp_path):
    store = Store(tmp_path / "s.sqlite3")
    service = SandboxService(
        store,
        config=SimpleNamespace(),
        native=_FakeNative(),
        native_browser=_FakeBrowser(),
        paused=lambda: False,
    )
    service.ensure_row("panini")
    assert service.configured
    status = asyncio.run(service.status("panini"))
    assert status["state"] == "running" and status["engine"] == "native"
    with pytest.raises(SandboxError):
        asyncio.run(
            service.action("panini", "exec", {"command": "echo hi"}, "agent")
        )  # shell is off by default
    service.patch_permissions("panini", {"enabled": True, "shell": True, "files": True, "browser": True})
    result = asyncio.run(service.action("panini", "exec", {"command": "echo hi"}, "agent"))
    assert result["exitCode"] == 0 and result["stdout"] == "panini:echo hi"
    asyncio.run(service.action("panini", "files_write", {"path": "a.txt", "contents": "x"}, "agent"))
    assert asyncio.run(service.action("panini", "files_read", {"path": "a.txt"}, "agent"))["contents"] == "x"
    navigated = asyncio.run(service.action("panini", "navigate", {"url": "https://example.com"}, "agent"))
    assert navigated == {"browser": "panini", "action": "navigate"}


def test_native_mode_is_chosen_when_no_engine_is_installed(tmp_path, monkeypatch):
    from backend.hands import native_sandbox, sandbox_runtime

    monkeypatch.setenv("PANINI_SANDBOX_ENGINE", "auto")
    monkeypatch.setattr(sandbox_runtime, "detect", lambda *a, **k: [])
    monkeypatch.setattr(native_sandbox, "available", lambda: True)
    service = SandboxService(Store(tmp_path / "s.sqlite3"), config=SimpleNamespace())
    assert isinstance(service.native, native_sandbox.NativeSandbox)
    monkeypatch.setattr(sandbox_runtime, "detect", lambda *a, **k: ["docker"])
    assert SandboxService(Store(tmp_path / "t.sqlite3"), config=SimpleNamespace()).native is None


def test_native_browser_maps_actions_and_rejects_stale_refs(tmp_path, monkeypatch):
    from backend.hands import net_guard
    from backend.hands.native_browser import NativeBrowser, NativeBrowserError

    monkeypatch.setattr(net_guard, "resolve", lambda host: ["93.184.215.14"])

    calls = []

    class Session:
        def __init__(self, preset):
            self.preset = preset

        async def call(self, tool, args):
            calls.append((tool, args))
            return {"content": [{"type": "text", "text": f"did {tool}"}]}

        async def close(self):
            calls.append(("close", {}))

    browser = NativeBrowser(lambda c: tmp_path / c, session_factory=Session)
    run = lambda action, args: asyncio.run(browser.action("panini", action, args))  # noqa: E731
    run("navigate", {"url": "https://example.com"})
    snap = run("snapshot", {})
    assert snap == {"text": "did browser_snapshot", "snapshotId": 1}
    run("click", {"ref": "e3", "snapshotId": 1})
    with pytest.raises(NativeBrowserError):
        run("click", {"ref": "e3", "snapshotId": 0})
    with pytest.raises(NativeBrowserError, match="standard web ports"):
        run("navigate", {"url": "http://127.0.0.1:8000/"})
    run("scroll", {"deltaY": 1200})
    assert [c[0] for c in calls] == [
        "browser_navigate", "browser_snapshot", "browser_click", "browser_press_key", "browser_press_key",
    ]  # fmt: skip
    assert browser.sessions["panini"].preset.key == "isolated:sandbox-panini"
    assert "--isolated" in browser.sessions["panini"].preset.flags
    asyncio.run(browser.close("panini"))
    assert calls[-1][0] == "close" and "panini" not in browser.sessions


def test_revoking_permission_ends_a_native_action_in_flight(tmp_path):
    class Slow(_FakeBrowser):
        async def action(self, computer, action, args):
            await asyncio.sleep(30)

    service = SandboxService(
        Store(tmp_path / "s.sqlite3"),
        config=SimpleNamespace(),
        native=_FakeNative(),
        native_browser=Slow(),
        paused=lambda: False,
    )
    service.ensure_row("panini")
    service.patch_permissions("panini", {"enabled": True, "browser": True})

    async def scenario():
        task = asyncio.ensure_future(
            service.action("panini", "navigate", {"url": "https://example.com"}, "agent")
        )
        await asyncio.sleep(0.1)
        service.patch_permissions("panini", {"browser": False})
        started = asyncio.get_running_loop().time()
        with pytest.raises(SandboxError):
            await task
        return asyncio.get_running_loop().time() - started

    assert asyncio.run(scenario()) < 1


def test_private_origins_block_every_port():
    from backend.hands.net_guard import PRIVATE_ORIGINS

    origins = PRIVATE_ORIGINS.split(";")
    for origin in ("http://127.0.0.1", "http://127.0.0.1:*", "https://localhost:*", "http://192.168.*:*"):
        assert origin in origins
