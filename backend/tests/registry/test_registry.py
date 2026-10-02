import asyncio
import json
from types import SimpleNamespace

import pytest

from backend.core_contracts import EffectClass, Principal, ToolResult
from backend.harness.agent import TOOLS
from backend.registry.context import ToolContext, ToolView
from backend.registry.manifest import BUILTIN, ManifestError, load_all, load_file
from backend.registry.mcp import connector_specs, connector_tool_name, effect_for
from backend.registry.registry import Hooks, Registry

FULL = ToolView(connectors=True, remotes=True)


def registry(*extra):
    return Registry({}, sources=[(BUILTIN, False), *extra])


def test_builtin_manifests_load_without_errors():
    r = registry()
    assert r.errors == []
    assert {"use_capability", "search_run", "browser_run", "desktop_run", "code_run"} <= set(r.specs)


def test_model_tools_equal_v1_tools_exactly():
    """Parity: with every requirement met, the registry offers v1's tools, in v1's order, with v1's schemas."""
    r = registry()
    assert r.model_tools(FULL, capabilities=["search", "browser", "desktop", "code"]) == TOOLS


def test_views_apply_v1_rules():
    r = registry()
    names = lambda view: {s.name for s in r.tools(view)}  # noqa: E731
    background = names(ToolView(background=True, tasks=False))
    assert (
        "schedule_task" not in background and "list_projects" not in background and "list_files" in background
    )
    external = names(ToolView(private=False))
    assert not {"recall", "remember", "forget", "ask_agent", "schedule_task"} & external
    assert "run_subagents" not in names(ToolView(depth=1))
    assert "use_connector" not in names(ToolView(connectors=False))
    assert "search_run" not in names(FULL)  # Routed tools are reached through use_capability.


def write(folder, name, data):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(json.dumps(data), encoding="utf-8")


TOOL = {
    "name": "weather_now",
    "hand": "weather",
    "title": "Weather",
    "description": "Current weather for a city.",
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {"city": {"type": "string"}},
        "required": ["city"],
    },
    "effect": "observe",
}


def manifest(**over):
    return {
        "schema": "panini.capability/1",
        "id": "weather",
        "title": "Weather",
        "version": "1",
        "summary": "Weather.",
        "entry": "backend.tests.registry.fixture_hand:WeatherHand",
        "tools": [TOOL],
        **over,
    }


def test_a_new_capability_needs_only_a_manifest_and_a_hand(tmp_path):
    write(tmp_path, "weather.json", manifest())
    r = registry((tmp_path, False))
    assert r.errors == [] and "weather_now" in {t["name"] for t in r.model_tools(FULL)}
    result = asyncio.run(r.call(ToolContext(), "weather_now", {"city": "Pune"}))
    assert result.outcome == "ok" and result.data == {"city": "Pune", "sky": "clear"}


def test_invalid_and_duplicate_manifests_are_reported(tmp_path):
    write(tmp_path, "a.json", manifest(entry="os:system"))
    write(tmp_path, "b.json", manifest(id="weather2", tools=[{**TOOL, "input_schema": {"type": "object"}}]))
    write(tmp_path, "c.json", manifest(id="weather3", tools=[{**TOOL, "effect": "magic"}]))
    write(tmp_path, "d.json", manifest(id="weather4"))
    write(tmp_path, "e.json", manifest(id="weather5"))  # duplicate tool name with d.json
    _, errors = load_all([(tmp_path, False)])
    joined = " ".join(errors)
    assert "backend." in joined and "additionalProperties" in joined and "magic" in joined
    assert "duplicate tool names" in joined


def test_user_manifests_cannot_load_code(tmp_path):
    write(tmp_path, "u.json", manifest())
    with pytest.raises(ManifestError, match="user manifests"):
        load_file(tmp_path / "u.json", user=True)


def test_pipeline_validates_hooks_and_taints(tmp_path):
    write(tmp_path, "weather.json", manifest(tools=[{**TOOL, "taints": ["web"]}]))
    r = registry((tmp_path, False))
    ctx = ToolContext()
    bad = asyncio.run(r.call(ctx, "weather_now", {"city": 3}))
    assert bad.outcome == "blocked" and "Invalid city" in bad.content
    extra = asyncio.run(r.call(ctx, "weather_now", {"city": "x", "evil": 1}))
    assert extra.outcome == "blocked"
    seen = []

    class Stop(Hooks):
        async def before(self, ctx, spec, args):
            seen.append(spec.name)
            return ToolResult("blocked", "policy said no", data={"error": "policy said no"})

    r.hooks.append(Stop())
    stopped = asyncio.run(r.call(ctx, "weather_now", {"city": "x"}))
    assert stopped.content == "policy said no" and seen == ["weather_now"]
    r.hooks.clear()
    asyncio.run(r.call(ctx, "weather_now", {"city": "x"}))
    assert ctx.taint == {"web"}
    assert asyncio.run(r.call(ctx, "nope", {})).outcome == "blocked"


