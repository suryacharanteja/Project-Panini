import asyncio
import base64
import io
import json
import socket
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx2
import pytest
import uvicorn
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client
from panini_cli.agent.model import ScriptedModel
from PIL import Image

from backend.app import create_app
from backend.core_contracts import Readiness
from backend.harness.runtime import Harness
from backend.harness.store import Store
from backend.panini.contracts import Fault
from backend.panini.settings import Settings


@pytest.fixture
def anyio_backend(request):
    if request.node.name == "test_disconnected_effect_stream_stops_the_hand":
        # Windows Proactor socket.shutdown raises asynchronously on deliberate TCP disconnect.
        # Use the socket-only selector loop for this transport test; stdio retains Proactor.
        return "asyncio", {"loop_factory": asyncio.SelectorEventLoop}
    return "asyncio"


class Desktop:
    enabled = True

    def __init__(self):
        self.pipe = self
        self.password = False

    def permits(self, app):
        return app == "notepad"

    async def call(self, args):
        if args["op"] == "targets":
            return {
                "targets": [
                    {"hwnd": 1, "pid": 2, "created": "3", "label": "Test Notepad", "application": "notepad"}
                ]
            }
        if args["op"] == "snapshot":
            return {"nodes": [{"states": ["password"] if self.password else []}]}
        if args["op"] == "capture":
            stream = io.BytesIO()
            Image.new("RGB", (320, 200), "navy").save(stream, format="PNG")
            return {"image_base64": base64.b64encode(stream.getvalue()).decode()}
        return {}


@asynccontextmanager
async def running(tmp_path, monkeypatch):
    # SSE's process-wide shutdown flag otherwise retains a prior uvicorn test server.
    # These bounded test servers close their clients before their explicit shutdown.
    from sse_starlette.sse import AppStatus

    monkeypatch.setattr(AppStatus, "should_exit", False)
    monkeypatch.setattr(AppStatus, "enable_automatic_graceful_drain", False)
    for key in ("PANINI_MCP_SERVER", "PANINI_V2_LEDGER", "PANINI_V2_INTERRUPTS"):
        monkeypatch.setenv(key, "true")
    monkeypatch.setenv("PANINI_V2_POLICY", "off")
    h = Harness(Store(tmp_path / "h.sqlite3"))
    h.adapters = {"desktop": Desktop()}
    app = create_app(harness_factory=lambda: h, panini_settings=Settings(), console_token="owner" * 10)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(32)
    url = f"http://127.0.0.1:{sock.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="on"))
    work = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        async with asyncio.timeout(15):
            while not server.started:
                if work.done():
                    work.result()
                await asyncio.sleep(0.02)
        yield h, app.state.mcp, url
    finally:
        server.should_exit = True
        await asyncio.wait_for(work, 15)
        sock.close()


@asynccontextmanager
async def sdk(url, token):
    async with httpx2.AsyncClient(headers={"Authorization": "Bearer " + token}) as http:
        async with streamable_http_client(url + "/mcp", http_client=http) as (read, write):
            async with ClientSession(read, write) as client:
                await client.initialize()
                yield client


@pytest.mark.anyio
async def test_official_sdk_observation_images_receipts_and_hidden_tools(tmp_path, monkeypatch):
    async with running(tmp_path, monkeypatch) as (h, service, url):
        issued = service.clients.create("Codex", save_token=False)
        async with sdk(url, issued["token"]) as client:
            tools = (await client.list_tools()).tools
            names = {t.name for t in tools}
            assert {"panini_status", "desktop_windows", "desktop_screenshot", "receipts_search"} <= names
            assert not (
                {
                    "remember",
                    "recall",
                    "computer_run_command",
                    "use_connector",
                    "ask_agent",
                    "place_call",
                    "sandbox_run_command",
                }
                & names
            )
            assert next(t for t in tools if t.name == "desktop_screenshot").annotations.read_only_hint
            status = await client.call_tool("panini_status", {})
            assert not status.is_error
            windows = json.loads((await client.call_tool("desktop_windows", {})).content[0].text)
            key = windows["result"]["windows"][0]["window"]
            shot = await client.call_tool("desktop_screenshot", {"window": key})
            assert not shot.is_error and shot.content[1].type == "image"
            assert len(base64.b64decode(shot.content[1].data)) < 1_000_000
            assert json.loads(shot.content[0].text)["receipts"]
            receipts = json.loads((await client.call_tool("receipts_search", {})).content[0].text)
            assert len(receipts["result"]["receipts"]) >= 3
            assert not h.interrupts.list()
            denied = await client.call_tool("remember", {"text": "owner secret"})
            assert denied.is_error
            h.adapters["desktop"].password = True
            assert (await client.call_tool("desktop_screenshot", {"window": key})).is_error


