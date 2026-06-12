"""
usage_tracker.py — 호출 예산 게이트 + 사용량 로그/집계
════════════════════════════════════════════════════════════════════════════════
  · 호출 시도("call")와 완료 결과("result")를 claude_usage.jsonl에 기록
  · 시간당/일일 호출 예산 검사 (예산 게이트는 spawn 이전에 평가)
  · 오늘 누적 사용량(호출 수·토큰·추정 비용) 집계 — 설정 모달 게이지에 사용
  · 일일 추정 비용이 임계값을 '넘는 순간' 감지

로그 라인 형식:
  {"ts": "...", "event": "call"}
  {"ts": "...", "event": "result", "cost": 0.012, "in": 3571, "out": 727,
   "cache_r": 6656, "cache_c": 0, "turns": 1, "ms": 7600, "session": "..."}
════════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional


class UsageTracker:
    """claude_usage.jsonl 기반 사용량 추적기 (스레드/태스크 안전)."""

    def __init__(self, log_path: Optional[Path] = None) -> None:
        self._explicit_path = log_path
        self._lock = threading.Lock()
        self._call_times: list[datetime] = []     # 최근 호출 시도 시각 (예산 계산용)
        self._seeded = False

    @property
    def path(self) -> Path:
        if self._explicit_path is not None:
            return self._explicit_path
        from app.services.claude_code import paths
        return paths.usage_log_path()

    # ── 내부: 재시작 후 복원 ──────────────────────────────────────────────────

    def _ensure_seeded(self) -> None:
        """프로세스 재시작 시 최근 24시간의 호출 기록을 파일에서 복원."""
        if self._seeded:
            return
        self._seeded = True
        cutoff = datetime.now() - timedelta(hours=24)
        for entry in self._iter_entries():
            if entry.get("event") == "call":
                ts = _parse_ts(entry.get("ts"))
                if ts and ts >= cutoff:
                    self._call_times.append(ts)

    def _iter_entries(self):
        p = self.path
        if not p.exists():
            return
        try:
            with p.open(encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        continue
        except OSError:
            return

    def _append(self, entry: dict) -> None:
        p = self.path
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # ── 기록 ──────────────────────────────────────────────────────────────────

    def record_call(self) -> None:
        """호출 시도 1건 기록 (spawn 직전에 호출)."""
        with self._lock:
            self._ensure_seeded()
            now = datetime.now()
            self._call_times.append(now)
            self._append({"ts": now.isoformat(timespec="seconds"), "event": "call"})

    def record_result(
        self,
        *,
        cost_usd: float,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int = 0,
        cache_creation_tokens: int = 0,
        num_turns: int = 0,
        duration_ms: int = 0,
        session_id: str = "",
    ) -> float:
        """완료 결과 기록. 반환값은 기록 후 오늘 누적 추정 비용(USD)."""
        with self._lock:
            self._ensure_seeded()
            self._append({
                "ts":      datetime.now().isoformat(timespec="seconds"),
                "event":   "result",
                "cost":    round(cost_usd, 6),
                "in":      input_tokens,
                "out":     output_tokens,
                "cache_r": cache_read_tokens,
                "cache_c": cache_creation_tokens,
                "turns":   num_turns,
                "ms":      duration_ms,
                "session": session_id,
            })
        return self.today()["cost_usd"]

    # ── 예산 게이트 ───────────────────────────────────────────────────────────

    def check_budget(self, hourly_limit: int, daily_limit: int) -> tuple[bool, str]:
        """(허용 여부, 사유 메시지) 반환. 거부 시 spawn 자체가 일어나지 않아야 한다."""
        with self._lock:
            self._ensure_seeded()
            now = datetime.now()
            hour_ago = now - timedelta(hours=1)
            midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)

            # 오래된 항목 정리 (24시간 이전)
            cutoff = now - timedelta(hours=24)
            self._call_times = [t for t in self._call_times if t >= cutoff]

            hourly = sum(1 for t in self._call_times if t >= hour_ago)
            daily = sum(1 for t in self._call_times if t >= midnight)

        if hourly >= hourly_limit:
            return False, f"시간당 호출 예산({hourly_limit}회)을 초과했습니다."
        if daily >= daily_limit:
            return False, f"일일 호출 예산({daily_limit}회)을 초과했습니다."
        return True, ""

    # ── 일별 집계 — 설정 모달 '오늘 사용량' 표시용 ────────────────────────────

    def today(self) -> dict:
        midnight = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        hour_ago = datetime.now() - timedelta(hours=1)

        calls = hourly_calls = 0
        cost = 0.0
        in_tok = out_tok = 0

        for entry in self._iter_entries():
            ts = _parse_ts(entry.get("ts"))
            if ts is None or ts < midnight:
                # 한 시간 경계는 자정 이전일 수 있으나, 오늘 집계 기준으로 충분
                continue
            if entry.get("event") == "call":
                calls += 1
                if ts >= hour_ago:
                    hourly_calls += 1
            elif entry.get("event") == "result":
                cost += float(entry.get("cost", 0.0) or 0.0)
                in_tok += int(entry.get("in", 0) or 0)
                out_tok += int(entry.get("out", 0) or 0)

        return {
            "calls":         calls,
            "hourly_calls":  hourly_calls,
            "cost_usd":      round(cost, 6),
            "input_tokens":  in_tok,
            "output_tokens": out_tok,
        }

    def crossed_warn_threshold(self, before_cost: float, after_cost: float,
                               threshold: float) -> bool:
        """이번 호출로 일일 비용이 임계값을 '처음' 넘었는지 판정."""
        return before_cost < threshold <= after_cost


def _parse_ts(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None
