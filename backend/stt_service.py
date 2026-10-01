"""Bounded AssemblyAI transcription with capability reporting."""

import asyncio
import io
import os
import time

from . import config
from .errors import provider_error

try:
    import assemblyai as aai
except ImportError:
    aai = None


def is_configured() -> bool:
    return aai is not None and config.configured("ASSEMBLYAI_API_KEY")


def get_status() -> dict:
    return {
        "assemblyai_installed": aai is not None,
        "configured": is_configured(),
        "fallback": "Web Speech API (browser native)" if not is_configured() else None,
    }


async def transcribe_audio_bytes(audio_bytes: bytes) -> dict:
    if not is_configured():
        raise RuntimeError("AssemblyAI is not configured; use Web Speech or enter text")
    if not 500 <= len(audio_bytes) <= config.MAX_AUDIO_BYTES:
        raise ValueError("Audio must be between 500 bytes and 10 MiB")
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
