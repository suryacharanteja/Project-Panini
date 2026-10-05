"""V2-10 browser vocabulary v2: the tool table and manifest, per-browser availability, the stale-ref guard, the
password probe, uploads, dialogs, header redaction, the stronger evidence rule, approval previews, and a real
managed-browser run that leaves a Playwright trace."""

import asyncio
import json
import os
import shutil
import sys
import threading
import zipfile
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.hands.browser_tools import TOOLS
from backend.hands.browser_v2 import BrowserHand
from backend.hands.playwright_mcp import PlaywrightPool, load_catalogue

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).parent))
MANIFEST = ROOT / "panini-core/capabilities/browser/manifests/22-browser.json"


# Table and manifest ------------------------------------------------------------------------
def test_every_playwright_tool_is_mapped_and_owner_tools_are_never_offered():
    assert set(load_catalogue()) <= set(TOOLS)
    manifest = {t["name"]: t for t in json.loads(MANIFEST.read_text(encoding="utf-8"))["tools"]}
    offered = {name for name, tool in TOOLS.items() if tool.offered}
    assert offered | {"browser_select", "browser_status", "browser_media"} == set(manifest)
    for name in (
        "browser_start_tracing",
        "browser_close",
        "browser_run_code_unsafe",
        "browser_cookie_set",
        "browser_route",
    ):
        assert name not in manifest
    for name, entry in manifest.items():
        if name in TOOLS:
            assert (
                entry["effect"] == TOOLS[name].effect
                and entry["input_schema"]["additionalProperties"] is False
            )
    assert TOOLS["browser_evaluate"].availability("chrome") == "never"
    # No model-chosen file paths anywhere, and snapshots are never cut short by depth.
    assert not any("filename" in t["input_schema"]["properties"] for t in manifest.values())
    assert "depth" not in manifest["browser_snapshot"]["input_schema"]["properties"]
    assert TOOLS["browser_file_upload"].availability("managed") == "tap"


# The hand with the fake driver ----------------------------------------------------------------
def fake_session():
    from test_playwright import fake_session as make

    return make()


def spec(name):
    return SimpleNamespace(name=name, timeout_ms=30000, title=name)


@pytest.fixture
def world(monkeypatch, tmp_path):
    from backend.ledger.artifacts import Artifacts

    monkeypatch.setenv("PANINI_BROWSER_DRIVER", "playwright")
    monkeypatch.setenv("PANINI_BROWSER_TRACING", "off")
    pool = PlaywrightPool(factory=lambda preset, **kw: fake_session())
    store = __import__("backend.harness.store", fromlist=["Store"]).Store(tmp_path / "a.sqlite3")
    harness = SimpleNamespace(browsers=pool, artifacts=Artifacts(store, tmp_path / "artifacts"))
    hand = BrowserHand(None, {"harness": harness})
    asked = []

    async def confirm(summary, detail):
        asked.append((summary, detail))
        return world_state["approve"]

    world_state = {"approve": True}
    ctx = SimpleNamespace(run_id="run-1", confirm=confirm, preapproved=False, agent=None)
    loop = asyncio.new_event_loop()  # One loop per test: the fake driver's pipes belong to it.
    yield SimpleNamespace(
        hand=hand, ctx=ctx, asked=asked, state=world_state, pool=pool, harness=harness, loop=loop
    )
    loop.run_until_complete(pool.close_all())
    loop.close()
    store.close()


def call(world, name, args):
    return world.loop.run_until_complete(world.hand.call(world.ctx, spec(name), args))


def test_the_active_browser_and_switching(world):
    status = call(world, "browser_status", {})
    assert status.data["browsers"]["managed"]["state"] == "ready"
    snapshot = call(world, "browser_snapshot", {})
    assert snapshot.data["browser"] == "managed"
    assert "[ref=e4]" in snapshot.data["normalized_snapshot"]
    assert "Choose" in call(world, "browser_select", {"browser": "nope"}).content
    assert call(world, "browser_select", {"browser": "isolated"}).outcome == "ok"
    blocked = call(world, "browser_navigate", {"url": "http://127.0.0.1/"})
    assert blocked.outcome == "blocked" and "Private" in blocked.content  # Isolated: public web only.


