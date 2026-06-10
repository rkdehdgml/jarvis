"""
tts_service.py — JARVIS TTS (Text-to-Speech) 서비스
edge-tts 라이브러리로 텍스트를 MP3 음성으로 변환합니다.
"""
from __future__ import annotations

import io
from typing import AsyncGenerator

import edge_tts

VOICES: dict[str, str] = {
    "ko_female": "ko-KR-SunHiNeural",
    "ko_male":   "ko-KR-InJoonNeural",
    "en_female": "en-US-JennyNeural",
    "en_male":   "en-US-GuyNeural",
}

DEFAULT_VOICE = "ko-KR-SunHiNeural"


class TtsService:

    def __init__(self, voice: str = DEFAULT_VOICE, rate: str = "+0%"):
        self.voice = voice
        self.rate  = rate

    async def stream(self, text: str) -> AsyncGenerator[bytes, None]:
        """텍스트를 MP3 바이트 청크로 스트리밍."""
        communicate = edge_tts.Communicate(text, self.voice, rate=self.rate)
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                yield chunk["data"]

    async def to_bytes(self, text: str) -> bytes:
        """텍스트를 완전한 MP3 바이트로 반환."""
        buf = io.BytesIO()
        async for data in self.stream(text):
            buf.write(data)
        return buf.getvalue()


# 모듈 레벨 기본 인스턴스
service = TtsService()
