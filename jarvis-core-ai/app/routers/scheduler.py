"""
scheduler.py — /api/scheduler 라우터
──────────────────────────────────────────────────────────────────────────────
엔드포인트:
  POST   /api/scheduler/reminders          리마인더 추가
  GET    /api/scheduler/reminders          전체 목록
  GET    /api/scheduler/reminders/{id}     단건 조회
  PUT    /api/scheduler/reminders/{id}     수정
  DELETE /api/scheduler/reminders/{id}     삭제
  GET    /api/scheduler/stream             SSE — 리마인더 발화 스트림
"""

import asyncio
import json
from datetime import datetime

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, field_validator

from app.services.scheduler_service import scheduler

router = APIRouter()


# ── 요청 모델 ──────────────────────────────────────────────────────────────────

class ReminderCreate(BaseModel):
    title:       str
    due_at:      str          # ISO-8601: "2024-12-25T09:00:00"
    repeat:      str = "none" # none | daily | weekly
    description: str = ""

    @field_validator("repeat")
    @classmethod
    def check_repeat(cls, v: str) -> str:
        if v not in ("none", "daily", "weekly"):
            raise ValueError("repeat must be none | daily | weekly")
        return v

    @field_validator("due_at")
    @classmethod
    def check_due_at(cls, v: str) -> str:
        try:
            datetime.fromisoformat(v)
        except ValueError:
            raise ValueError("due_at must be ISO-8601 datetime string")
        return v


class ReminderUpdate(BaseModel):
    title:       str | None = None
    due_at:      str | None = None
    repeat:      str | None = None
    description: str | None = None


# ── CRUD ───────────────────────────────────────────────────────────────────────

@router.post("/reminders", status_code=201)
async def create_reminder(body: ReminderCreate):
    """리마인더 추가."""
    return scheduler.add(
        title       = body.title,
        due_at      = body.due_at,
        repeat      = body.repeat,
        description = body.description,
    )


@router.get("/reminders")
async def list_reminders():
    """전체 리마인더 목록 반환 (due_at 오름차순)."""
    items = scheduler.list_all()
    items.sort(key=lambda r: r.get("due_at", ""))
    return items


@router.get("/reminders/{reminder_id}")
async def get_reminder(reminder_id: str):
    r = scheduler.get(reminder_id)
    if not r:
        raise HTTPException(status_code=404, detail="리마인더를 찾을 수 없습니다.")
    return r


@router.put("/reminders/{reminder_id}")
async def update_reminder(reminder_id: str, body: ReminderUpdate):
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if not fields:
        raise HTTPException(status_code=400, detail="수정할 필드가 없습니다.")
    r = scheduler.update(reminder_id, **fields)
    if not r:
        raise HTTPException(status_code=404, detail="리마인더를 찾을 수 없습니다.")
    return r


@router.delete("/reminders/{reminder_id}")
async def delete_reminder(reminder_id: str):
    ok = scheduler.remove(reminder_id)
    if not ok:
        raise HTTPException(status_code=404, detail="리마인더를 찾을 수 없습니다.")
    return {"deleted": True, "id": reminder_id}


# ── SSE 스트림 ─────────────────────────────────────────────────────────────────

@router.get("/stream")
async def scheduler_stream():
    """리마인더 발화 이벤트 SSE 스트림."""
    q = scheduler.subscribe()

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
            scheduler.unsubscribe(q)

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
