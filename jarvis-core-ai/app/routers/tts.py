"""
tts.py — /api/tts 라우터
POST /speak   텍스트를 MP3 오디오 스트림으로 변환
GET  /voices  지원 음성 목록 반환
"""
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.services.tts_service import TtsService, VOICES

router = APIRouter()


class SpeakRequest(BaseModel):
    text: str
    voice_key: str = "ko_female"
    rate: str      = "+0%"


@router.post("/speak")
async def speak(req: SpeakRequest):
    """텍스트 → MP3 오디오 스트리밍 응답."""
    voice = VOICES.get(req.voice_key, VOICES["ko_female"])
    tts   = TtsService(voice=voice, rate=req.rate)

    return StreamingResponse(
        tts.stream(req.text),
        media_type="audio/mpeg",
    )


@router.get("/voices")
async def list_voices():
    """지원하는 TTS 음성 목록."""
    return {"voices": [{"key": k, "voice": v} for k, v in VOICES.items()]}
