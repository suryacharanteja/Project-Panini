import base64

import pytest
from fastapi.testclient import TestClient

from backend import comparison
from backend.app import app
from backend.task_catalog import TASKS

client = TestClient(app, base_url="http://localhost")


def test_catalog_keeps_all_seven_tasks_and_examples():
    response = client.get("/api/tasks")
    assert response.status_code == 200
    assert len(response.json()["tasks"]) == 7
    assert all(len(task.examples) >= 3 for task in TASKS.values())


@pytest.mark.parametrize(
    "payload",
    [
        {"text": " ", "task": "sentiment"},
        {"text": "x", "task": "missing"},
        {"text": "x" * 20001, "task": "sentiment"},
        {"text": 3, "task": "sentiment"},
    ],
)
def test_invalid_comparison_never_calls_providers(monkeypatch, payload):
    def fail(*args):
        pytest.fail("Invalid request reached provider")

    monkeypatch.setattr(comparison, "direct_llm_classify", fail)
    assert client.post("/api/compare", json=payload).status_code == 422


def test_cross_origin_cannot_trigger_browser_or_models():
    assert (
        client.post("/api/browser/stop", headers={"origin": "https://untrusted.example"}).status_code == 403
    )
    assert client.post("/api/browser/stop", headers={"sec-fetch-site": "cross-site"}).status_code == 403


@pytest.mark.parametrize(
    "payload",
    [
        {"instruction": ""},
        {"instruction": "x", "max_steps": 0},
        {"instruction": "x", "timeout_seconds": 100000},
        {"instruction": "x", "url": "file:///private"},
    ],
)
def test_browser_inputs_are_bounded(payload):
    payload["mode"] = "chromium"
    assert client.post("/api/browser/run-stream", json=payload).status_code == 422


@pytest.mark.parametrize("policy,steps,seconds", [("continuous", None, None), ("bounded", 100, 900)])
def test_run_policy_reaches_owned_browser_loop(policy, steps, seconds):
    from backend.app import create_app

    received = []

    class Loop:
        loop_active = False

        async def run_instruction(self, **options):
            received.append(options)
            yield {"event": "complete"}

    test_app = create_app()
    test_app.state.browser_loop = Loop()
    response = TestClient(test_app, base_url="http://localhost").post(
        "/api/browser/run-stream",
        json={
            "instruction": "Complete the task",
            "mode": "chromium",
            "run_policy": policy,
            "max_steps": 100,
            "timeout_seconds": 900,
        },
    )
    assert response.status_code == 200
    assert received[0]["max_steps"] == steps and received[0]["timeout_seconds"] == seconds
    assert "run_policy" not in received[0]


def test_voice_status_does_not_expose_credentials(monkeypatch):
    monkeypatch.setenv("ASSEMBLYAI_API_KEY", "test-private-value")
    response = client.get("/api/stt/status")
    assert "test-private" not in response.text
    assert "key_prefix" not in response.json()


def test_invalid_audio_is_client_error(monkeypatch):
    monkeypatch.delenv("ASSEMBLYAI_API_KEY", raising=False)
    assert client.post("/api/stt/transcribe-base64", json={"audio_b64": "%%%invalid"}).status_code == 400
    assert client.post("/api/stt/transcribe", files={"file": ("empty.webm", b"")}).status_code == 400
    response = client.post(
        "/api/stt/transcribe-base64", json={"audio_b64": base64.b64encode(b"a" * 500).decode()}
    )
    assert response.status_code == 503


def test_busy_history_reset_preserves_history():
    from types import SimpleNamespace

    loop = SimpleNamespace(loop_active=True, instruction_history=["existing"])
    previous, app.state.browser_loop = app.state.browser_loop, loop
    try:
        assert client.post("/api/browser/reset").status_code == 409
        assert loop.instruction_history == ["existing"]
    finally:
        app.state.browser_loop = previous