def test_refs_must_come_from_the_latest_snapshot_and_match_their_names(world):
    first = call(world, "browser_click", {"element": "Buy now", "target": "e4"})
    assert first.outcome == "blocked" and "browser_snapshot first" in first.content
    call(world, "browser_snapshot", {})
    assert "older view" in call(world, "browser_click", {"element": "Buy now", "target": "e99"}).content
    assert "not a selector" in call(world, "browser_click", {"element": "Buy now", "target": "#buy"}).content
    disguised = call(world, "browser_click", {"element": "Learn more link", "target": "e4"})
    assert disguised.outcome == "blocked" and "Buy now" in disguised.content
    honest = call(world, "browser_click", {"element": "Buy now button", "target": "e4"})
    assert honest.outcome == "ok" and honest.evidence[0]["kind"] == "page"


def test_passwords_and_one_time_codes_are_never_typed(world):
    call(world, "browser_snapshot", {})
    assert (
        "never types passwords"
        in call(world, "browser_type", {"element": "Password", "target": "e3", "text": "x"}).content
    )
    # The label says nothing, but the field's own autocomplete says one-time-code: the probe refuses it.
    code = call(world, "browser_type", {"element": "Verification", "target": "e5", "text": "123456"})
    assert code.outcome == "blocked" and "never types passwords" in code.content
    form = call(
        world,
        "browser_fill_form",
        {
            "fields": [
                {"element": "Your name", "target": "e2", "name": "name", "type": "textbox", "value": "Ada"},
                {"element": "Verification", "target": "e5", "name": "code", "type": "textbox", "value": "1"},
            ]
        },
    )
    assert form.outcome == "blocked"
    assert (
        call(world, "browser_type", {"element": "Your name", "target": "e2", "text": "Ada"}).outcome == "ok"
    )


def test_uploads_need_approval_with_a_preview_and_never_take_secret_files(world, tmp_path):
    call(world, "browser_snapshot", {})
    report = tmp_path / "report.pdf"
    report.write_bytes(b"%PDF-1.4 demo")
    world.state["approve"] = False
    declined = call(world, "browser_file_upload", {"paths": [str(report)]})
    assert declined.outcome == "blocked" and "report.pdf (13 bytes)" in world.asked[-1][1]
    world.state["approve"] = True
    assert call(world, "browser_file_upload", {"paths": [str(report)]}).outcome == "ok"
    secret = tmp_path / ".env"
    secret.write_text("KEY=1")
    assert "secret" in call(world, "browser_file_upload", {"paths": [str(secret)]}).content
    assert "not a file" in call(world, "browser_file_upload", {"paths": ["C:/nope/missing.txt"]}).content


def test_your_chrome_refuses_page_javascript_and_tab_changes(world):
    world.hand.active["run-1"] = "chrome"
    assert "not allowed in your Chrome" in call(world, "browser_evaluate", {"function": "() => 1"}).content
    assert "does not open or close" in call(world, "browser_tabs", {"action": "close"}).content


def test_network_output_hides_sensitive_headers(world):
    out = call(world, "browser_network_requests", {"static": False})
    assert "secret-token" not in out.content and "authorization: [redacted]" in out.content


def test_destructive_dialogs_ask_first(world):
    world.hand.modal["managed"] = '["confirm" dialog with message "Delete this project permanently?"]'
    world.state["approve"] = False
    refused = call(world, "browser_handle_dialog", {"accept": True})
    assert refused.outcome == "blocked" and "Delete this project" in world.asked[-1][1]


def test_media_macro_reports_v1_fields(world):
    result = call(world, "browser_media", {"action": "state"})
    assert set(result.data) >= {"playing", "ad_showing", "ad_skippable_now"}
    assert result.evidence[0]["kind"] == "assertion"