@pytest.mark.anyio
async def test_sdk_scopes_and_owner_console_separation(tmp_path, monkeypatch):
    async with running(tmp_path, monkeypatch) as (h, service, url):
        issued = service.clients.create("Limited", [], save_token=False)
        async with sdk(url, issued["token"]) as client:
            assert {t.name for t in (await client.list_tools()).tools} == {
                "panini_status",
                "receipts_search",
                "panini_ask",
            }
            assert (await client.call_tool("desktop_windows", {})).is_error
        async with httpx2.AsyncClient() as client:
            response = await client.get(
                url + "/api/admin/v1/mcp/clients", headers={"Authorization": "Bearer " + issued["token"]}
            )
            assert response.status_code == 401


class BrowserFixture:
    def __init__(self):
        self.calls = []
        self.name = "Run test"
        self.slow = False
        self.stopped = asyncio.Event()
        self.screenshot = None

    async def call(self, name, args, **kwargs):
        self.calls.append((name, args))
        if name == "browser_take_screenshot" and self.screenshot:
            return {"content": [{"type": "text", "text": f"[Screenshot]({self.screenshot})"}]}
        if self.slow and name == "browser_click":
            try:
                await asyncio.Event().wait()
            finally:
                self.stopped.set()
        return {
            "content": [
                {
                    "type": "text",
                    "text": f'### Page\nPage URL: https://example.com/\n- button "{self.name}" [ref=e1]',
                }
            ]
        }


def browser_fixture(h, monkeypatch):
    monkeypatch.setenv("PANINI_BROWSER_DRIVER", "playwright")
    hand = h.registry.hands["browser_click"]
    fixture = BrowserFixture()

    async def ready():
        return {b: Readiness("ready") for b in ("chrome", "managed", "isolated")}

    async def session(browser, ctx):
        assert browser == "isolated"  # Even when Chrome is ready, an MCP default never selects it.
        return fixture

    monkeypatch.setattr(hand, "ready", ready)
    monkeypatch.setattr(hand, "session", session)
    monkeypatch.setenv("PANINI_BROWSER_TRACING", "off")
    return fixture


async def pending(h):
    async with asyncio.timeout(5):
        while not h.interrupts.list():
            await asyncio.sleep(0.01)
        return h.interrupts.list()[0]


async def owner_decide(url, interrupt, approved=True):
    async with httpx2.AsyncClient(headers={"x-panini-console": "owner" * 10}) as http:
        response = await http.post(
            url + "/api/harness/interrupts/" + interrupt["id"],
            json={
                "nonce": interrupt["nonce"],
                "action_digest": interrupt["action_digest"],
                "status": "resolved",
                "payload": {"approved": approved},
            },
        )
        assert response.status_code == 200, response.text


@pytest.mark.anyio
async def test_sdk_effect_requires_owner_even_policy_off_and_repeated_effect_is_receipted(
    tmp_path, monkeypatch
):
    async with running(tmp_path, monkeypatch) as (h, service, url):
        fixture = browser_fixture(h, monkeypatch)
        issued = service.clients.create("Codex", save_token=False)
        async with sdk(url, issued["token"]) as client:
            assert not (await client.call_tool("browser_snapshot", {})).is_error
            for _ in range(2):
                call = asyncio.create_task(
                    client.call_tool("browser_click", {"target": "e1", "element": "Run test"})
                )
                interrupt = await pending(h)
                assert "Codex wants to" in interrupt["title"]
                with pytest.raises(Fault):
                    h.interrupts.resolve(
                        interrupt["id"],
                        nonce=interrupt["nonce"],
                        action_digest=interrupt["action_digest"],
                        status="resolved",
                        payload={"approved": True},
                        decided_by={"principal_kind": "mcp_client", "surface": "mcp", "method": "local"},
                    )
                await owner_decide(url, interrupt)
                assert not (await asyncio.wait_for(call, 5)).is_error
            assert sum(n == "browser_click" for n, a in fixture.calls) == 2
            rows = h.store.all("SELECT principal,outcome FROM receipts WHERE tool='browser_click'")
            assert len(rows) == 2 and all(
                json.loads(r["principal"])["client_id"] == issued["id"] for r in rows
            )


