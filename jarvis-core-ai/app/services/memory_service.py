"""
memory_service.py — JARVIS 장기 기억 서비스
════════════════════════════════════════════════════════════════════════════════
역할:
  · 대화 이력을 로컬 JSON 파일에 영속 저장
  · 최근 패턴을 요약해 LLM 시스템 프롬프트에 주입
  · 가장 자주 사용한 에이전트, 주요 관심사 등을 추적

저장 위치: ./data/memory.json (앱 실행 디렉토리 기준)

Public API:
  memory.add(user_msg, assistant_msg, agent_key) → 이력 추가 (비동기)
  memory.get_context_prompt()                    → 시스템 프롬프트 주입용 문자열
  memory.get_recent(n)                           → 최근 N개 이력
  memory.get_summary()                           → 패턴 요약 dict
════════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import json
import threading
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

_MEMORY_FILE = Path("./data/memory.json")
_MAX_ENTRIES = 100   # 최대 보관 대화 수


class MemoryService:
    """대화 이력 영속 저장 및 컨텍스트 제공."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        _MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        self._data: dict[str, Any] = self._load()

    # ── 공개 API ──────────────────────────────────────────────────────────────

    def add(self, user_msg: str, assistant_msg: str, agent_key: str = "unknown") -> None:
        """대화 한 쌍을 기억에 저장."""
        entry = {
            "ts":        datetime.now().isoformat(timespec="seconds"),
            "user":      user_msg[:500],      # 길이 제한
            "assistant": assistant_msg[:800],
            "agent":     agent_key,
        }
        with self._lock:
            self._data.setdefault("interactions", []).append(entry)
            # 최대 항목 초과 시 오래된 것부터 제거
            if len(self._data["interactions"]) > _MAX_ENTRIES:
                self._data["interactions"] = self._data["interactions"][-_MAX_ENTRIES:]
            self._save()

    def get_recent(self, n: int = 5) -> list[dict]:
        """최근 N개 대화 이력 반환."""
        with self._lock:
            return list(self._data.get("interactions", [])[-n:])

    def get_summary(self) -> dict:
        """전체 이력에서 집계된 패턴 요약 반환."""
        with self._lock:
            interactions = self._data.get("interactions", [])

        if not interactions:
            return {"total": 0, "top_agent": None, "topics": []}

        agent_counts = Counter(i["agent"] for i in interactions)
        top_agent = agent_counts.most_common(1)[0][0] if agent_counts else None

        return {
            "total":     len(interactions),
            "top_agent": top_agent,
            "agent_dist": dict(agent_counts.most_common(5)),
        }

    def get_context_prompt(self) -> str:
        """LLM 시스템 프롬프트에 삽입할 최근 기억 문자열 반환.

        포맷:
          [MEMORY CONTEXT]
          - Recent topics: ...
          - Most used mode: ...
          - Last 3 conversations:
            ...
        """
        recent = self.get_recent(3)
        summary = self.get_summary()

        if not recent:
            return ""

        lines = ["[MEMORY CONTEXT — JARVIS 장기 기억]"]
        lines.append(f"· 총 {summary['total']}회 대화 기록됨")
        if summary["top_agent"]:
            lines.append(f"· 가장 자주 사용한 모드: {summary['top_agent'].replace('_', ' ').title()}")

        lines.append("· 최근 대화 요약:")
        for entry in recent:
            ts    = entry["ts"][:10]
            user  = entry["user"][:80].replace("\n", " ")
            agent = entry["agent"]
            lines.append(f"  [{ts}·{agent}] 사용자: \"{user}\"")

        return "\n".join(lines)

    # ── 내부 ──────────────────────────────────────────────────────────────────

    def _load(self) -> dict:
        try:
            return json.loads(_MEMORY_FILE.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {"interactions": []}

    def _save(self) -> None:
        _MEMORY_FILE.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


memory = MemoryService()
