"""V2-27 local model routing and the private-mode Jev boundary."""

import asyncio
from types import SimpleNamespace

import httpx
import pytest
from panini_cli.agent import model, model_router
from panini_cli.agent.local_model import FallbackModel, LocalModel, endpoint

from backend import config, jev_client


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
