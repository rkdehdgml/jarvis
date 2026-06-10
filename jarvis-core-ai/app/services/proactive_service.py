"""
proactive_service.py — JARVIS 능동적 제안 서비스
════════════════════════════════════════════════════════════════════════════════
동작 원리:
  · asyncio 백그라운드 태스크로 주기적으로 실행
  · 현재 시간대 + 기억 컨텍스트 → LLM으로 짧은 능동 제안 생성
  · SSE 구독자(렌더러)에게 브로드캐스트
  · IDLE 상태일 때만 제안 전송 (렌더러에서 판단)

제안 카테고리:
  morning    — 오전 8-10시: 하루 시작 인사 및 일정 체크
  break      — 오후 12-13시, 오후 3-4시: 휴식 권유
  evening    — 오후 6-8시: 업무 마무리 제안
  idle_check — 30분 이상 비활동: 도움 필요 여부 확인

Public API:
  proactive.start(loop) → 백그라운드 태스크 시작
  proactive.subscribe() → asyncio.Queue 반환
  proactive.unsubscribe(q) → 구독 해제
════════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import asyncio
import threading
from datetime import datetime
from typing import Optional

from app.services.memory_service import memory

# ── 설정 ──────────────────────────────────────────────────────────────────────
CHECK_INTERVAL_SEC  = 60 * 10    # 10분마다 제안 조건 확인
MIN_SUGGESTION_GAP  = 60 * 25    # 연속 제안 최소 간격 (25분)


# ── 시간대별 제안 템플릿 (LLM 미사용 시 폴백) ──────────────────────────────────
_TEMPLATES = {
    "morning": [
        "좋은 아침입니다, Sir. 오늘 하루도 최선을 다해 보조하겠습니다. 오늘 계획이 있으시면 말씀해 주세요.",
        "아침이 밝았습니다. 오늘 집중하실 업무가 있으신가요? 도움이 필요하시면 언제든지 말씀해 주세요.",
    ],
    "break": [
        "Sir, 잠시 휴식을 취하시는 건 어떨까요? 집중력 회복에 도움이 됩니다.",
        "장시간 작업 중이신 것 같습니다. 5분 휴식을 권장드립니다.",
    ],
    "evening": [
        "오늘 하루 수고 많으셨습니다. 오늘 작업을 정리하거나 내일 일정을 확인하는 데 도움을 드릴까요?",
        "마무리할 업무가 있으시면 말씀해 주세요. 오늘의 성과를 요약해 드릴 수도 있습니다.",
    ],
    "idle_check": [
        "Sir, 조용하시군요. 필요한 것이 있으시면 언제든지 말씀해 주세요.",
        "도움이 필요하신가요? 무엇이든 도와드릴 준비가 되어 있습니다.",
    ],
}


class ProactiveService:
    """시간대·패턴 기반 능동적 제안 서비스 (싱글턴)."""

    def __init__(self) -> None:
        self._running: bool     = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._subscribers: list[asyncio.Queue] = []
        self._lock = threading.Lock()
        self._last_suggestion_time: float = 0.0
        self._suggestion_index: dict[str, int] = {}  # 카테고리별 다음 인덱스

    # ── 공개 API ──────────────────────────────────────────────────────────────

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        if self._running:
            return
        self._running = True
        self._loop    = loop
        asyncio.run_coroutine_threadsafe(self._run_loop(), loop)
        print("[Proactive] 능동적 제안 서비스 시작")

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=5)
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            try:
                self._subscribers.remove(q)
            except ValueError:
                pass

    # ── 내부 루프 ──────────────────────────────────────────────────────────────

    async def _run_loop(self) -> None:
        import time
        while self._running:
            await asyncio.sleep(CHECK_INTERVAL_SEC)
            now = time.time()

            # 최소 간격 미충족 시 건너뜀
            if now - self._last_suggestion_time < MIN_SUGGESTION_GAP:
                continue

            # 고부하 시 제안 건너뜀 (PC 퍼포먼스 보호)
            try:
                from app.services.resource_monitor import monitor as _rm
                if _rm.is_high_load:
                    continue
            except Exception:
                pass

            category = self._get_category()
            if not category:
                continue

            text = await self._generate_suggestion(category)
            if text:
                self._last_suggestion_time = now
                await self._broadcast({"event": "suggestion", "category": category, "text": text})

    def _get_category(self) -> str | None:
        """현재 시간대에 해당하는 제안 카테고리 반환."""
        hour = datetime.now().hour
        if 8 <= hour < 10:
            return "morning"
        if hour in (12, 15):
            return "break"
        if 18 <= hour < 20:
            return "evening"
        # 구독자가 있을 때만 idle_check (렌더러가 연결 중 = JARVIS 실행 중)
        if self._subscribers:
            return "idle_check"
        return None

    async def _generate_suggestion(self, category: str) -> str:
        """LLM으로 제안 생성. 실패 시 템플릿 폴백."""
        try:
            ctx = memory.get_context_prompt()
            return await self._llm_suggest(category, ctx)
        except Exception as e:
            print(f"[Proactive] LLM 제안 생성 실패, 템플릿 사용: {e}")
            return self._template_suggest(category)

    async def _llm_suggest(self, category: str, memory_ctx: str) -> str:
        from app.services.llm_manager import manager
        from app.services.agent_router import PromptSet, RoutingResult

        category_desc = {
            "morning":    "아침 인사 및 하루 시작 격려",
            "break":      "휴식 권유",
            "evening":    "업무 마무리 제안",
            "idle_check": "자연스러운 대화 시작 유도",
        }

        routing = RoutingResult(
            agent_key  = "life_coach",
            agent_name = "Life Coach",
            confidence = 1.0,
            method     = "proactive",
            reasoning  = "능동적 제안",
        )

        system = (
            "당신은 JARVIS, 사용자의 개인 AI 어시스턴트입니다. "
            "지금은 사용자가 먼저 말을 걸지 않았지만, 능동적으로 짧은 제안을 해야 합니다. "
            "반드시 한국어로, 1~2문장 이내로 자연스럽고 간결하게 작성하세요. "
            "사용자를 'Sir'로 호칭하세요. 이모지나 마크다운 없이 순수 텍스트만 사용하세요."
        )

        if memory_ctx:
            system += f"\n\n{memory_ctx}"

        user_prompt = (
            f"제안 유형: {category_desc.get(category, category)}\n"
            f"현재 시각: {datetime.now().strftime('%H:%M')}\n"
            "위 상황에 맞는 짧은 능동적 제안 메시지를 1~2문장으로 작성해 주세요."
        )

        prompt_set = PromptSet(
            system     = system,
            messages   = [{"role": "user", "content": user_prompt}],
            agent_key  = "life_coach",
            agent_name = "Life Coach",
            routing    = routing,
        )

        response = await manager.run(prompt_set, max_retries=1)
        text = response.text.strip()
        return text[:300] if text else ""

    def _template_suggest(self, category: str) -> str:
        templates = _TEMPLATES.get(category, _TEMPLATES["idle_check"])
        idx = self._suggestion_index.get(category, 0) % len(templates)
        self._suggestion_index[category] = idx + 1
        return templates[idx]

    async def _broadcast(self, payload: dict) -> None:
        with self._lock:
            subs = list(self._subscribers)
        for q in subs:
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                pass


proactive = ProactiveService()
