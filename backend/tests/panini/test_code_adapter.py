import json
from types import SimpleNamespace

import pytest

from backend.capabilities.code import CodeAdapter
from backend.panini.contracts import Fault
from backend.panini.settings import Settings
from backend.tests.panini.test_desktop_adapter import Context


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_disabled_and_invalid_workspace_fail_closed(monkeypatch, tmp_path):
    monkeypatch.setenv("PANINI_CLI_WORKSPACES", json.dumps([str(tmp_path)]))
    disabled = CodeAdapter(False)
    assert (await disabled.availability())["state"] == "unavailable"
    assert await disabled.targets() == []
    monkeypatch.setenv("PANINI_CLI_WORKSPACES", '{"bad":"shape"}')
    assert await CodeAdapter(True).targets() == []
    with pytest.raises(ValueError, match="coordinator"):
        Settings(code_enabled=True)


@pytest.mark.anyio
async def test_only_configured_opaque_target_can_be_selected(monkeypatch, tmp_path):
    monkeypatch.setenv("PANINI_CLI_WORKSPACES", json.dumps([str(tmp_path)]))
    adapter = CodeAdapter(True)
    with pytest.raises(Fault):
        await adapter.select(str(tmp_path))
    choices = await adapter.targets()
    selected = await adapter.select(choices[0]["choice_id"])
    assert selected["path"] == str(tmp_path.resolve())
    with pytest.raises(Fault):
        await adapter.run(Context(), "fixture", {**selected, "path": str(tmp_path.parent)})


@pytest.mark.anyio
async def test_receipts_confirmations_and_cleanup_use_coordinator(monkeypatch, tmp_path):
    import panini_cli.engine

    monkeypatch.setenv("PANINI_CLI_WORKSPACES", json.dumps([str(tmp_path)]))
    monkeypatch.setenv("PANINI_CLI_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("JEV_REV_CODE_MODEL", "test-code-model")
    context = Context()
    activities = []
    context.code_activity = lambda value: activities.append(value)

    class FakeEngine:
        def __init__(self, *args):
            self.stopped = self.closed = False

        async def run(self, run_id, instruction, **callbacks):
            assert callbacks["model"] == "test-code-model"
            assert callbacks["reasoning_effort"] == "low"
            await callbacks["emit"](
                {
                    "type": "item.started",
                    "item_id": "command",
                    "kind": "commandExecution",
                    "command": "test fixture",
                }
            )
            await callbacks["emit"](
                {
                    "type": "item.delta",
                    "item_id": "command",
                    "kind": "commandExecution",
                    "delta": "live output",
                }
            )
            assert await callbacks["confirm"]({"request": {"command": "test fixture"}})
            assert not await callbacks["confirm"]({"request": {"command": "x" * 5000}})
            await callbacks["emit"]({"type": "item.completed", "receipt": {"exit_code": 0}})
            return SimpleNamespace(
                status="partial", message="Command exited 0; task not independently verified"
            )

        async def stop(self):
            self.stopped = True

        async def close(self):
            self.closed = True

    monkeypatch.setattr(panini_cli.engine, "CodeEngine", FakeEngine)
    adapter = CodeAdapter(True)
    target = await adapter.select((await adapter.targets())[0]["choice_id"])
    target["target_ref"] = "coordinator-issued-lease-reference"
    result = await adapter.run(context, "fixture", target)
    engine = adapter.engines[context.task.task_id]
    assert result.outcome == "partial" and result.evidence_ids
    assert context.confirmations == 1 and context.dispatched == 1
    assert activities[-1]["items"][0]["output"] == "live output"
    await adapter.stop(context.task.task_id)
    await adapter.release(context.task.task_id)
    assert engine.stopped and engine.closed and not adapter.engines
