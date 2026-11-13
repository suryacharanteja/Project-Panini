"""V2-27 offline dictation routing never reaches a cloud engine in private mode."""

import asyncio
from types import SimpleNamespace

from fastapi.testclient import TestClient

from backend import local_stt, stt_service
from backend.app import app


def test_local_model_is_an_existing_folder_not_a_download_alias(monkeypatch, tmp_path):
    monkeypatch.setenv("PANINI_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(local_stt, "WhisperModel", object())
    assert not local_stt.readiness()["ready"]
    folder = tmp_path / "models" / "whisper"
    folder.mkdir(parents=True)
    (folder / "model.bin").write_bytes(b"model")
    (folder / "config.json").write_text("{}", encoding="utf-8")
    (folder / "tokenizer.json").write_text("{}", encoding="utf-8")
    (folder / "vocabulary.txt").write_text("vocabulary", encoding="utf-8")
    assert local_stt.readiness()["ready"]
    monkeypatch.setattr(local_stt, "_local_disk", lambda _path: False)
    assert not local_stt.readiness()["ready"]


def test_private_transcription_uses_only_the_local_model(monkeypatch, tmp_path):
    monkeypatch.setenv("PANINI_LOCAL_MODE", "private")
    monkeypatch.setenv("PANINI_DATA_DIR", str(tmp_path))
    folder = tmp_path / "models" / "whisper"
    folder.mkdir(parents=True)
    (folder / "model.bin").write_bytes(b"model")
    (folder / "config.json").write_text("{}", encoding="utf-8")
    (folder / "tokenizer.json").write_text("{}", encoding="utf-8")
    (folder / "vocabulary.txt").write_text("vocabulary", encoding="utf-8")
    loaded = []

    class FakeWhisper:
        def __init__(self, path, **kwargs):
            loaded.append((path, kwargs))

        def transcribe(self, audio, **kwargs):
            assert audio.read() == b"x" * 500
            return iter([SimpleNamespace(text=" hello "), SimpleNamespace(text=" world")]), SimpleNamespace(
                language="en"
            )

    monkeypatch.setattr(local_stt, "WhisperModel", FakeWhisper)
    monkeypatch.setattr(local_stt, "_model", None)
    monkeypatch.setattr(local_stt, "_model_path", None)
    monkeypatch.setattr(stt_service, "aai", None)
    result = asyncio.run(stt_service.transcribe_audio_bytes(b"x" * 500))
    assert result["text"] == "hello world"
    assert result["engine"] == "faster-whisper-local"
    assert loaded == [(str(folder), {"device": "cpu", "compute_type": "int8"})]
    assert stt_service.get_status()["fallback"] is None


def test_private_transcription_route_stays_available_for_local_engine(monkeypatch):
    monkeypatch.setenv("PANINI_LOCAL_MODE", "private")
    monkeypatch.setattr(local_stt, "readiness", lambda: {"ready": True})

    async def local(audio):
        assert audio == b"x" * 500
        return {"text": "local", "engine": "faster-whisper-local"}

    monkeypatch.setattr(local_stt, "transcribe_audio_bytes", local)

    async def forbidden_cloud(_audio):
        raise AssertionError("Private-mode route reached the injected cloud operation")

    previous = app.state.transcribe_service
    app.state.transcribe_service = forbidden_cloud
    try:
        with TestClient(app, base_url="http://localhost") as client:
            response = client.post("/api/stt/transcribe", files={"file": ("sample.webm", b"x" * 500)})
    finally:
        app.state.transcribe_service = previous
    assert response.status_code == 200
    assert response.json()["text"] == "local"


def test_fallback_retries_local_after_cloud_failure(monkeypatch):
    monkeypatch.setenv("PANINI_LOCAL_MODE", "fallback")
    monkeypatch.setattr(stt_service, "is_configured", lambda: True)
    monkeypatch.setattr(local_stt, "readiness", lambda: {"ready": True})
    calls = []

    async def cloud(_audio):
        calls.append("cloud")
        raise RuntimeError("provider unavailable")

    async def local(_audio):
        calls.append("local")
        return {"text": "available", "engine": "faster-whisper-local"}

    monkeypatch.setattr(stt_service, "_cloud_transcribe", cloud)
    monkeypatch.setattr(local_stt, "transcribe_audio_bytes", local)
    assert asyncio.run(stt_service.transcribe_audio_bytes(b"x" * 500))["text"] == "available"
    assert calls == ["cloud", "local"]
