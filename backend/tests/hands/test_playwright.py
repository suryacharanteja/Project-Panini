"""V2-08 Playwright sidecar: network guard, session lifecycle, the managed hand's safety rules, the pinned
tool catalogue against the real Playwright MCP server, and a real headless Edge run on the demo page."""

import asyncio
import json
import os
import shutil
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.core_contracts import ToolSpec
from backend.hands import net_guard
from backend.hands.browser import ManagedBrowserHand, element_names
from backend.hands.playwright_mcp import (
    BrowserDriverError,
    PlaywrightPool,
    PlaywrightSession,
    Preset,
    SessionCrashed,
    clean_env,
    schema_digest,
)

FAKE = Path(__file__).with_name("fake_playwright.py")
ROOT = Path(__file__).resolve().parents[3]


def fake_catalogue():
    sys.path.insert(0, str(FAKE.parent))
    import fake_playwright  # noqa: PLC0415 -- the fake's own TOOLS list is the pin for these tests.

    return {t["name"]: schema_digest(t["inputSchema"]) for t in fake_playwright.TOOLS}


def fake_session(env=None, **kwargs):
    preset = Preset("managed", [], frozenset({"browser_click", "browser_type", "browser_navigate"}))
    return PlaywrightSession(
        preset,
        command=sys.executable,
        args=[str(FAKE)],
        env=env or clean_env(),
        catalogue=fake_catalogue(),
        **kwargs,
    )


# Network guard (cases from OpenDots tests/security.test.ts) ----------------------------
@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.1",
        "172.16.0.1",
        "192.168.0.1",
        "169.254.169.254",
        "0.0.0.0",
        "::1",
        "2001::1",
        "2001:100::1",
        "2002:7f00:1::",
        "fc00::1",
        "fe80::1",
        "::ffff:127.0.0.1",
        "224.0.0.1",
        "100.64.0.1",
        "not-an-ip",
    ],
)
def test_private_and_special_addresses_are_not_public(address):
    assert not net_guard.is_public_address(address)


@pytest.mark.parametrize("address", ["8.8.8.8", "1.1.1.1", "2606:4700:4700::1111"])
def test_public_addresses_are_public(address):
    assert net_guard.is_public_address(address)


def test_urls_must_be_public_http_on_standard_ports():
    public = lambda host: ["93.184.216.34"]  # noqa: E731
    assert net_guard.check_url("https://example.com/a?b=1", resolver=public)
    for url in [
        "file:///etc/passwd",
        "https://user:pass@example.com",
        "http://127.0.0.1",
        "http://localhost:3200",
        "http://printer.local",
        "https://example.com:8443/",
        "ftp://example.com",
    ]:
        with pytest.raises(net_guard.BlockedUrl):
            net_guard.check_url(url, resolver=public)
    with pytest.raises(net_guard.BlockedUrl):
        net_guard.check_url("https://rebind.test", resolver=lambda host: ["93.184.216.34", "10.0.0.5"])


# Session lifecycle -------------------------------------------------------------------
def test_the_driver_environment_carries_no_secrets(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-leak")
    monkeypatch.setenv("PANINI_CONSOLE_TOKEN", "t" * 32)
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "C:/x")
    env = clean_env()
    assert "OPENAI_API_KEY" not in env and "PANINI_CONSOLE_TOKEN" not in env
    assert "PLAYWRIGHT_BROWSERS_PATH" not in env and env["NODE_OPTIONS"] == ""

    async def scenario():
        session = fake_session(env=env)
        result = await session.call("browser_snapshot", {"probe_env": True})
        await session.close()
        return json.loads(result["content"][0]["text"])

    keys = asyncio.run(scenario())
    assert "OPENAI_API_KEY" not in keys and "PANINI_CONSOLE_TOKEN" not in keys


def test_a_changed_tool_catalogue_refuses_to_start(monkeypatch):
    monkeypatch.setenv("FAKE_EXTRA_TOOL", "1")

    async def scenario():
        session = fake_session(env=clean_env({"FAKE_EXTRA_TOOL": "1"}))
        with pytest.raises(BrowserDriverError, match="version mismatch"):
            await session.call("browser_snapshot", {})
        assert not session.healthy

    asyncio.run(scenario())


