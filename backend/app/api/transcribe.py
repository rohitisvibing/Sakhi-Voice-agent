"""Dictation: a short recording of the person's request becomes text they can edit."""

import time
from collections import defaultdict, deque
from typing import Annotated

from fastapi import APIRouter, File, UploadFile

from app.api.profiles import Principal
from app.core.deps import get_container
from app.core.errors import AppError, ErrorKind
from app.models.common import Schema
from app.providers.languages import to_name

router = APIRouter(prefix="/api", tags=["dictation"])

MAX_BYTES = 30 * 16000 * 2 + 4096  # 30 s of 16 kHz mono PCM16 WAV (Sarvam's REST limit)
PER_HOUR = 40
_recent: dict[str, deque[float]] = defaultdict(deque)


class Transcription(Schema):
    text: str
    language: str | None = None


def _rate_limit(pid: str) -> None:
    now = time.monotonic()
    q = _recent[pid]
    while q and now - q[0] > 3600:
        q.popleft()
    if len(q) >= PER_HOUR:
        raise AppError(ErrorKind.rate_limited, "Too many recordings in an hour. Type it instead, "
                       "or try again shortly.")
    q.append(now)


@router.post("/transcribe", response_model=Transcription)
async def transcribe(pid: Principal, audio: Annotated[UploadFile, File()]) -> Transcription:
    c = get_container()
    if not c.voice_available:
        raise AppError(ErrorKind.missing_key, "Voice input isn't set up on this server.")
    data = await audio.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise AppError(ErrorKind.invalid_state, "Recordings can be up to 30 seconds.")
    if len(data) < 16000:  # under half a second
        raise AppError(ErrorKind.invalid_state, "Couldn't hear anything. Try again.")
    _rate_limit(pid)
    result = await c.stt.transcribe(data, filename="request.wav")
    text = (result.text or "").strip()
    if not text:
        raise AppError(ErrorKind.speech_failed, "Couldn't make out any words. Try again.",
                       retryable=True)
    return Transcription(text=text, language=to_name(result.language_code)
                         if result.language_code else None)