@pytest.mark.anyio
@pytest.mark.parametrize("stop", ["revoke", "flag"])
async def test_revocation_or_flag_stops_running_hand_and_closes_receipt(tmp_path, monkeypatch, stop):
    async with running(tmp_path, monkeypatch) as (h, service, url):
        fixture = browser_fixture(h, monkeypatch)
        fixture.slow = True
        issued = service.clients.create("Codex", save_token=False)
        async with sdk(url, issued["token"]) as client:
            await client.call_tool("browser_snapshot", {})
            call = asyncio.create_task(
                client.call_tool("browser_click", {"target": "e1", "element": "Run test"})
            )
            await owner_decide(url, await pending(h))
            async with asyncio.timeout(5):
                while not any(n == "browser_click" for n, a in fixture.calls):
                    await asyncio.sleep(0.01)
            if stop == "revoke":
                await service.revoke(issued["id"])
            else:
                monkeypatch.setenv("PANINI_MCP_SERVER", "false")
            await asyncio.wait_for(fixture.stopped.wait(), 2)
            assert (await asyncio.wait_for(call, 5)).is_error
            assert not h.interrupts.list()
            assert (
                h.store.one("SELECT outcome FROM receipts WHERE tool='browser_click'")["outcome"]
                == "unknown_effect"
            )


@pytest.mark.anyio
async def test_decline_changed_target_and_pending_revoke_do_not_dispatch(tmp_path, monkeypatch):
    async with running(tmp_path, monkeypatch) as (h, service, url):
        fixture = browser_fixture(h, monkeypatch)
        issued = service.clients.create("Codex", save_token=False)
        async with sdk(url, issued["token"]) as client:
            await client.call_tool("browser_snapshot", {})
            call = asyncio.create_task(
                client.call_tool("browser_click", {"target": "e1", "element": "Run test"})
            )
            await owner_decide(url, await pending(h), False)
            assert (await call).is_error
            call = asyncio.create_task(
                client.call_tool("browser_click", {"target": "e1", "element": "Run test"})
            )
            interrupt = await pending(h)
            fixture.name = "Delete account"
            await owner_decide(url, interrupt)
            assert (await call).is_error
            fixture.name = "Run test"
            await client.call_tool("browser_snapshot", {})
            call = asyncio.create_task(
                client.call_tool("browser_click", {"target": "e1", "element": "Run test"})
            )
            await pending(h)
            await service.revoke(issued["id"])
            assert (await call).is_error
            assert not h.interrupts.list()
            assert not any(n == "browser_click" for n, a in fixture.calls)


@pytest.mark.anyio
async def test_panini_ask_no_owner_memory_skills_connectors_or_hidden_tools(tmp_path, monkeypatch):
    async with running(tmp_path, monkeypatch) as (h, service, url):
        h.memory.add("PRIVATE_OWNER_SENTINEL", kind="profile")
        model = ScriptedModel(
            [
                {"calls": [{"name": "remember", "arguments": {"text": "steal"}}]},
                {"calls": [{"name": "mcp_status", "arguments": {}}]},
                {"text": "Runtime checked."},
            ]
        )
        h.model_factory = lambda: model
        issued = service.clients.create("Codex", save_token=False)
        async with sdk(url, issued["token"]) as client:
            result = await client.call_tool("panini_ask", {"goal": "Check this runtime."})
            assert not result.is_error
            assert "PRIVATE_OWNER_SENTINEL" not in json.dumps(model.requests)
            assert "This tool is unavailable" in json.dumps(model.requests)
            assert h.store.one("SELECT COUNT(*) AS n FROM memories")["n"] == 1
            assert h.store.one("SELECT COUNT(*) AS n FROM receipts WHERE outcome='pending'")["n"] == 0


