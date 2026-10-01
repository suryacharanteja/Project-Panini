from types import SimpleNamespace

from backend.panini.coordinator import Task, TaskContext
from backend.panini.events import EventHub


def context():
    task = Task("task", "Observe the fixture", "desktop", {"label": "Fixture"}, 1)
    session = SimpleNamespace(
        active=task,
        state="ready",
        lease_epoch=1,
        expires=10_000,
        hub=EventHub("instance", "session"),
    )
    coordinator = SimpleNamespace(clock=lambda: 0)
    return TaskContext(coordinator, session, task), task, session


def test_computer_activity_is_replayable_and_bounded():
    activity, task, session = context()
    activity.computer_activity(
        {
            "surface": "desktop",
            "phase": "proposed",
            "message": "Click Save.",
            "action": {"kind": "click", "x": 12, "y": 18},
            "frame_b64": "thumbnail",
        }
    )
    event = session.hub.history[-1][0]
    assert event["type"] == "computer.activity"
    assert event["data"]["sequence"] == 1
    assert task.trace[0]["action"]["kind"] == "click"


def test_oversized_frame_is_omitted_but_the_action_record_remains():
    activity, task, session = context()
    activity.computer_activity(
        {
            "surface": "desktop",
            "phase": "dispatched",
            "message": "Typed the requested text.",
            "action": {"kind": "type"},
            "frame_b64": "x" * 46_001,
        }
    )
    event = session.hub.history[-1][0]
    assert event["data"]["frame_omitted"] is True
    assert "frame_b64" not in event["data"]
    assert task.trace[0]["action"] == {"kind": "type"}