def test_a_crash_during_an_effect_is_an_unknown_effect_and_the_session_restarts():
    async def scenario():
        session = fake_session()
        await session.call("browser_snapshot", {})
        with pytest.raises(SessionCrashed):
            await session.call("browser_click", {"element": "x", "target": "crash"})
        assert not session.healthy
        again = await session.call("browser_snapshot", {})  # Restarted on the next call.
        assert "Page state" in again["content"][0]["text"] and len(session.restarts) == 1
        await session.close()

    asyncio.run(scenario())


def test_a_crash_while_observing_is_a_plain_failure_and_repeated_crashes_pause_the_driver():
    async def scenario():
        session = fake_session()
        session.preset = Preset("managed", [], frozenset())  # Nothing counts as an effect here.
        for _ in range(4):  # The first start and three restarts.
            with pytest.raises(BrowserDriverError) as raised:
                await session.call("browser_navigate", {"url": "https://crash.test/"})
            assert not isinstance(raised.value, SessionCrashed)
        with pytest.raises(BrowserDriverError, match="paused"):
            await session.call("browser_snapshot", {})

    asyncio.run(scenario())


def test_idle_sessions_close():
    async def scenario():
        now = [0.0]
        pool = PlaywrightPool(
            factory=lambda preset, **kw: fake_session(clock=lambda: now[0]), clock=lambda: now[0]
        )
        session = pool.session(Preset("managed"))
        await session.call("browser_snapshot", {})
        now[0] = 1000
        await pool.close_idle(idle_s=600)
        assert not session.healthy
        await pool.close_all()

    asyncio.run(scenario())


# The managed hand ----------------------------------------------------------------------
def _spec(name):
    return ToolSpec(
        name=name,
        capability="managed_browser",
        hand="managed",
        title=name,
        description="",
        input_schema={},
        effect="observe",
        timeout_ms=30000,
    )


@pytest.fixture
def hand(monkeypatch):
    monkeypatch.setenv("PANINI_BROWSER_DRIVER", "playwright")
    pool = PlaywrightPool(factory=lambda preset, **kw: fake_session())
    harness = SimpleNamespace(browsers=pool)
    h = ManagedBrowserHand(None, {"harness": harness})
    h.session = lambda: pool.session(Preset("managed"))
    yield h
    asyncio.run(pool.close_all())


def test_element_names_come_from_the_snapshot():
    names = element_names('- button "Buy now" [ref=e4]\n  - link "Docs" [ref=e9] [cursor=pointer]')
    assert names == {"e4": 'button "Buy now"', "e9": 'link "Docs"'}


def test_the_hand_reads_acts_and_verifies(hand):
    async def scenario():
        opened = await hand.call(None, _spec("managed_navigate"), {"url": "https://example.com/"})
        assert opened.evidence and opened.evidence[0]["kind"] == "page"  # The page state after the action.
        snap = await hand.call(None, _spec("managed_snapshot"), {})
        assert snap.outcome == "ok" and snap.evidence[0]["kind"] == "page" and "Buy now" in snap.data["page"]
        typed = await hand.call(
            None,
            _spec("managed_type"),
            {"element": "Your name textbox", "ref": "e2", "text": "Ada", "submit": True},
        )
        assert typed.outcome == "ok" and '"submit": true' in typed.content
        checked = await hand.call(None, _spec("managed_verify_text"), {"text": "Hello"})
        assert checked.evidence[0]["kind"] == "assertion"
        missing = await hand.call(None, _spec("managed_verify_text"), {"text": "missing"})
        assert missing.outcome == "failed"

    asyncio.run(scenario())


def test_the_hand_refuses_passwords_mislabelled_elements_and_non_web_urls(hand):
    async def scenario():
        await hand.call(None, _spec("managed_snapshot"), {})
        password = await hand.call(
            None, _spec("managed_type"), {"element": "Password", "ref": "e3", "text": "x", "submit": False}
        )
        assert password.outcome == "blocked" and "never types passwords" in password.content
        sneaky = await hand.call(
            None, _spec("managed_type"), {"element": "comment box", "ref": "e3", "text": "x", "submit": False}
        )
        assert sneaky.outcome == "blocked"
        disguised = await hand.call(None, _spec("managed_click"), {"element": "Learn more link", "ref": "e4"})
        assert disguised.outcome == "blocked" and "Buy now" in disguised.content
        honest = await hand.call(None, _spec("managed_click"), {"element": "Buy now button", "ref": "e4"})
        assert honest.outcome == "ok"
        local = await hand.call(None, _spec("managed_navigate"), {"url": "file:///C:/Windows/win.ini"})
        assert local.outcome == "blocked"

    asyncio.run(scenario())


