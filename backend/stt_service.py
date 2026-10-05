"""Bounded AssemblyAI transcription with capability reporting."""

import asyncio
import io
import os
import time

from . import config, local_stt, private_mode
from .errors import provider_error

try:
    import assemblyai as aai
except ImportError:
    aai = None


def is_configured() -> bool:
    return aai is not None and config.configured("ASSEMBLYAI_API_KEY")


def can_transcribe() -> bool:
    mode = os.getenv("PANINI_LOCAL_MODE", "off").strip().lower()
    if mode == "private":
        return local_stt.readiness()["ready"]
    return is_configured() or (mode == "fallback" and local_stt.readiness()["ready"])


def get_status() -> dict:
    mode = os.getenv("PANINI_LOCAL_MODE", "off").strip().lower()
    local = local_stt.readiness()
    cloud = is_configured()
    if mode == "private":
        engine = "local" if local["ready"] else None
    elif cloud:
        engine = "assemblyai"
    else:
        engine = "local" if mode == "fallback" and local["ready"] else None
    return {
        "mode": mode,
        "assemblyai_installed": aai is not None,
        "configured": cloud,
        "local": local,
        "active_engine": engine,
        "fallback": None if mode == "private" or can_transcribe() else "Web Speech API (browser native)",
    }


async def transcribe_audio_bytes(audio_bytes: bytes) -> dict:
    if not 500 <= len(audio_bytes) <= config.MAX_AUDIO_BYTES:
        raise ValueError("Audio must be between 500 bytes and 10 MiB")
    mode = os.getenv("PANINI_LOCAL_MODE", "off").strip().lower()
    if private_mode.active():
        return await local_stt.transcribe_audio_bytes(audio_bytes)
    if not is_configured():
        if mode == "fallback" and local_stt.readiness()["ready"]:
            return await local_stt.transcribe_audio_bytes(audio_bytes)
        raise RuntimeError("AssemblyAI is not configured; use Web Speech or enter text")
    if mode == "fallback":
        try:
            return await _cloud_transcribe(audio_bytes)
        except (RuntimeError, TimeoutError):
            if local_stt.readiness()["ready"]:
                return await local_stt.transcribe_audio_bytes(audio_bytes)
            raise
    return await _cloud_transcribe(audio_bytes)


async def _cloud_transcribe(audio_bytes: bytes) -> dict:
    started = time.monotonic()

    try:
        async with asyncio.timeout(90), aai.AsyncClient(api_key=os.environ["ASSEMBLYAI_API_KEY"]) as client:
            transcriber = aai.AsyncTranscriber(
                client=client, config=aai.TranscriptionConfig(language_detection=True)
            )
            transcript = await transcriber.transcribe(io.BytesIO(audio_bytes), poll_timeout=60)
            if transcript.status == aai.TranscriptStatus.error:
                raise RuntimeError("AssemblyAI could not transcribe this audio. Try a clearer recording.")
            result = {
                "text": transcript.text or "",
                "confidence": transcript.confidence,
                "language_code": transcript.language_code,
            }
    except TimeoutError:
        raise
    except Exception as exc:
        raise RuntimeError(provider_error("AssemblyAI", exc, "ASSEMBLYAI_API_KEY")) from exc
    return {**result, "latency_ms": int((time.monotonic() - started) * 1000), "engine": "assemblyai"}
