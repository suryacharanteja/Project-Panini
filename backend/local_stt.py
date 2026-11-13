"""Offline transcription from a bundled or user-provided local model directory.

Never pass a model name to faster-whisper: names trigger a Hugging Face download.
The accepted model is an existing CTranslate2 directory under PANINI_DATA_DIR
or the packaged runtime root, on a fixed local drive.
"""

import asyncio
import ctypes
import io
import os
import time
from pathlib import Path

from . import config
from .extension_pairing import data_directory

try:
    from faster_whisper import WhisperModel
except ImportError:
    WhisperModel = None

_model = None
_model_path = None
_model_lock = asyncio.Lock()


def model_directory() -> Path:
    personal = data_directory().resolve() / "models" / "whisper"
    bundled = Path(config.ROOT).resolve() / "models" / "whisper"
    if _model_files(personal):
        return personal
    if _model_files(bundled):
        return bundled
    return personal


def _model_files(folder: Path) -> bool:
    return folder.is_dir() and all(
        (folder / name).is_file() for name in ("model.bin", "config.json", "tokenizer.json", "vocabulary.txt")
    )


def _local_disk(folder: Path) -> bool:
    resolved = folder.resolve()
    if os.name != "nt":
        return True
    if str(resolved).startswith("\\\\"):
        return False
    # DRIVE_FIXED = 3. Refuse removable and mapped network drives in private mode.
    return bool(resolved.drive) and ctypes.windll.kernel32.GetDriveTypeW(resolved.drive + "\\") == 3


def readiness() -> dict:
    folder = model_directory()
    files_present = _model_files(folder)
    local_disk = _local_disk(folder) and all(
        (folder / name).resolve().is_relative_to(folder.resolve()) and _local_disk(folder / name)
        for name in ("model.bin", "config.json", "tokenizer.json", "vocabulary.txt")
    )
    ready = WhisperModel is not None and files_present and local_disk
    return {
        "ready": ready,
        "installed": WhisperModel is not None,
        "model_present": files_present,
        "local_disk": local_disk,
        "model_directory": str(folder),
    }


def _transcribe(audio: bytes, folder: Path) -> dict:
    global _model, _model_path
    if _model is None or _model_path != folder:
        # Local path only; no size/name alias or hub lookup is possible here.
        _model = WhisperModel(str(folder), device="cpu", compute_type="int8")
        _model_path = folder
    segments, info = _model.transcribe(io.BytesIO(audio), beam_size=1, vad_filter=False)
    if getattr(info, "duration", 0) > 30:
        raise ValueError("Local dictation is limited to 30 seconds per recording.")
    text = " ".join(segment.text.strip() for segment in segments).strip()
    return {
        "text": text,
        "confidence": None,
        "language_code": getattr(info, "language", None),
        "engine": "faster-whisper-local",
    }


async def transcribe_audio_bytes(audio: bytes) -> dict:
    if not readiness()["ready"]:
        raise RuntimeError("Local transcription needs faster-whisper and a model in PANINI's data folder.")
    folder = model_directory()
    started = time.monotonic()
    # One model instance and inference at a time. WorkPool also bounds request count.
    async with _model_lock:
        result = await asyncio.to_thread(_transcribe, audio, folder)
    return {**result, "latency_ms": int((time.monotonic() - started) * 1000)}
