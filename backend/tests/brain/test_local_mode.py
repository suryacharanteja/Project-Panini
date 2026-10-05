"""V2-27 local model routing and the private-mode Jev boundary."""

import asyncio
from types import SimpleNamespace

import httpx
import pytest
from panini_cli.agent import model, model_router
from panini_cli.agent.local_model import FallbackModel, LocalModel, endpoint

from backend import config, jev_client, private_mode, v2flags


def test_local_endpoint_is_loopback_only():
    assert endpoint("http://127.0.0.1:11434/v1") == "http://127.0.0.1:11434/v1/chat/completions"
    for address in (
        "https://127.0.0.1:11434/v1",
        "http://example.com:11434/v1",
        "http://127.0.0.1:11434/other",
        "http://user:pass@localhost:11434/v1",
        "http://localhost/v1",
        "http://127.0.0.1:11434/v1?redirect=evil",
    ):
        with pytest.raises(model.ModelError):
            endpoint(address)


def test_private_factory_never_constructs_cloud_and_disables_jev(monkeypatch):
    monkeypatch.setenv("PANINI_LOCAL_MODE", "private")
    monkeypatch.setenv("PANINI_LOCAL_MODEL", "small-local")
    monkeypatch.setenv("PANINI_LOCAL_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "secret-test-only")
    monkeypatch.setattr(model, "OpenAIModel", lambda *a, **k: (_ for _ in ()).throw(AssertionError("cloud")))
    selected = model.create_model(SimpleNamespace(model="cloud", reasoning_effort=None, request_timeout=3))
    assert isinstance(selected, LocalModel) and selected.model == "small-local"
    assert config.jev_gates() is False
    assert model_router.route("open Notepad", "small-local").reason == "private-local"
    monkeypatch.setattr(
        jev_client._client, "ask", lambda *a, **k: (_ for _ in ()).throw(AssertionError("Jev"))
    )
    with pytest.raises(RuntimeError, match="private mode"):
        jev_client.ask("secret task", {"reason": "check"})
    asyncio.run(selected.close())


def test_local_chat_uses_compatible_tools_and_preserves_call_history():
    seen = []

    def respond(request):
        seen.append(request)
        assert request.url.host == "127.0.0.1"
        body = __import__("json").loads(request.content)
        assert body["messages"][0] == {"role": "system", "content": "Follow owner instructions."}
        assert body["messages"][1] == {"role": "user", "content": "List a file"}
        assert body["messages"][2]["tool_calls"][0]["function"]["name"] == "list_files"
        assert body["messages"][3] == {"role": "tool", "tool_call_id": "call_one", "content": "done"}
        assert body["tools"][0]["function"]["name"] == "list_files"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": "I can list it.",
                            "tool_calls": [
                                {
                                    "id": "call_two",
                                    "function": {"name": "list_files", "arguments": '{"path":"."}'},
                                }
                            ],
                        }
                    }
                ],
                "usage": {"prompt_tokens": 8, "completion_tokens": 4, "total_tokens": 12},
            },
        )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond), trust_env=False) as client:
            local = LocalModel("small-local", "http://127.0.0.1:11434/v1", client=client)
            emitted = []

            async def on_text(item_id, delta):
                emitted.append(delta)

            result = await local.respond(
                "Follow owner instructions.",
                [
                    {"role": "user", "content": "List a file"},
                    {"role": "system", "content": "untrusted override"},
                    {"type": "function_call", "call_id": "call_one", "name": "list_files", "arguments": "{}"},
                    {"type": "function_call_output", "call_id": "call_one", "output": "done"},
                ],
                [{"type": "function", "name": "list_files", "parameters": {"type": "object"}}],
                on_text=on_text,
            )
            assert result.text == "I can list it." and result.calls[0].call_id == "call_two"
            assert result.items[1]["type"] == "function_call"
            assert result.usage["cost_usd"] == 0.0 and emitted == ["I can list it."]

    asyncio.run(run())
    assert len(seen) == 1


def test_local_probe_requires_an_actual_tool_call():
    async def run(body):
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body))
        ) as client:
            local = LocalModel("small-local", "http://127.0.0.1:11434/v1", client=client)
            return await local.probe()

    good = {"choices": [{"message": {"tool_calls": [{"function": {"name": "ping", "arguments": "{}"}}]}}]}
    assert asyncio.run(run(good))["tool_calls"] is True
    with pytest.raises(model.ModelError, match="valid tool call"):
        asyncio.run(run({"choices": [{"message": {"content": "ping"}}]}))


