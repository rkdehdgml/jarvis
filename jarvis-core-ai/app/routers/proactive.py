"""
proactive.py — /api/proactive 라우터
──────────────────────────────────────────────────────────────────────────────
엔드포인트:
  GET /api/proactive/stream  → 능동적 제안 SSE 스트림
  GET /api/proactive/status  → 서비스 상태 및 기억 요약

SSE 이벤트 형식:
  data: {"event": "suggestion", "category": "morning", "text": "..."}
  data: {"event": "ping"}
"""

import asyncio
import json

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.services.proactive_service import proactive
from app.services.memory_service import memory

router = APIRouter()


@router.get("/stream")
async def proactive_stream():
    """능동적 제안을 SSE로 실시간 전달."""
    q = proactive.subscribe()

    async def generate():
        yield f"data: {json.dumps({'event': 'connected'})}\n\n"
        try:
            while True:
                try:
                    payload = await asyncio.wait_for(q.get(), timeout=30.0)
                    yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield "data: {\"event\":\"ping\"}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            proactive.unsubscribe(q)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":    "no-cache",
            "X-Accel-Buffering": "no",
            "Connection":       "keep-alive",
        },
    )


@router.get("/status")
async def proactive_status():
    """기억 요약 및 서비스 상태 반환."""
    return {
        "memory_summary": memory.get_summary(),
        "recent":         memory.get_recent(3),
    }
