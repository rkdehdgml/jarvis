"""
tasks.py — /api/tasks 라우터
──────────────────────────────────────────────────────────────────────────────
엔드포인트:
  POST   /api/tasks/submit         → 백그라운드 태스크 등록
  GET    /api/tasks/stream         → SSE 이벤트 스트림
  GET    /api/tasks/list           → 등록된 태스크 목록
  DELETE /api/tasks/{task_id}      → 태스크 취소

SSE 이벤트:
  {"event": "submitted",  "task_id": "...", "description": "..."}
  {"event": "progress",   "task_id": "...", "message": "..."}
  {"event": "completed",  "task_id": "...", "result": "..."}
  {"event": "failed",     "task_id": "...", "error": "..."}
  {"event": "cancelled",  "task_id": "..."}
"""

import asyncio
import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.services.task_manager import task_manager

router = APIRouter()


class TaskSubmitRequest(BaseModel):
    task_type:   str              # llm_analysis | system_check | file_summary
    params:      dict  = {}
    description: str   = ""


@router.post("/submit")
async def submit_task(req: TaskSubmitRequest):
    """백그라운드 태스크 등록."""
    try:
        task_id = task_manager.submit(
            task_type   = req.task_type,
            params      = req.params,
            description = req.description or req.task_type,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=429, detail=str(e))

    return {"task_id": task_id, "status": "submitted"}


@router.get("/stream")
async def task_stream():
    """백그라운드 태스크 이벤트 SSE 스트림."""
    q = task_manager.subscribe()

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
            task_manager.unsubscribe(q)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":    "no-cache",
            "X-Accel-Buffering": "no",
            "Connection":       "keep-alive",
        },
    )


@router.get("/list")
async def list_tasks():
    return {"tasks": task_manager.list_tasks()}


@router.delete("/{task_id}")
async def cancel_task(task_id: str):
    ok = task_manager.cancel(task_id)
    if not ok:
        raise HTTPException(status_code=404, detail="태스크를 찾을 수 없거나 이미 완료되었습니다.")
    return {"task_id": task_id, "cancelled": True}