@pytest.mark.anyio
async def test_official_sdk_over_stdio_bridge(tmp_path, monkeypatch):
    import sys

    async with running(tmp_path, monkeypatch) as (h, service, url):
        issued = service.clients.create("Codex")
        script = str(Path(__file__).resolve().parents[3] / "scripts/panini-mcp.py")
        params = StdioServerParameters(
            command=sys.executable,
            args=[
                script,
                "--client",
                issued["id"],
                "--token-file",
                issued["token_file"],
                "--url",
                url + "/mcp",
            ],
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as client:
                await client.initialize()
                assert "panini_status" in {t.name for t in (await client.list_tools()).tools}
                assert not (await client.call_tool("panini_status", {})).is_error
        assert await service.revoke(issued["id"])
        async with httpx2.AsyncClient() as client:
            response = await client.post(
                url + "/mcp", headers={"Authorization": "Bearer " + issued["token"]}, json={}
            )
            assert response.status_code == 401


@pytest.mark.anyio
async def test_browser_screenshot_image_progress_and_owner_expiring_grant(tmp_path, monkeypatch):
    async with running(tmp_path, monkeypatch) as (h, service, url):
        fixture = browser_fixture(h, monkeypatch)
        path = h.registry.hands["browser_click"].managed.output_root() / "test.jpg"
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (100, 100), "navy").save(path)
        fixture.screenshot = str(path)
        issued = service.clients.create("Codex", save_token=False)
        progress = []

        async def on_progress(value, total, message):
            progress.append((value, message))

        async with sdk(url, issued["token"]) as client:
            result = await client.call_tool(
                "browser_take_screenshot", {"scale": "css"}, progress_callback=on_progress
            )
            assert not result.is_error and result.content[1].type == "image"
            assert progress and progress[0][0] >= 1
            async with httpx2.AsyncClient(headers={"x-panini-console": "owner" * 10}) as http:
                grant = await http.post(
                    url + f"/api/admin/v1/mcp/clients/{issued['id']}/grants",
                    json={"tool": "browser_navigate", "seconds": 3600},
                )
                assert grant.status_code == 201
                assert not (
                    await client.call_tool("browser_navigate", {"url": "https://example.com"})
                ).is_error
                assert not h.interrupts.list()
                assert (
                    await http.delete(
                        url + f"/api/admin/v1/mcp/clients/{issued['id']}/grants/{grant.json()['id']}"
                    )
                ).status_code == 200
            call = asyncio.create_task(client.call_tool("browser_navigate", {"url": "https://example.com"}))
            await owner_decide(url, await pending(h), False)
            assert (await call).is_error


@pytest.mark.anyio
async def test_modern_sdk_protocol_preserves_client_browser_context(tmp_path, monkeypatch):
    async with running(tmp_path, monkeypatch) as (h, service, url):
        browser_fixture(h, monkeypatch)
        issued = service.clients.create("Codex", save_token=False)
        async with httpx2.AsyncClient(headers={"Authorization": "Bearer " + issued["token"]}) as http:
            async with streamable_http_client(url + "/mcp", http_client=http) as (read, write):
                async with ClientSession(read, write) as client:
                    await client.discover()
                    assert not (await client.call_tool("browser_snapshot", {})).is_error
                    call = asyncio.create_task(
                        client.call_tool("browser_click", {"target": "e1", "element": "Run test"})
                    )
                    await owner_decide(url, await pending(h))
                    assert not (await call).is_error


@pytest.mark.anyio
async def test_transport_session_cannot_be_borrowed_by_another_client(tmp_path, monkeypatch):
    async with running(tmp_path, monkeypatch) as (h, service, url):
        first = service.clients.create("First", save_token=False)
        second = service.clients.create("Second", save_token=False)
        sessions = []

        async def capture(response):
            if response.headers.get("mcp-session-id"):
                sessions.append(response.headers["mcp-session-id"])

        async with httpx2.AsyncClient(
            headers={"Authorization": "Bearer " + first["token"]}, event_hooks={"response": [capture]}
        ) as http:
            async with streamable_http_client(url + "/mcp", http_client=http) as (read, write):
                async with ClientSession(read, write) as client:
                    await client.initialize()
                    assert sessions
                    async with httpx2.AsyncClient() as other:
                        response = await other.post(
                            url + "/mcp",
                            headers={
                                "Authorization": "Bearer " + second["token"],
                                "mcp-session-id": sessions[0],
                                "mcp-protocol-version": "2025-11-25",
                                "accept": "application/json, text/event-stream",
                            },
                            json={"jsonrpc": "2.0", "id": 4, "method": "tools/list", "params": {}},
                        )
                        assert response.status_code in {403, 404}
                    assert not (await client.call_tool("panini_status", {})).is_error


