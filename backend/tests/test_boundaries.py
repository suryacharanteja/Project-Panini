import asyncio
import logging
from types import SimpleNamespace
from uuid import UUID

import httpx
import pytest

from backend import comparison, stt_service
from backend.app import create_app
from backend.work import CapacityError, WorkPool


def test_disconnected_caller_cannot_bypass_capacity():
    async def run():
        pool = WorkPool(1)
        started, finish = asyncio.Event(), asyncio.Event()

        async def operation():
            started.set()
            await finish.wait()
            return "completed"

        caller = asyncio.create_task(pool.run(operation))
        await started.wait()
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
        with pytest.raises(CapacityError):
            await pool.run(operation)
        assert pool.active == 1
        finish.set()
        await pool.close()
        assert pool.active == 0
        with pytest.raises(CapacityError):
            await pool.run(operation)

    asyncio.run(run())


def test_overload_is_429_and_service_recovers():
    async def run():
        started, finish = asyncio.Event(), asyncio.Event()

        async def compare(*args):
            started.set()
            await finish.wait()
            return {"direct": {}, "jev": {}}

        app = create_app(comparison_limit=1, compare_operation=compare)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as client:
            payload = {"text": "hello", "task": "sentiment"}
            first = asyncio.create_task(client.post("/api/compare", json=payload))
            await started.wait()
            overloaded = await client.post("/api/compare", json=payload)
            assert overloaded.status_code == 429
            assert overloaded.headers["retry-after"] == "1"
            health = (await client.get("/api/health")).json()
            assert health["work"]["comparisons"]["active"] == 1
            assert health["work"]["transcriptions"]["active"] == 0
            finish.set()
            assert (await first).status_code == 200
            assert (await client.post("/api/compare", json=payload)).status_code == 200

    asyncio.run(run())


def test_body_limit_covers_chunked_requests_without_content_length():
    async def run():
        async def chunks():
            for _ in range(16):
                yield b"x" * 1024 * 1024

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app()), base_url="http://localhost"
        ) as client:
            response = await client.post(
                "/api/compare", content=chunks(), headers={"content-type": "application/json"}
            )
            assert response.status_code == 413
            assert response.headers["cache-control"] == "no-store"

    asyncio.run(run())


def test_request_trace_does_not_log_body_or_query(caplog):
    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app()), base_url="http://localhost"
        ) as client:
            response = await client.post(
                "/api/compare?private=never-log-this", json={"text": "secret-prompt", "task": "missing"}
            )
            assert response.status_code == 422
            UUID(response.headers["x-request-id"])

    with caplog.at_level(logging.INFO, logger="jev.http"):
        asyncio.run(run())
    messages = " ".join(record.message for record in caplog.records if record.name == "jev.http")
    assert '"route": "/api/compare"' in messages
    assert "secret-prompt" not in messages
    assert "never-log-this" not in messages


def test_upstream_error_body_is_not_returned_or_logged(monkeypatch, caplog):
    monkeypatch.setenv("OPENAI_API_KEY", "test-configured-key")

    def failed():
        raise RuntimeError("upstream echoed private-key-value and user-prompt")

    monkeypatch.setattr(comparison, "get_openai_client", failed)
    result = comparison.direct_llm_classify("hello", "sentiment")
    assert "error" in result
    assert "private-key-value" not in str(result) + caplog.text
    assert "user-prompt" not in str(result) + caplog.text


def test_speech_cancellation_closes_async_client(monkeypatch):
    monkeypatch.setenv("ASSEMBLYAI_API_KEY", "test-configured-key")

    async def run():
        entered, closed = asyncio.Event(), asyncio.Event()

        class Client:
            def __init__(self, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                closed.set()

        class Transcriber:
            def __init__(self, **kwargs):
                pass

            async def transcribe(self, *args, **kwargs):
                entered.set()
                await asyncio.Event().wait()

        monkeypatch.setattr(
            stt_service,
            "aai",
            SimpleNamespace(
                AsyncClient=Client, AsyncTranscriber=Transcriber, TranscriptionConfig=lambda **kwargs: None
            ),
        )
        task = asyncio.create_task(stt_service.transcribe_audio_bytes(b"x" * 500))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert closed.is_set()

    asyncio.run(run())


def test_application_factories_do_not_share_work_or_browser_state():
    first, second = create_app(), create_app()
    assert first.state.comparison_work is not second.state.comparison_work
    first.state.browser_loop = object()
    assert second.state.browser_loop is None


def test_reference_snapshots_are_never_imported():
    """references/ holds upstream snapshots for reading only (docs/panini-enhancements/harvest-map.md)."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    pattern = re.compile(r"^\s*(from|import)\s+references\b|['\"]references[/\\]", re.M)
    offenders = []
    for folder in ("backend", "frontend/server", "panini-core", "scripts"):
        for path in (root / folder).rglob("*"):
            if path.suffix not in {".py", ".mjs", ".js", ".ts", ".tsx"} or "node_modules" in path.parts:
                continue
            if path.name in {"test_boundaries.py", "check-pins.mjs"}:  # Read-only pin verification.
                continue
            if pattern.search(path.read_text(encoding="utf-8", errors="ignore")):
                offenders.append(str(path.relative_to(root)))
    assert offenders == []
