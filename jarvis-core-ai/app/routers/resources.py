"""
resources.py — /api/resources 라우터
──────────────────────────────────────────────────────────────────────────────
엔드포인트:
  GET /api/resources/stream  → CPU·RAM·디스크 실시간 SSE 스트림
  GET /api/resources/status  → 현재 자원 통계 스냅샷

SSE 이벤트:
  {"event": "stats",      "cpu_percent": 42.1, "ram_percent": 61.0, ...}
  {"event": "high_load",  "cpu_percent": 88.5, ...}
  {"event": "normal",     "cpu_percent": 40.0, ...}
"""

import asyncio
import json

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.services.resource_monitor import monitor

router = APIRouter()


@router.get("/stream")
async def resource_stream():
    """시스템 자원 통계를 SSE로 실시간 전달."""
    q = monitor.subscribe()

    async def generate():
        # 연결 시 현재 스냅샷 즉시 전송
        latest = monitor.latest_stats()
        if latest:
            yield f"data: {json.dumps({'event': 'stats', **latest})}\n\n"

        try:
            while True:
                try:
                    payload = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield "data: {\"event\":\"ping\"}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            monitor.unsubscribe(q)

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
async def resource_status():
    return {
        "stats":     monitor.latest_stats(),
        "high_load": monitor.is_high_load,
    }
