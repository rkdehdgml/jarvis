"""
telegram.py — /api/telegram 라우터
──────────────────────────────────────────────────────────────────────────────
엔드포인트:
  GET  /api/telegram/status   봇 활성 여부 + 허용 ID 수 반환
  GET  /api/telegram/stream   SSE — 원격 명령 실행 알림
"""

import asyncio
import json

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.services.telegram_service import telegram_bot

router = APIRouter()


@router.get("/status")
async def bot_status():
    """텔레그램 봇 상태 반환."""
    return {
        "active":      telegram_bot.is_active(),
        "description": "Telegram 원격 제어 봇",
    }


@router.get("/stream")
async def telegram_stream():
    """원격 명령 실행 SSE 스트림 (오버레이 알림용)."""
    q = telegram_bot.subscribe()

    async def event_gen():
        try:
            while True:
                try:
                    payload = await asyncio.wait_for(q.get(), timeout=25)
                    data    = json.dumps(payload, ensure_ascii=False)
                    yield f"data: {data}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            telegram_bot.unsubscribe(q)

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":    "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