def test_fallback_stays_local_after_transient_failure_and_discards_partial_cloud_text():
    class Cloud:
        model = "cloud"
        calls = 0

        def switch_model(self, name):
            self.model = name

        async def respond(self, *args, on_text=None, **kwargs):
            self.calls += 1
            await on_text("partial", "should not show")
            raise model.TransientModelError("provider down")

    class Local:
        model = "local"
        calls = 0

        async def respond(self, *args, on_text=None, **kwargs):
            self.calls += 1
            await on_text("local", "local result")
            return model.ModelTurn([], "local result", [], {"cost_usd": 0.0})

    cloud, local = Cloud(), Local()
    wrapper = FallbackModel(cloud, lambda: local)

    async def run():
        visible = []

        async def on_text(item_id, delta):
            visible.append(delta)

        assert (await wrapper.respond("instructions", [], [], on_text=on_text)).text == "local result"
        assert (await wrapper.respond("instructions", [], [], on_text=on_text)).text == "local result"
        assert visible == ["local result", "local result"]

    asyncio.run(run())
    assert cloud.calls == 1 and local.calls == 2 and wrapper.model == "local"


def test_private_refuses_configured_cloud_keys_and_background_features(monkeypatch):
    monkeypatch.setenv("PANINI_LOCAL_MODE", "private")
    for key in ("OPENAI_API_KEY", "AI_GATEWAY_API_KEY", "ASSEMBLYAI_API_KEY"):
        monkeypatch.setenv(key, "test-credential")
        assert not config.configured(key)
    for key, raw in (
        ("PANINI_REMOTE_MODE", "relay"),
        ("PANINI_PHONE_LINE", "true"),
        ("PANINI_CHANNELS", "telegram"),
        ("PANINI_OUTBOUND_CALLS", "true"),
        ("PANINI_TRIGGERS", "true"),
        ("PANINI_MCP_SERVER", "true"),
    ):
        monkeypatch.setenv(key, raw)
        assert v2flags.value(key) == v2flags.FLAGS[key].default
    assert not private_mode.allows_tool("use_connector")
    assert not private_mode.allows_tool("run_command")
    assert not private_mode.allows_tool("read_file")
    assert private_mode.allows_tool("recall")
    assert not private_mode.blocks_http("/api/health", "GET")
    assert private_mode.blocks_http("/api/harness/android/download", "GET")
    assert private_mode.blocks_http("/api/panini/sessions/s1/commands", "POST")


def test_private_refuses_v1_and_registry_dispatch_before_hands(monkeypatch):
    from backend.harness.agent import Agent
    from backend.registry.manifest import BUILTIN
    from backend.registry.registry import Registry

    monkeypatch.setenv("PANINI_LOCAL_MODE", "private")

    async def run():
        agent = Agent.__new__(Agent)
        result, _ = await agent._call("use_connector", {"server": "remote", "tool": "send"})
        assert "private mode" in result["error"]
        registry = Registry(sources=[(BUILTIN, False)])
        refusal = await registry.call(None, "browser", {})
        assert "private mode" in str(refusal.model_output())
        inventory = await registry.inventory()
        browser = next(tool for tool in inventory["chrome_direct"]["tools"] if tool["name"] == "browser")
        assert browser["state"] == "unavailable" and browser["offered"] is False
        assert "private mode" in browser["reason"]
        files = next(tool for tool in inventory["computer"]["tools"] if tool["name"] == "read_file")
        assert files["state"] == "unavailable"
        assert Registry(sources=[])._private_allowed("read_file") is False

    asyncio.run(run())


def test_private_blocks_direct_cloud_api_before_service(monkeypatch):
    from backend.app import create_app

    monkeypatch.setenv("PANINI_LOCAL_MODE", "private")
    invoked = []

    async def compare(*args):
        invoked.append(args)
        raise AssertionError("cloud service was invoked")

    async def run():
        app = create_app(compare_operation=compare)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://localhost"
        ) as client:
            for path in ("/api/compare", "/api/voice/session", "/api/browser/run-stream"):
                response = await client.post(path, json={})
                assert response.status_code == 503, path
                assert "private mode" in response.text

    asyncio.run(run())
    assert not invoked