def test_long_pages_are_truncated_into_an_artifact(world):
    class Big:
        async def call(self, tool, args, timeout_s=60):
            body = "\n".join(f'- link "Item {i}" [ref=e{i}]' for i in range(3000))
            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"### Page\n- Page URL: https://shop.example/\n### Snapshot\n{body}",
                    }
                ]
            }

    async def big_session(browser, ctx):
        return Big()

    world.hand.session = big_session
    result = call(world, "browser_snapshot", {})
    assert "Snapshot truncated" in result.content and result.artifact_ids
    assert world.harness.artifacts.get(result.artifact_ids[0])[1].count(b"[ref=") == 3000


# Evidence rule ---------------------------------------------------------------------------------
def test_a_submit_needs_an_assertion_not_just_the_page_afterwards(tmp_path):
    from backend.harness.store import Store
    from backend.ledger.receipts import Ledger

    ledger = Ledger(Store(tmp_path / "l.sqlite3"))

    def receipt(run, tool, effect, kind):
        rid = ledger.begin(
            run_id=run, tool=tool, capability="browser", hand="browser", args={}, effect=effect, principal={}
        )["id"]
        if kind:
            ledger.evidence(rid, kind, "x", {})
        ledger.end(rid, outcome="ok", content="")

    receipt("a", "browser_click", "submit", "page")
    assert not ledger.verified_after_last_effect("a")
    receipt("a", "browser_snapshot", "observe", "page")
    assert not ledger.verified_after_last_effect("a")  # Looking again is still only an observation.
    receipt("a", "browser_verify_text_visible", "observe", "assertion")
    assert ledger.verified_after_last_effect("a")
    receipt("b", "browser_type", "input", "page")
    assert ledger.verified_after_last_effect("b")  # Typing is confirmed by the page itself.


def test_policy_approvals_include_the_hands_preview(monkeypatch):
    from backend.core_contracts import Principal
    from backend.policy.hook import PolicyHook
    from backend.registry.context import ToolContext

    monkeypatch.setenv("PANINI_V2_POLICY", "enforce")
    asked = []

    async def confirm(summary, detail):
        asked.append(detail)
        return True

    class Hand:
        async def preview(self, ctx, spec, args):
            return 'Element: button "Buy now"\nScreenshot: artifact abc'

    registry = SimpleNamespace(hands={"browser_click": Hand()})
    ctx = ToolContext(principal=Principal(), confirm=confirm, agent=SimpleNamespace(registry=registry))
    from backend.core_contracts import ToolSpec

    click = ToolSpec(
        name="browser_click",
        capability="browser_v2",
        hand="browser",
        title="Click",
        description="",
        input_schema={},
        effect="submit",
        dynamic_effect=True,
    )
    asyncio.run(PolicyHook().before(ctx, click, {"element": "Buy now button", "target": "e4"}))
    assert asked and asked[0].startswith('Element: button "Buy now"')


