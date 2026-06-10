"""
presence.py — /api/presence 라우터
──────────────────────────────────────────────────────────────────────────────
엔드포인트:
  GET /api/presence/stream  → SSE 이벤트 스트림 (away | back | present)
  GET /api/presence/status  → 현재 재석 상태 JSON

SSE 이벤트 형식:
  data: {"event": "away",    "state": "ABSENT"}
  data: {"event": "back",    "state": "PRESENT"}
  data: {"event": "present", "state": "PRESENT"}
"""

import asyncio
import json

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.services.presence_service import presence

router = APIRouter()


@router.get("/stream")
async def presence_stream():
    """재석 상태 변화를 SSE로 실시간 전달."""
    q = presence.subscribe()

    async def generate():
        # 연결 직후 현재 상태 전송
        yield f"data: {json.dumps({'event': 'init', 'state': presence.get_state()})}\n\n"
        try:
            while True:
                try:
                    payload = await asyncio.wait_for(q.get(), timeout=25.0)
                    yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield "data: {\"event\":\"ping\"}\n\n"   # 연결 유지 핑
        except asyncio.CancelledError:
            pass
        finally:
            presence.unsubscribe(q)

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
async def presence_status():
    return {"state": presence.get_state()}
