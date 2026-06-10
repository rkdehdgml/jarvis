"""
resource_monitor.py — JARVIS 시스템 자원 모니터링 + 자동 쓰로틀
════════════════════════════════════════════════════════════════════════════════
동작 원리:
  · psutil로 5초마다 CPU·RAM·디스크 수집
  · 고부하(CPU > 80% 또는 RAM > 85%) 감지 시 is_high_load = True
  · 다른 서비스(proactive, task_manager)가 이 플래그를 조회해 속도 조절
  · SSE 구독자에게 통계 브로드캐스트

이벤트 타입 (SSE):
  stats      → 수집된 자원 통계
  high_load  → 고부하 진입 (throttle 시작)
  normal     → 고부하 해제 (throttle 해제)

Public API:
  monitor.start(loop)     → 백그라운드 루프 시작
  monitor.is_high_load    → 현재 고부하 여부 (bool)
  monitor.latest_stats()  → 최근 수집 결과 dict
  monitor.subscribe()     → asyncio.Queue
  monitor.unsubscribe(q)
════════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
import threading
from typing import Optional

# Windows에서 "/" 경로 오류 방지
_DISK_PATH = (os.getenv('SystemDrive', 'C:') + '\\') if sys.platform == 'win32' else '/'

# ── 고부하 임계값 ─────────────────────────────────────────────────────────────
CPU_HIGH_THRESHOLD  = 80.0   # CPU % 초과 시 고부하
RAM_HIGH_THRESHOLD  = 85.0   # RAM % 초과 시 고부하
CHECK_INTERVAL_SEC  = 5      # 수집 주기 (초)


class ResourceMonitor:
    """시스템 자원 모니터링 서비스 (싱글턴)."""

    def __init__(self) -> None:
        self._running    = False
        self._high_load  = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._subscribers: list[asyncio.Queue] = []
        self._lock       = threading.Lock()
        self._stats: dict = {}

    # ── 공개 API ──────────────────────────────────────────────────────────────

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        if self._running:
            return
        self._running = True
        self._loop    = loop
        t = threading.Thread(
            target=self._monitor_loop, daemon=True, name="ResourceMonitor"
        )
        t.start()
        print("[ResourceMonitor] 시스템 자원 모니터링 시작")

    @property
    def is_high_load(self) -> bool:
        return self._high_load

    def latest_stats(self) -> dict:
        return dict(self._stats)

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

    # ── 내부 루프 ──────────────────────────────────────────────────────────────

    def _monitor_loop(self) -> None:
        try:
            import psutil
        except ImportError:
            print("[ResourceMonitor] psutil 없음 — 모니터링 비활성화")
            return

        while self._running:
            try:
                cpu   = psutil.cpu_percent(interval=1)
                ram   = psutil.virtual_memory()
                disk  = psutil.disk_usage(_DISK_PATH)

                # GPU 온도 (가능한 경우)
                gpu_info = self._get_gpu_info()

                stats = {
                    "cpu_percent":    round(cpu, 1),
                    "ram_percent":    round(ram.percent, 1),
                    "ram_used_mb":    ram.used  // (1024 ** 2),
                    "ram_total_mb":   ram.total // (1024 ** 2),
                    "disk_percent":   round(disk.percent, 1),
                    "disk_free_gb":   round(disk.free  / (1024 ** 3), 1),
                    "disk_total_gb":  round(disk.total / (1024 ** 3), 1),
                    "ts":             round(time.time(), 1),
                    **gpu_info,
                }
                self._stats = stats

                # 고부하 감지
                is_high = cpu > CPU_HIGH_THRESHOLD or ram.percent > RAM_HIGH_THRESHOLD
                prev_high = self._high_load

                if is_high != prev_high:
                    self._high_load = is_high
                    event_type = "high_load" if is_high else "normal"
                    self._broadcast({"event": event_type, **stats})
                    print(
                        f"[ResourceMonitor] {'⚠ 고부하 감지' if is_high else '✓ 부하 정상화'} "
                        f"CPU={cpu:.0f}% RAM={ram.percent:.0f}%"
                    )
                else:
                    self._broadcast({"event": "stats", **stats})

            except Exception as e:
                print(f"[ResourceMonitor] 수집 오류: {e}")

            time.sleep(CHECK_INTERVAL_SEC)

    def _get_gpu_info(self) -> dict:
        """GPU 온도 수집 (nvidia-smi 또는 psutil sensors 사용)."""
        try:
            import subprocess, re
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=3,
            )
            if result.returncode == 0:
                parts = [p.strip() for p in result.stdout.strip().split(",")]
                if len(parts) >= 3:
                    return {
                        "gpu_percent":   float(parts[0]),
                        "gpu_mem_used":  int(parts[1]),
                        "gpu_mem_total": int(parts[2]),
                    }
        except Exception:
            pass
        return {}

    def _broadcast(self, payload: dict) -> None:
        if not self._loop or not self._loop.is_running():
            return
        with self._lock:
            for q in list(self._subscribers):
                asyncio.run_coroutine_threadsafe(
                    self._safe_put(q, payload), self._loop
                )

    @staticmethod
    async def _safe_put(q: asyncio.Queue, item: dict) -> None:
        try:
            q.put_nowait(item)
        except asyncio.QueueFull:
            pass


monitor = ResourceMonitor()