@pytest.mark.anyio
async def test_other_client_never_sees_owner_receipts_or_window_choices(tmp_path, monkeypatch):
    async with running(tmp_path, monkeypatch) as (h, service, url):
        first = service.clients.create("First", save_token=False)
        second = service.clients.create("Second", save_token=False)
        owner = h.ledger.begin(
            run_id="owner",
            tool="secret",
            capability="memory",
            hand="memory",
            args={},
            effect="observe",
            principal={"kind": "owner"},
        )
        h.ledger.end(owner["id"], outcome="ok", content="PRIVATE_OWNER_SENTINEL")
        async with sdk(url, first["token"]) as client:
            windows = json.loads((await client.call_tool("desktop_windows", {})).content[0].text)
            key = windows["result"]["windows"][0]["window"]
        async with sdk(url, second["token"]) as client:
            assert (await client.call_tool("desktop_screenshot", {"window": key})).is_error
            receipts = await client.call_tool("receipts_search", {})
            assert "PRIVATE_OWNER_SENTINEL" not in receipts.content[0].text
            assert owner["id"] not in receipts.content[0].text


@pytest.mark.anyio
async def test_disconnected_effect_stream_stops_the_hand(tmp_path, monkeypatch):
    async with running(tmp_path, monkeypatch) as (h, service, url):
        fixture = browser_fixture(h, monkeypatch)
        fixture.slow = True
        issued = service.clients.create("Codex", save_token=False)
        sessions = []

        async def capture(response):
            if response.headers.get("mcp-session-id"):
                sessions.append(response.headers["mcp-session-id"])

        async with httpx2.AsyncClient(
            headers={"Authorization": "Bearer " + issued["token"]}, event_hooks={"response": [capture]}
        ) as http:
            async with streamable_http_client(url + "/mcp", http_client=http) as (read, write):
                async with ClientSession(read, write) as client:
                    await client.initialize()
                    await client.call_tool("browser_snapshot", {})
                    async with http.stream(
                        "POST",
                        url + "/mcp",
                        headers={
                            "mcp-session-id": sessions[0],
                            "mcp-protocol-version": "2025-11-25",
                            "accept": "application/json, text/event-stream",
                        },
                        json={
                            "jsonrpc": "2.0",
                            "id": 99,
                            "method": "tools/call",
                            "params": {
                                "name": "browser_click",
                                "arguments": {"target": "e1", "element": "Run test"},
                                "_meta": {"progressToken": "disconnect-test"},
                            },
                        },
                    ) as response:
                        assert response.status_code == 200
                        await owner_decide(url, await pending(h))
                        async with asyncio.timeout(5):
                            while not any(n == "browser_click" for n, args in fixture.calls):
                                await asyncio.sleep(0.01)
                    await asyncio.wait_for(fixture.stopped.wait(), 3)
                    assert not h.interrupts.list()


@pytest.mark.anyio
async def test_idle_session_cleanup_does_not_close_active_sibling(tmp_path, monkeypatch):
    async with running(tmp_path, monkeypatch) as (h, service, url):
        browser_fixture(h, monkeypatch)
        issued = service.clients.create("Two sessions", save_token=False)
        async with sdk(url, issued["token"]) as first, sdk(url, issued["token"]) as second:
            await first.call_tool("browser_snapshot", {})
            old = next(iter(service.states.values()))
            await second.call_tool("browser_snapshot", {})
            assert len(service.states) == 2
            call = asyncio.create_task(
                second.call_tool("browser_click", {"target": "e1", "element": "Run test"})
            )
            approval = await pending(h)
            old["last_used"] = time.monotonic() - 901
            async with asyncio.timeout(3):
                while old in service.states.values():
                    await asyncio.sleep(0.02)
            assert len(service.states) == 1
            assert not call.done()
            await owner_decide(url, approval)
            assert not (await call).is_error