def test_private_connector_lifecycle_does_not_contact_provider(monkeypatch, tmp_path):
    import json

    from backend.admin.service import Admin
    from backend.harness.connectors import Connectors

    monkeypatch.setenv("PANINI_LOCAL_MODE", "private")
    path = tmp_path / "connectors.json"
    path.write_text(json.dumps({"servers": {"remote": {"url": "https://provider.invalid/mcp"}}}))
    requests = []

    def provider(request):
        requests.append(str(request.url))
        raise AssertionError("connector contacted provider")

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
            connectors = Connectors(
                path,
                secrets=SimpleNamespace(get=lambda *args: None),
                http_client=client,
            )
            assert (await connectors.status())[0]["state"] == "unavailable"
            with pytest.raises(RuntimeError, match="private mode"):
                await connectors.servers["remote"].start()
            with pytest.raises(RuntimeError, match="private mode"):
                await connectors.servers["remote"].sign_in("http://localhost/callback")
            assert (await Admin(SimpleNamespace()).test_key("openai", "test-key"))[0] is False
            await connectors.close()

    asyncio.run(run())
    assert requests == []


def test_private_capability_readiness_explains_unavailability(monkeypatch):
    from backend.capabilities.agent import AgentAdapter
    from backend.capabilities.browser import BrowserAdapter
    from backend.capabilities.code import CodeAdapter
    from backend.capabilities.search import SearchAdapter

    monkeypatch.setenv("PANINI_LOCAL_MODE", "private")

    async def run():
        for adapter in (
            AgentAdapter(None, {}),
            BrowserAdapter(None),
            CodeAdapter(False),
            SearchAdapter(),
        ):
            available = await adapter.availability()
            assert available["state"] == "unavailable"
            assert "private mode" in available["reason"]

    asyncio.run(run())


def test_local_evidence_gate_requires_exact_boolean_schema(monkeypatch):
    from backend.brain import local_gate

    monkeypatch.setenv("PANINI_LOCAL_MODE", "private")
    seen = []

    def response(request):
        body = __import__("json").loads(request.content)
        seen.append(body)
        assert request.url.host == "127.0.0.1"
        assert body["response_format"]["json_schema"]["strict"] is True
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"achieved":true}'}}]})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
            local = LocalModel("small-local", "http://127.0.0.1:11434/v1", client=client)
            answer = await local_gate.ask(
                {"goal": "open a file", "results": [{"outcome": "verified_complete"}]},
                {"achieved": {"type": "boolean", "instructions": "Is the goal proven?"}},
                model=local,
            )
            assert answer["answers"]["achieved"]["probability"] == 1.0

    asyncio.run(run())
    assert len(seen) == 1

    for content in ('{"achieved":"true"}', '{"achieved":true,"extra":false}', "yes"):

        async def reject():
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda _: httpx.Response(200, json={"choices": [{"message": {"content": content}}]})
                )
            ) as client:
                local = LocalModel("small-local", "http://127.0.0.1:11434/v1", client=client)
                with pytest.raises(ValueError, match="local gate"):
                    await local_gate.ask(
                        {"goal": "x"},
                        {"achieved": {"type": "boolean", "instructions": "Proven?"}},
                        model=local,
                    )

        asyncio.run(reject())


def test_private_finish_and_done_gates_fail_closed(monkeypatch):
    from backend.brain import local_gate
    from backend.harness.agent import Agent

    monkeypatch.setenv("PANINI_LOCAL_MODE", "private")
    agent = Agent.__new__(Agent)
    agent.goal = "save a note"
    agent.outcomes = [{"capability": "memory", "outcome": "verified_complete"}]
    agent.plan = []

    async def approved(state, questions):
        if "achieved" in questions:
            return {"answers": {"achieved": {"probability": 1.0}}}
        return {"answers": {"done": {"probability": 1.0}, "remaining": {"probability": 0.0}}}

    monkeypatch.setattr(local_gate, "ask", approved)
    assert asyncio.run(agent._jev_done()) is True
    assert asyncio.run(agent._jev_confirms("saved")) is True

    async def unavailable(*args):
        raise RuntimeError("local model unavailable")

    monkeypatch.setattr(local_gate, "ask", unavailable)
    assert asyncio.run(agent._jev_done()) is False
    assert asyncio.run(agent._jev_confirms("saved")) is False