def test_a_crash_mid_click_never_reports_success(hand):
    async def scenario():
        result = await hand.call(None, _spec("managed_click"), {"element": "crash", "ref": "crash"})
        assert result.outcome == "unknown_effect" and result.dispatched

    asyncio.run(scenario())


def test_the_hand_is_off_without_the_playwright_driver(monkeypatch):
    monkeypatch.setenv("PANINI_BROWSER_DRIVER", "legacy")
    h = ManagedBrowserHand(None, {})
    result = asyncio.run(h.call(None, _spec("managed_snapshot"), {}))
    assert result.outcome == "blocked"
    assert asyncio.run(h.readiness()).state == "unavailable"


def test_browser_tools_are_offered_only_with_the_playwright_driver(monkeypatch, tmp_path):
    from panini_cli.agent.model import ScriptedModel

    from backend.harness.runtime import Harness
    from backend.harness.skills import Skills
    from backend.harness.store import Store

    monkeypatch.setenv("PANINI_V2_REGISTRY", "true")
    h = Harness(
        Store(tmp_path / "h.sqlite3"),
        model_factory=lambda: ScriptedModel([]),
        skills=Skills(user_root=tmp_path / "s"),
    )
    agent = h.agent(delegate=None, capabilities=())
    names = {t["name"] for t in agent.tools()}
    assert "browser_click" not in names and "browser" in names  # v1 tool only
    monkeypatch.setenv("PANINI_BROWSER_DRIVER", "playwright")
    names = {t["name"] for t in agent.tools()}
    assert {"browser_navigate", "browser_click", "browser_verify_text_visible", "browser_select"} <= names
    assert "browser" not in names  # V2-10: the v1 direct tool steps aside.
    assert not names & {
        "browser_start_tracing",
        "browser_close",
        "browser_cookie_list",
        "browser_run_code_unsafe",
    }
    agent.only_tools = {"browser_snapshot", "finish"}
    assert {t["name"] for t in agent.tools()} == {"browser_snapshot", "finish"}
    background = h.agent(delegate=None, capabilities=(), background=True)
    assert not any(t["name"].startswith("browser_") for t in background.tools())
    h.store.close()


# Real Playwright ----------------------------------------------------------------------
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")


@needs_node
def test_the_pinned_catalogue_matches_the_installed_playwright():
    import subprocess

    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/playwright-catalogue.py"), "--check"],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stdout + result.stderr


edge = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")


@needs_node
@pytest.mark.skipif(
    not edge.exists() or os.getenv("PANINI_SKIP_BROWSER_TESTS"), reason="needs Microsoft Edge"
)
def test_the_managed_browser_fills_and_verifies_the_demo_page(tmp_path, monkeypatch):
    """Headless Edge through the real Playwright MCP server: read, type, submit, verify."""
    from backend.hands import playwright_mcp

    monkeypatch.setenv("PANINI_BROWSER_DRIVER", "playwright")
    handler = partial(SimpleHTTPRequestHandler, directory=str(ROOT / "frontend/public"))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}/browser-demo.html"
    pool = PlaywrightPool()
    preset = playwright_mcp.managed_preset(tmp_path / "data", tmp_path / "artifacts", headless=True)
    h = ManagedBrowserHand(None, {"harness": SimpleNamespace(browsers=pool)})
    h.session = lambda: pool.session(preset)
    h.output_root = lambda: tmp_path / "artifacts" / "playwright"

    async def scenario():
        try:
            page = await h.call(None, _spec("managed_navigate"), {"url": url})
            assert page.outcome == "ok", page.content
            ref = next(r for r, name in h.names.items() if "textbox" in name)
            typed = await h.call(
                None,
                _spec("managed_type"),
                {"element": h.names[ref], "ref": ref, "text": "Ada", "submit": True},
            )
            assert typed.outcome == "ok", typed.content
            verified = await h.call(
                None, _spec("managed_verify_text"), {"text": "Hello, Ada! Browser task complete."}
            )
            assert verified.outcome == "ok", verified.content
            assert (tmp_path / "data/browser/managed").is_dir()  # The persistent profile folder.
        finally:
            await pool.close_all()
            server.shutdown()

    asyncio.run(scenario())


