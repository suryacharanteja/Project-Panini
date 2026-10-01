import json

from backend.panini.code_activity import CodeActivity
from backend.panini.coordinator import Task


def test_live_output_is_bounded_redacted_and_completion_reconciles():
    activity = CodeActivity("fixture", "lightweight")
    activity.update({"type": "item.started", "item_id": "1", "kind": "commandExecution", "command": "test"})
    value = activity.update(
        {
            "type": "item.delta",
            "item_id": "1",
            "kind": "commandExecution",
            "delta": "\x1b[31mAPI_KEY=secret-value\n" + "x" * 1000,
        }
    )
    assert "secret-value" not in json.dumps(value)
    assert "[redacted]" in value["items"][0]["output"]
    value = activity.update(
        {
            "type": "item.completed",
            "item_id": "1",
            "kind": "commandExecution",
            "status": "completed",
            "receipt": {"command": "test", "exit_code": 1, "output": "failed"},
        }
    )
    assert value["items"][0]["exit_code"] == 1
    assert value["items"][0]["output"] == "failed"
    for index in range(100):
        value = activity.update(
            {"type": "item.delta", "item_id": str(index), "kind": "commandExecution", "delta": "😀" * 10000}
        )
    assert len(json.dumps(value, ensure_ascii=False).encode()) <= 48000
    assert len(value["items"]) <= 12 and value["trimmed"]


def test_internal_reasoning_is_not_displayed():
    activity = CodeActivity("fixture", "lightweight")
    value = activity.update(
        {"type": "item.completed", "item_id": "reason", "kind": "reasoning", "text": "internal"}
    )
    assert value["items"] == []


def test_task_trace_projection_stays_within_the_snapshot_budget():
    task = Task("fixture", "observe", "desktop", None, 1)
    for index in range(60):
        task.trace.append({"sequence": index, "phase": "observed", "message": "frame"})
    task.trace[-1]["frame_b64"] = "x" * 100_000
    view = task.view()
    assert len(task.trace) == 60  # Retention is owned by TaskContext, not projection.
    assert len(view["trace"]) <= 12
    assert len(json.dumps(view, ensure_ascii=False).encode()) <= 48_000
