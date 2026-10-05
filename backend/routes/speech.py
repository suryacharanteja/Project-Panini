import asyncio
import base64
import binascii

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from .. import config, local_stt, private_mode, stt_service

router = APIRouter(prefix="/api/stt")


@router.get("/status")
async def status():
    return stt_service.get_status()


async def transcribe(data: bytes, request: Request):
    if not 500 <= len(data) <= config.MAX_AUDIO_BYTES:
        raise HTTPException(400 if len(data) < 500 else 413, "Audio must be between 500 bytes and 10 MiB")
    if not stt_service.can_transcribe():
        raise HTTPException(503, "No transcription engine is ready for this mode")
    try:
        operation = (
            local_stt.transcribe_audio_bytes
            if private_mode.active()
            else request.app.state.transcribe_service
        )
        return await request.app.state.speech_work.run(lambda: operation(data))
    except asyncio.TimeoutError as exc:
        raise HTTPException(504, "Transcription timed out") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)[:300]) from exc
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)[:300]) from exc


@router.post("/transcribe")
async def upload(request: Request, file: UploadFile = File(...)):
    try:
        return await transcribe(await file.read(config.MAX_AUDIO_BYTES + 1), request)
    finally:
        await file.close()


class Base64Audio(BaseModel):
    audio_b64: str = Field(min_length=1, max_length=(config.MAX_AUDIO_BYTES * 4 // 3) + 1024)
    mime_type: str = "audio/webm"
    filename: str = "audio.webm"


@router.post("/transcribe-base64")
async def encoded(payload: Base64Audio, request: Request):
    data = payload.audio_b64
    if data.startswith("data:") and "," in data:
        data = data.split(",", 1)[1]
    try:
        decoded = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(400, "Invalid base64 audio") from exc
    return await transcribe(decoded, request)