def test_the_separate_browser_runs_on_playwright_through_a_focused_agent(tmp_path, monkeypatch):
    """With PANINI_BROWSER_DRIVER=playwright, Browser's separate-browser target is a focused agent on the
    managed_* tools; every step goes through the registry (policy, ledger)."""
    from panini_cli.agent.model import ScriptedModel

    from backend.capabilities.browser import BrowserAdapter
    from backend.harness.runtime import Harness
    from backend.harness.skills import Skills
    from backend.harness.store import Store

    monkeypatch.setenv("PANINI_V2_REGISTRY", "true")
    monkeypatch.setenv("PANINI_BROWSER_DRIVER", "playwright")
    steps = [
        {"calls": [{"name": "browser_navigate", "arguments": {"url": "https://example.com/"}}]},
        {"calls": [{"name": "browser_verify_text_visible", "arguments": {"text": "Jev browser demo"}}]},
        {
            "calls": [
                {"name": "finish", "arguments": {"outcome": "verified_complete", "message": "Opened it."}}
            ]
        },
    ]
    model = ScriptedModel(steps)
    h = Harness(
        Store(tmp_path / "h.sqlite3"), model_factory=lambda: model, skills=Skills(user_root=tmp_path / "s")
    )
    h.browsers = PlaywrightPool(factory=lambda preset, **kw: fake_session())
    adapter = BrowserAdapter(SimpleNamespace(state=SimpleNamespace(harness=h)))
    recorded = []

    class Context:
        task = SimpleNamespace(task_id="t-managed", capability="browser")

        def emit(self, phase, message, **detail):
            pass

        def check(self):
            pass

        async def confirm(self, *args):
            return None

        def record_evidence(self, kind, summary, details):
            recorded.append(kind)
            return f"ev-{kind}"

    async def scenario():
        try:
            return await adapter.run(Context(), "open example.com", {"mode": "chromium", "target_id": ""})
        finally:
            await h.browsers.close_all()

    outcome = asyncio.run(scenario())
    assert outcome.outcome == "verified_complete" and recorded == ["browser_managed"]
    assert len(model.requests) == 3  # navigate, verify, finish: the focused agent used only managed tools.
    h.store.close()


@needs_node
@pytest.mark.skipif(
    not edge.exists() or os.getenv("PANINI_SKIP_BROWSER_TESTS"), reason="needs Microsoft Edge"
)
def test_the_managed_profile_keeps_cookies_across_restarts(tmp_path, monkeypatch):
    """Acceptance: sign in once and stay signed in. A persistent cookie set in one driver process is still there
    after the driver (and browser) restart on the same PANINI profile."""
    from backend.hands import playwright_mcp

    monkeypatch.setenv("PANINI_BROWSER_DRIVER", "playwright")
    site = tmp_path / "site"
    site.mkdir()
    (site / "login.html").write_text(
        "<!doctype html><title>Login test</title><p id=s></p><script>"
        "if (!document.cookie.includes('panini_session=')) document.cookie = 'panini_session=signed-in; max-age=86400; path=/';"
        "else document.getElementById('s').textContent = 'Still signed in';"
        "</script>",
        encoding="utf-8",
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(site)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}/login.html"

    async def visit():
        pool = PlaywrightPool()
        preset = playwright_mcp.managed_preset(tmp_path / "data", tmp_path / "artifacts", headless=True)
        h = ManagedBrowserHand(None, {"harness": SimpleNamespace(browsers=pool)})
        h.session = lambda: pool.session(preset)
        h.output_root = lambda: tmp_path / "artifacts" / "playwright"
        try:
            await h.call(None, _spec("managed_navigate"), {"url": url})
            return await h.call(None, _spec("managed_verify_text"), {"text": "Still signed in"})
        finally:
            await pool.close_all()

    try:
        assert asyncio.run(visit()).outcome == "failed"  # First visit: the site signs us in.
        assert asyncio.run(visit()).outcome == "ok"  # New driver process, same profile: still signed in.
    finally:
        server.shutdown()