# Real managed browser ---------------------------------------------------------------------------
@pytest.mark.skipif(
    shutil.which("node") is None
    or not Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe").exists()
    or bool(os.getenv("PANINI_SKIP_BROWSER_TESTS")),
    reason="needs Node.js and Microsoft Edge",
)
def test_a_managed_run_acts_verifies_and_leaves_a_trace(tmp_path, monkeypatch):
    from backend.harness.store import Store
    from backend.ledger.artifacts import Artifacts

    monkeypatch.setenv("PANINI_BROWSER_DRIVER", "playwright")
    monkeypatch.setenv("PANINI_MANAGED_HEADLESS", "1")
    monkeypatch.setenv("PANINI_DATA_DIR", str(tmp_path / "data"))
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(ROOT / "frontend/public"))
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}/browser-demo.html"
    store = Store(tmp_path / "h.sqlite3")
    artifacts = Artifacts(store, tmp_path / "artifacts")
    pool = PlaywrightPool()
    hand = BrowserHand(None, {"harness": SimpleNamespace(browsers=pool, artifacts=artifacts)})
    ctx = SimpleNamespace(run_id="trace-run", confirm=None, preapproved=False, agent=None)

    async def scenario():
        try:
            page = await hand.call(ctx, spec("browser_navigate"), {"url": url})
            assert page.outcome == "ok", page.content
            names = hand.names["managed"]
            ref = next(r for r, n in names.items() if "textbox" in n)
            typed = await hand.call(
                ctx,
                spec("browser_type"),
                {"element": names[ref], "target": ref, "text": "Ada", "submit": True},
            )
            assert typed.outcome == "ok", typed.content
            stale = await hand.call(ctx, spec("browser_click"), {"element": "x", "target": "e999"})
            assert stale.outcome == "blocked"
            verified = await hand.call(
                ctx, spec("browser_verify_text_visible"), {"text": "Hello, Ada! Browser task complete."}
            )
            assert verified.outcome == "ok" and verified.evidence[0]["kind"] == "assertion", verified.content
            return await hand.run_finished(ctx)
        finally:
            await pool.close_all()

    monkeypatch.setenv("PANINI_BROWSER_TRACING", "on")
    try:
        trace = asyncio.run(scenario())
        assert trace, "no trace artifact"
        meta, data = artifacts.get(trace)
        assert meta["kind"] == "trace"
        path = tmp_path / "trace.zip"
        path.write_bytes(data)
        assert zipfile.is_zipfile(path) and any(
            n.endswith(".trace") for n in zipfile.ZipFile(path).namelist()
        )
    finally:
        server.shutdown()
        store.close()


def test_open_trace_route_opens_only_traces(tmp_path, monkeypatch):
    import subprocess

    from fastapi.testclient import TestClient
    from panini_cli.agent.model import ScriptedModel

    from backend.app import create_app
    from backend.harness.runtime import Harness
    from backend.harness.skills import Skills
    from backend.harness.store import Store

    launched = []
    monkeypatch.setattr(subprocess, "Popen", lambda args, **kw: launched.append(args))
    h = Harness(
        Store(tmp_path / "h.sqlite3"),
        model_factory=lambda: ScriptedModel([]),
        skills=Skills(user_root=tmp_path / "s"),
    )
    trace = h.artifacts.put("trace", b"PK\x05\x06" + b"\x00" * 18, "application/zip")
    shot = h.artifacts.put("screenshot", b"\xff\xd8", "image/jpeg")
    app = create_app(harness_factory=lambda: h, console_token="t" * 32)
    headers = {"x-panini-console": "t" * 32}
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.post(f"/api/harness/artifacts/{shot}/open-trace", headers=headers).status_code == 404
        assert client.post(f"/api/harness/artifacts/{trace}/open-trace", headers=headers).json() == {
            "opened": True
        }
    viewer = [args for args in launched if "show-trace" in args]  # (The app also starts its Jev sidecar.)
    assert viewer and viewer[0][3].endswith(f"panini-trace-{trace}.zip")


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_the_trace_viewer_cli_is_available():
    import subprocess

    result = subprocess.run(
        ["node", str(ROOT / "node_modules/playwright-core/cli.js"), "show-trace", "--help"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0 and "trace" in result.stdout.lower()


def test_taking_over_the_managed_browser_pauses_the_agent_until_it_looks_again(world):
    from backend import live

    call(world, "browser_snapshot", {})
    live.takeover().take("managed", "default")
    paused = call(world, "browser_snapshot", {})
    assert paused.outcome == "blocked" and "You have control of PANINI's browser" in paused.content
    live.takeover().release("managed", "default")
    acting = call(world, "browser_click", {"element": "Buy now button", "target": "e4"})
    assert acting.outcome == "blocked" and "Look at it again" in acting.content
    assert call(world, "browser_snapshot", {}).outcome == "ok"
    assert call(world, "browser_click", {"element": "Buy now button", "target": "e4"}).outcome == "ok"
