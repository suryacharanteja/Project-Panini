"""Bounded Windows system-voice synthesis; no provider client or network request."""

import asyncio
import base64
import io
import os
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

MAX_TEXT = 500
MAX_WAV = 3_000_000


def readiness():
    return {"ready": sys.platform == "win32", "engine": "windows-sapi-local"}


def _worker(path: Path):
    import pythoncom
    import win32com.client

    text = sys.stdin.buffer.read(MAX_TEXT * 4 + 1).decode("utf-8")
    if (
        not text.strip()
        or len(text) > MAX_TEXT
        or any(ord(char) < 32 and char not in "\n\t" for char in text)
    ):
        raise ValueError("Invalid local speech text.")
    pythoncom.CoInitialize()
    try:
        voice = win32com.client.Dispatch("SAPI.SpVoice")
        voices = voice.GetVoices()
        selected = next(
            (
                voices.Item(i)
                for i in range(voices.Count)
                if voices.Item(i).GetDescription().startswith("Microsoft ")
            ),
            None,
        )
        if selected is None:
            raise RuntimeError("No installed Microsoft system voice is available.")
        voice.Voice = selected
        stream = win32com.client.Dispatch("SAPI.SpFileStream")
        stream.Open(str(path), 3, False)  # SSFMCreateForWrite; SAPI writes a complete WAV header.
        try:
            voice.AudioOutputStream = stream
            voice.Speak(text, 16)  # SVSFIsNotXML: text cannot be interpreted as SSML.
        finally:
            stream.Close()
    finally:
        pythoncom.CoUninitialize()


def _synthesize(text: str) -> bytes:
    if sys.platform != "win32":
        raise RuntimeError("Local system speech needs Windows.")
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT:
        raise ValueError("Speak between 1 and 500 characters.")
    if any(ord(char) < 32 and char not in "\n\t" for char in text):
        raise ValueError("Unsupported control character in speech text.")
    with tempfile.TemporaryDirectory(prefix="panini-local-tts-") as folder:
        path = Path(folder) / "reply.wav"
        command = (
            [sys.executable, "--local-tts-worker", str(path)]
            if getattr(sys, "frozen", False)
            else [sys.executable, "-m", "backend.local_tts", "--worker", str(path)]
        )
        try:
            result = subprocess.run(
                command,
                input=text.encode("utf-8"),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=20,
                check=False,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("Local speech timed out.") from exc
        if result.returncode != 0 or not path.is_file():
            raise RuntimeError("The installed Windows voice could not synthesize this text.")
        if not 44 <= path.stat().st_size <= MAX_WAV:
            raise RuntimeError("Local speech returned an invalid size.")
        audio = path.read_bytes()
    try:
        with wave.open(io.BytesIO(audio), "rb") as wav:
            if (
                wav.getnchannels() != 1
                or wav.getsampwidth() != 2
                or not 8000 <= wav.getframerate() <= 48000
                or wav.getnframes() / wav.getframerate() > 45
            ):
                raise RuntimeError("Local speech returned an unsupported WAV.")
    except wave.Error as exc:
        raise RuntimeError("Local speech returned an invalid WAV.") from exc
    return audio


async def synthesize(text: str):
    audio = await asyncio.to_thread(_synthesize, text)
    return {"audio_b64": base64.b64encode(audio).decode("ascii"), "engine": "windows-sapi-local"}


if __name__ == "__main__" and sys.argv[1:2] == ["--worker"]:
    _worker(Path(sys.argv[2]))
