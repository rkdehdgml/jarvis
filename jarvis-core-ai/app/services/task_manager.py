"""
task_manager.py — JARVIS 백그라운드 멀티태스킹 엔진
════════════════════════════════════════════════════════════════════════════════
역할:
  · 사용자 대화와 병렬로 장시간 작업을 비동기 실행
  · 진행 상황과 완료 결과를 SSE 구독자에게 브로드캐스트
  · 최대 5개 태스크 동시 실행 (초과 시 대기열 오류)

지원 태스크 타입:
  llm_analysis  — 텍스트를 LLM에 전달해 심층 분석 수행
  system_check  — CPU·메모리·디스크 시스템 자원 현황 수집
  file_summary  — 지정 파일 내용 읽어서 LLM으로 요약

이벤트 타입 (SSE):
  submitted  → 태스크 등록 완료
  progress   → 진행 상황 업데이트
  completed  → 정상 완료 (result 포함)
  failed     → 실행 오류 (error 포함)
  cancelled  → 사용자 취소

Public API:
  task_manager.submit(task_type, params, description) → task_id (str)
  task_manager.cancel(task_id)                        → bool
  task_manager.list_tasks()                           → list[dict]
  task_manager.subscribe()                            → asyncio.Queue
  task_manager.unsubscribe(q)
════════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

MAX_CONCURRENT = 5


@dataclass
class BgTask:
    id:          str
    task_type:   str
    description: str
    status:      str = "pending"   # pending | running | completed | failed | cancelled
    created_at:  str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    result:      Any  = None
    error:       str  = ""
    _handle:     Optional[asyncio.Task] = field(default=None, repr=False, compare=False)


class TaskManager:
    """백그라운드 태스크 실행 및 SSE 브로드캐스트 관리자."""

    def __init__(self) -> None:
        self._tasks: dict[str, BgTask] = {}
        self._subscribers: list[asyncio.Queue] = []

    # ── 공개 API ──────────────────────────────────────────────────────────────

    def submit(
        self,
        task_type: str,
        params: dict,
        description: str = "",
    ) -> str:
        """태스크를 등록하고 asyncio.Task로 즉시 실행. task_id 반환."""
        # 고부하 시 동시 실행 수를 1로 제한
        try:
            from app.services.resource_monitor import monitor as _rm
            effective_max = 1 if _rm.is_high_load else MAX_CONCURRENT
        except Exception:
            effective_max = MAX_CONCURRENT

        running = len([t for t in self._tasks.values() if t.status == "running"])
        if running >= effective_max:
            limit_msg = "고부하로 인해 1개로 제한 중" if effective_max == 1 else f"최대 {MAX_CONCURRENT}개"
            raise RuntimeError(f"최대 동시 실행 수 초과 ({limit_msg})")

        task_id = str(uuid.uuid4())[:8]
        task = BgTask(id=task_id, task_type=task_type, description=description)
        self._tasks[task_id] = task

        handle = asyncio.create_task(
            self._run(task, params),
            name=f"jarvis-bg-{task_id}",
        )
        task._handle = handle

        asyncio.create_task(
            self._broadcast({
                "event":       "submitted",
                "task_id":     task_id,
                "task_type":   task_type,
                "description": description,
            })
        )
        return task_id

    def cancel(self, task_id: str) -> bool:
        task = self._tasks.get(task_id)
        if not task or task._handle is None:
            return False
        task._handle.cancel()
        task.status = "cancelled"
        asyncio.create_task(
            self._broadcast({"event": "cancelled", "task_id": task_id})
        )
        return True

    def list_tasks(self) -> list[dict]:
        return [
            {
                "id":          t.id,
                "type":        t.task_type,
                "description": t.description,
                "status":      t.status,
                "created_at":  t.created_at,
            }
            for t in self._tasks.values()
        ]

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=20)
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        try:
            self._subscribers.remove(q)
        except ValueError:
            pass

    # ── 내부 실행 ──────────────────────────────────────────────────────────────

    async def _run(self, task: BgTask, params: dict) -> None:
        task.status = "running"
        await self._broadcast({
            "event":       "progress",
            "task_id":     task.id,
            "description": task.description,
            "message":     "백그라운드 작업 시작...",
        })

        try:
            if task.task_type == "llm_analysis":
                result = await self._run_llm_analysis(task, params)
            elif task.task_type == "system_check":
                result = await self._run_system_check(task)
            elif task.task_type == "file_summary":
                result = await self._run_file_summary(task, params)
            elif task.task_type == "debug_error":
                result = await self._run_debug_error(task, params)
            else:
                raise ValueError(f"알 수 없는 태스크 타입: {task.task_type!r}")

            task.status = "completed"
            task.result = result

            # debug_error 태스크는 구조화된 결과 그대로 전달
            if task.task_type == "debug_error" and isinstance(result, dict):
                await self._broadcast({
                    "event":       "completed",
                    "task_id":     task.id,
                    "task_type":   task.task_type,
                    "description": task.description,
                    **result,          # fix_code, explanation, references, error_type 포함
                })
            else:
                await self._broadcast({
                    "event":       "completed",
                    "task_id":     task.id,
                    "task_type":   task.task_type,
                    "description": task.description,
                    "result":      str(result)[:1000],
                })

        except asyncio.CancelledError:
            task.status = "cancelled"
        except Exception as e:
            task.status = "failed"
            task.error  = str(e)
            await self._broadcast({
                "event":       "failed",
                "task_id":     task.id,
                "description": task.description,
                "error":       str(e),
            })

    # ── 태스크 실행기 ──────────────────────────────────────────────────────────

    async def _run_llm_analysis(self, task: BgTask, params: dict) -> str:
        from app.services.llm_manager import manager
        from app.services.agent_router import PromptSet, RoutingResult

        text = params.get("text", "")
        if not text:
            raise ValueError("분석할 텍스트가 없습니다.")

        await self._broadcast({
            "event":   "progress",
            "task_id": task.id,
            "message": "LLM 심층 분석 중...",
        })

        routing = RoutingResult(
            agent_key  = "task_agent",
            agent_name = "Task Agent",
            confidence = 1.0,
            method     = "background",
            reasoning  = "백그라운드 분석 태스크",
        )
        prompt_set = PromptSet(
            system   = (
                "당신은 JARVIS의 분석 엔진입니다. "
                "제공된 내용을 심층적으로 분석하고 구조화된 보고서를 한국어로 작성하세요. "
                "핵심 인사이트, 주요 패턴, 개선 제안을 포함하세요."
            ),
            messages = [{"role": "user", "content": f"다음 내용을 분석해 주세요:\n\n{text[:3000]}"}],
            agent_key  = "task_agent",
            agent_name = "Task Agent",
            routing    = routing,
        )

        response = await manager.run(prompt_set, max_retries=1)
        return response.text

    async def _run_system_check(self, task: BgTask) -> str:
        await self._broadcast({
            "event":   "progress",
            "task_id": task.id,
            "message": "시스템 자원 수집 중...",
        })

        await asyncio.sleep(0.5)   # 짧은 비동기 대기 (실제 I/O 시뮬레이션)

        try:
            import psutil
            cpu   = psutil.cpu_percent(interval=1)
            ram   = psutil.virtual_memory()
            disk  = psutil.disk_usage("/")
            return (
                f"CPU 사용률: {cpu:.1f}%\n"
                f"메모리: {ram.used // (1024**2):,} MB / {ram.total // (1024**2):,} MB "
                f"({ram.percent:.1f}%)\n"
                f"디스크: {disk.used // (1024**3):.1f} GB / {disk.total // (1024**3):.1f} GB "
                f"({disk.percent:.1f}% 사용 중)"
            )
        except ImportError:
            import shutil, platform
            disk = shutil.disk_usage("/")
            return (
                f"OS: {platform.system()} {platform.release()}\n"
                f"디스크 (루트): {disk.used // (1024**3):.1f} GB / "
                f"{disk.total // (1024**3):.1f} GB 사용 중"
            )

    async def _run_file_summary(self, task: BgTask, params: dict) -> str:
        file_path = params.get("path", "")
        if not file_path:
            raise ValueError("파일 경로가 없습니다.")

        from pathlib import Path
        p = Path(file_path)
        if not p.exists():
            raise FileNotFoundError(f"파일을 찾을 수 없습니다: {file_path}")

        await self._broadcast({
            "event":   "progress",
            "task_id": task.id,
            "message": f"파일 읽는 중: {p.name}",
        })

        content = p.read_text(encoding="utf-8", errors="replace")[:4000]
        return await self._run_llm_analysis(
            task,
            {"text": f"파일명: {p.name}\n\n내용:\n{content}"},
        )

    async def _run_debug_error(self, task: BgTask, params: dict) -> dict:
        from app.services.debug_service import debug

        error_text = params.get("error_text", "") or params.get("text", "")
        context    = params.get("context", "")

        if not error_text:
            raise ValueError("분석할 에러 텍스트가 없습니다.")

        async def progress(msg: str):
            await self._broadcast({"event": "progress", "task_id": task.id, "message": msg})

        return await debug.analyze(error_text, context=context, broadcast_cb=progress)

    # ── 브로드캐스트 ──────────────────────────────────────────────────────────

    async def _broadcast(self, payload: dict) -> None:
        for q in list(self._subscribers):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                pass


task_manager = TaskManager()