@pytest.mark.anyio
async def test_unexpected_call_failure_closes_pending_receipts(tmp_path, monkeypatch):
    async with running(tmp_path, monkeypatch) as (h, service, url):
        issued = service.clients.create("Fault test", save_token=False)

        async def fail(ctx, name, args):
            row = h.ledger.begin(
                run_id=ctx.run_id,
                tool=name,
                capability="mcp",
                hand="mcp",
                args={},
                effect="observe",
                principal={"kind": "mcp_client", "client_id": issued["id"]},
            )
            h.ledger.dispatched(row["id"])
            raise RuntimeError("PRIVATE_EXCEPTION_SENTINEL")

        monkeypatch.setattr(h.registry, "call", fail)
        async with sdk(url, issued["token"]) as client:
            result = await client.call_tool("panini_status", {})
            assert result.is_error and "PRIVATE_EXCEPTION_SENTINEL" not in result.content[0].text
        assert not h.store.all("SELECT id FROM receipts WHERE outcome='pending'")
        assert (
            h.store.one("SELECT outcome FROM receipts WHERE tool='mcp_status'")["outcome"] == "unknown_effect"
        )


@pytest.mark.anyio
async def test_pages_require_explicit_scope_and_only_offer_read_tools(tmp_path, monkeypatch):
    monkeypatch.setenv("PANINI_SPACES", "true")
    async with running(tmp_path, monkeypatch) as (h, service, url):
        space = h.spaces.create_space("MCP notebook")
        page = h.spaces.create(space["id"], "MCP sample", "Explicitly shared notebook text")
        limited = service.clients.create("No notebooks", [], save_token=False)
        granted = service.clients.create("Notebook reader", ["pages"], save_token=False)
        async with sdk(url, limited["token"]) as client:
            assert "pages_read" not in {t.name for t in (await client.list_tools()).tools}
            assert (await client.call_tool("pages_read", {"page_id": page["id"]})).is_error
        async with sdk(url, granted["token"]) as client:
            names = {t.name for t in (await client.list_tools()).tools}
            assert {"pages_search", "pages_read"} <= names
            assert not {"pages_edit", "pages_create", "pages_delete"} & names
            result = await client.call_tool("pages_read", {"page_id": page["id"]})
            assert not result.is_error and page["content"] in result.content[0].text
            assert not h.interrupts.list()


@pytest.mark.anyio
async def test_task_model_strict_schema_and_nullable_optional_arguments(tmp_path, monkeypatch):
    async with running(tmp_path, monkeypatch) as (h, service, url):
        from jsonschema import Draft202012Validator

        class CheckedModel(ScriptedModel):
            async def respond(self, instructions, items, tools, **kwargs):
                receipts = next(t for t in tools if t["name"] == "mcp_receipts_search")
                schema = receipts["parameters"]
                assert receipts["strict"] is True
                assert set(schema["required"]) == {"query", "since"}
                assert schema["additionalProperties"] is False
                Draft202012Validator(schema).validate({"query": None, "since": None})
                screenshot = next(t for t in tools if t["name"] == "mcp_desktop_screenshot")
                assert screenshot["parameters"]["required"] == ["window"]
                Draft202012Validator(screenshot["parameters"]).validate({"window": None})
                return await super().respond(instructions, items, tools, **kwargs)

        model = CheckedModel(
            [
                {"calls": [{"name": "mcp_receipts_search", "arguments": {"query": None, "since": None}}]},
                {"text": "Checked receipts."},
            ]
        )
        h.model_factory = lambda: model
        issued = service.clients.create("Schema test", save_token=False)
        original = json.dumps(h.registry.tool("mcp_receipts_search").input_schema)
        async with sdk(url, issued["token"]) as client:
            result = await client.call_tool("panini_ask", {"goal": "Read my receipts."})
            assert not result.is_error
        assert "Invalid or unavailable" not in json.dumps(model.requests)
        assert json.dumps(h.registry.tool("mcp_receipts_search").input_schema) == original
        assert h.store.one("SELECT outcome FROM receipts WHERE tool='mcp_receipts_search'")["outcome"] == "ok"
