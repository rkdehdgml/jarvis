"""
presence_service.py — 카메라 기반 재석 감지 서비스
════════════════════════════════════════════════════════════════════════════════
동작 원리:
  · OpenCV Haar Cascade로 프레임마다 얼굴 감지
  · `ABSENT_THRESHOLD`초 동안 얼굴 미감지 → ABSENT 상태 전환 (away 이벤트)
  · ABSENT 상태에서 얼굴 감지 → PRESENT 상태 전환 (back 이벤트)
  · SSE 구독자에게 asyncio.Queue를 통해 이벤트 브로드캐스트

Public API:
  presence.start()          → 백그라운드 감지 루프 시작 (한 번만 호출)
  presence.subscribe()      → asyncio.Queue 반환 (SSE 클라이언트당 하나)
  presence.unsubscribe(q)   → 구독 해제
  presence.get_state()      → 'PRESENT' | 'ABSENT' | 'UNKNOWN'
════════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Optional

# ── 설정값 ────────────────────────────────────────────────────────────────────
ABSENT_THRESHOLD  = 60    # 얼굴 미감지 지속 시간(초) → ABSENT 판정
CHECK_INTERVAL    = 2.0   # 감지 주기(초)
CAMERA_INDEX      = 0     # 기본 카메라


class PresenceService:
    """OpenCV 얼굴 감지 기반 재석 감지 서비스 (싱글턴)."""

    def __init__(self) -> None:
        self._state: str          = "UNKNOWN"   # PRESENT | ABSENT | UNKNOWN
        self._last_seen: float    = 0.0          # 마지막 얼굴 감지 epoch
        self._running: bool       = False
        self._subscribers: list[asyncio.Queue] = []
        self._lock = threading.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    # ── 공개 API ──────────────────────────────────────────────────────────────

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        """백그라운드 감지 스레드를 시작. 이미 실행 중이면 무시."""
        if self._running:
            return
        self._loop    = loop
        self._running = True
        t = threading.Thread(target=self._detection_loop, daemon=True, name="PresenceSensor")
        t.start()
        print("[Presence] 재석 감지 서비스 시작 (카메라 인덱스:", CAMERA_INDEX, ")")

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=10)
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            try:
                self._subscribers.remove(q)
            except ValueError:
                pass

    def get_state(self) -> str:
        return self._state

    # ── 내부 로직 ─────────────────────────────────────────────────────────────

    def _detection_loop(self) -> None:
        try:
            import cv2
        except ImportError:
            print("[Presence] opencv-python 없음 — 재석 감지 비활성화")
            return

        cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        face_cascade = cv2.CascadeClassifier(cascade_path)

        cap = cv2.VideoCapture(CAMERA_INDEX)
        if not cap.isOpened():
            print("[Presence] 카메라 열기 실패 — 재석 감지 비활성화")
            return

        print("[Presence] 카메라 초기화 성공")
        self._last_seen = time.time()   # 시작 시점엔 있다고 가정

        while self._running:
            ret, frame = cap.read()
            if not ret:
                time.sleep(CHECK_INTERVAL)
                continue

            gray  = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = face_cascade.detectMultiScale(
                gray,
                scaleFactor = 1.1,
                minNeighbors = 5,
                minSize = (60, 60),
            )
            face_found = len(faces) > 0

            now = time.time()
            if face_found:
                prev_state = self._state
                self._last_seen = now
                if prev_state != "PRESENT":
                    self._state = "PRESENT"
                    if prev_state == "ABSENT":
                        # 복귀: 컨텍스트 복원 실행 후 결과 이벤트에 포함
                        restore_result = self._restore_context()
                        self._broadcast("back", extra=restore_result)
                    else:
                        self._broadcast("present")
            else:
                absent_secs = now - self._last_seen
                if absent_secs >= ABSENT_THRESHOLD and self._state != "ABSENT":
                    self._state = "ABSENT"
                    # 이탈: 현재 작업 컨텍스트 스냅샷 저장
                    self._snapshot_context()
                    self._broadcast("away")

            time.sleep(CHECK_INTERVAL)

        cap.release()
        print("[Presence] 감지 루프 종료")

    def _snapshot_context(self) -> None:
        """별도 스레드에서 context snapshot 호출 (동기)."""
        try:
            from app.services.context_service import context
            context.snapshot()
        except Exception as e:
            print(f"[Presence] 컨텍스트 스냅샷 실패: {e}")

    def _restore_context(self) -> dict:
        """context restore 호출 → 복원 결과 dict 반환."""
        try:
            from app.services.context_service import context
            return context.restore()
        except Exception as e:
            print(f"[Presence] 컨텍스트 복원 실패: {e}")
            return {}

    def _broadcast(self, event_type: str, extra: dict | None = None) -> None:
        """asyncio.Queue에 이벤트 전송 (스레드 안전)."""
        payload = {"event": event_type, "state": self._state, **(extra or {})}
        if not self._loop or not self._loop.is_running():
            return
        with self._lock:
            for q in list(self._subscribers):
                asyncio.run_coroutine_threadsafe(
                    self._safe_put(q, payload),
                    self._loop,
                )

    @staticmethod
    async def _safe_put(q: asyncio.Queue, item: dict) -> None:
        try:
            q.put_nowait(item)
        except asyncio.QueueFull:
            pass


presence = PresenceService()