def test_meta_tools_resolve_to_concrete_specs():
    r = registry()
    assert r.resolve("use_capability", {"capability": "desktop", "instruction": "x"}).name == "desktop_run"
    assert (
        r.resolve("use_capability", {"capability": "desktop", "instruction": "x"}).effect
        is EffectClass.SUBMIT
    )


def test_owner_only_tools_refuse_others(tmp_path):
    write(tmp_path, "weather.json", manifest(tools=[{**TOOL, "owner_only": True}]))
    r = registry((tmp_path, False))
    assert "weather_now" not in r.specs or not r.offered["weather_now"]
    result = asyncio.run(
        r.call(ToolContext(principal=Principal(kind="mcp_client")), "weather_now", {"city": "x"})
    )
    assert "owner" in result.content


def test_mcp_annotations_map_to_effects():
    assert effect_for({"readOnlyHint": True}, True) == (EffectClass.OBSERVE, ("mcp",))
    assert effect_for({"readOnlyHint": True, "openWorldHint": False}, True) == (EffectClass.OBSERVE, ())
    assert effect_for({"destructiveHint": False}, False)[0] is EffectClass.SUBMIT
    assert effect_for({}, False)[0] is EffectClass.DESTROY
    server = SimpleNamespace(
        name="Notes",
        spec={"background_read_only": ["list"]},
        tools=[
            {"name": "list", "read_only": True, "annotations": {"readOnlyHint": True}},
            {"name": "delete", "read_only": False, "annotations": {}},
        ],
    )
    specs = {s.name: s for s in connector_specs(SimpleNamespace(servers={"notes": server}))}
    assert specs[connector_tool_name("Notes", "list")].background
    assert specs[connector_tool_name("Notes", "delete")].effect is EffectClass.DESTROY


def test_disabled_capability_is_hidden(tmp_path):
    from backend.harness.store import Store

    store = Store(tmp_path / "h.sqlite3")
    r = Registry({}, sources=[(BUILTIN, False)], state=store)
    store.execute("INSERT INTO capability_state(id, enabled, updated) VALUES ('memory', 0, 0)")
    assert "recall" not in {s.name for s in r.tools(FULL)}
    store.close()


def test_agent_tool_calls_go_through_the_registry(tmp_path, monkeypatch):
    """Invariant I1 groundwork: with the flag on, the agent reaches hands only through Registry.call."""
    from panini_cli.agent.model import ScriptedModel

    from backend.harness.runtime import Harness
    from backend.harness.skills import Skills
    from backend.harness.store import Store

    monkeypatch.setenv("PANINI_V2_REGISTRY", "true")
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    steps = [
        {"calls": [{"name": "list_files", "arguments": {"path": str(tmp_path), "pattern": "*"}}]},
        {"calls": [{"name": "remember", "arguments": {"text": "Likes tea.", "kind": "preference"}}]},
        {"text": "Done."},
    ]
    h = Harness(
        Store(tmp_path / "h.sqlite3"),
        model_factory=lambda: ScriptedModel(steps),
        skills=Skills(user_root=tmp_path / "skills"),
    )
    seen = []
    original = Registry.call

    async def spy(self, ctx, name, args):
        seen.append(name)
        return await original(self, ctx, name, args)

    monkeypatch.setattr(Registry, "call", spy)

    async def delegate(capability, instruction):
        return {"outcome": "verified_complete", "message": "", "results": []}

    agent = h.agent(delegate=delegate, capabilities=("search",))
    assert agent.registry is h.registry
    result = asyncio.run(agent.run("list and remember"))
    assert seen == ["list_files", "remember"]
    assert result.outcome == "verified_complete"
    assert h.memory.list()[0]["text"] == "Likes tea."
    h.store.close()


def test_registry_api_lists_and_toggles(tmp_path):
    from fastapi.testclient import TestClient
    from panini_cli.agent.model import ScriptedModel

    from backend.app import create_app
    from backend.harness.runtime import Harness
    from backend.harness.skills import Skills
    from backend.harness.store import Store

    h = Harness(
        Store(tmp_path / "h.sqlite3"),
        model_factory=lambda: ScriptedModel([]),
        skills=Skills(user_root=tmp_path / "skills"),
    )
    app = create_app(harness_factory=lambda: h, console_token="t" * 32)
    headers = {"x-panini-console": "t" * 32}
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.get("/api/harness/registry").status_code == 401
        body = client.get("/api/harness/registry", headers=headers).json()
        assert body["errors"] == [] and "computer" in body["inventory"]
        assert {t["name"] for t in body["inventory"]["computer"]["tools"]} == {
            "list_files",
            "read_file",
            "run_command",
            "open",
        }
        off = client.patch("/api/harness/registry/memory", json={"enabled": False}, headers=headers)
        assert off.json() == {"id": "memory", "enabled": False}
        assert (
            client.get("/api/harness/registry", headers=headers).json()["inventory"]["memory"]["enabled"]
            is False
        )
        assert (
            client.patch("/api/harness/registry/nope", json={"enabled": False}, headers=headers).status_code
            == 404
        )
