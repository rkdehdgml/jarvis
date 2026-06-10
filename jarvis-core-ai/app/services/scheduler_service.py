"""
scheduler_service.py — JARVIS 스마트 스케줄러 / 리마인더 서비스
════════════════════════════════════════════════════════════════════════════════
동작 원리:
  · 리마인더를 ./data/reminders.json 에 영구 저장
  · 백그라운드 스레드가 30초마다 마감 도래 여부 확인
  · 마감 ±1분 이내 → SSE 구독자에게 이벤트 브로드캐스트
  · 반복(daily/weekly) 리마인더는 발화 후 다음 due_at 자동 갱신

각 리마인더 필드:
  id          str   UUID
  title       str   알림 제목
  due_at      str   ISO-8601 (예: "2024-12-25T09:00:00")
  repeat      str   "none" | "daily" | "weekly"
  description str   상세 메모 (선택)
  fired       bool  이번 due_at에 이미 발화했는지

Public API:
  scheduler.add(title, due_at, repeat, description) → dict
  scheduler.remove(reminder_id)                     → bool
  scheduler.list_all()                              → list[dict]
  scheduler.update(reminder_id, **fields)           → dict | None
  scheduler.start(loop)
  scheduler.subscribe()  → asyncio.Queue
  scheduler.unsubscribe(q)
════════════════════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

_DATA_FILE = Path("./data/reminders.json")
_CHECK_INTERVAL = 30          # 초
_FIRE_WINDOW    = 60          # ±이 초 이내면 발화


class SchedulerService:
    """리마인더 스케줄러 싱글턴."""

    def __init__(self) -> None:
        _DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
        self._reminders: list[dict] = self._load()
        self._lock      = threading.Lock()
        self._running   = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._subscribers: list[asyncio.Queue] = []

    # ── 공개 API ──────────────────────────────────────────────────────────────

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        if self._running:
            return
        self._loop    = loop
        self._running = True
        t = threading.Thread(target=self._check_loop, daemon=True, name="SchedulerService")
        t.start()
        print("[Scheduler] 스케줄러 서비스 시작")

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=20)
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            try:
                self._subscribers.remove(q)
            except ValueError:
                pass

    def add(
        self,
        title:       str,
        due_at:      str,        # ISO-8601
        repeat:      str = "none",
        description: str = "",
    ) -> dict:
        reminder = {
            "id":          str(uuid.uuid4()),
            "title":       title.strip(),
            "due_at":      due_at,
            "repeat":      repeat,   # none | daily | weekly
            "description": description.strip(),
            "fired":       False,
            "created_at":  datetime.now().isoformat(timespec="seconds"),
        }
        with self._lock:
            self._reminders.append(reminder)
            self._save()
        print(f"[Scheduler] 리마인더 추가: {title} @ {due_at}")
        return reminder

    def remove(self, reminder_id: str) -> bool:
        with self._lock:
            before = len(self._reminders)
            self._reminders = [r for r in self._reminders if r["id"] != reminder_id]
            if len(self._reminders) < before:
                self._save()
                return True
        return False

    def update(self, reminder_id: str, **fields) -> dict | None:
        allowed = {"title", "due_at", "repeat", "description", "fired"}
        with self._lock:
            for r in self._reminders:
                if r["id"] == reminder_id:
                    for k, v in fields.items():
                        if k in allowed:
                            r[k] = v
                    self._save()
                    return dict(r)
        return None

    def list_all(self) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._reminders]

    def get(self, reminder_id: str) -> dict | None:
        with self._lock:
            for r in self._reminders:
                if r["id"] == reminder_id:
                    return dict(r)
        return None

    # ── 내부 루프 ─────────────────────────────────────────────────────────────

    def _check_loop(self) -> None:
        while self._running:
            try:
                self._check_due()
            except Exception as e:
                print(f"[Scheduler] 점검 오류: {e}")
            time.sleep(_CHECK_INTERVAL)

    def _check_due(self) -> None:
        now = datetime.now()
        with self._lock:
            for r in self._reminders:
                if r.get("fired"):
                    continue
                try:
                    due = datetime.fromisoformat(r["due_at"])
                except ValueError:
                    continue
                diff = (due - now).total_seconds()
                if -_FIRE_WINDOW <= diff <= _FIRE_WINDOW:
                    r["fired"] = True
                    self._save()
                    self._broadcast_reminder(dict(r))
                    self._schedule_next(r, due)

    def _schedule_next(self, r: dict, fired_due: datetime) -> None:
        """반복 리마인더의 다음 due_at 갱신."""
        repeat = r.get("repeat", "none")
        if repeat == "daily":
            next_due = fired_due + timedelta(days=1)
        elif repeat == "weekly":
            next_due = fired_due + timedelta(weeks=1)
        else:
            return
        r["due_at"] = next_due.isoformat(timespec="seconds")
        r["fired"]  = False
        self._save()

    def _broadcast_reminder(self, r: dict) -> None:
        payload = {
            "event":       "reminder",
            "id":          r["id"],
            "title":       r["title"],
            "description": r.get("description", ""),
            "due_at":      r["due_at"],
            "repeat":      r.get("repeat", "none"),
        }
        if not self._loop or not self._loop.is_running():
            return
        with self._lock:
            for q in list(self._subscribers):
                asyncio.run_coroutine_threadsafe(
                    self._safe_put(q, payload), self._loop
                )
        print(f"[Scheduler] 리마인더 발화: {r['title']}")

    @staticmethod
    async def _safe_put(q: asyncio.Queue, item: dict) -> None:
        try:
            q.put_nowait(item)
        except asyncio.QueueFull:
            pass

    # ── 영구 저장 ─────────────────────────────────────────────────────────────

    def _load(self) -> list[dict]:
        try:
            return json.loads(_DATA_FILE.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    def _save(self) -> None:
        _DATA_FILE.write_text(
            json.dumps(self._reminders, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


scheduler = SchedulerService()
