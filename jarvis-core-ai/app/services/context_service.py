"""
context_service.py — JARVIS 작업 컨텍스트 스냅샷 / 복원 서비스
════════════════════════════════════════════════════════════════════════════════
동작 원리:
  · AWAY 이벤트 발생 시 snapshot() 호출 → 현재 상태를 JSON에 저장
    - 활성 워크스페이스 키
    - 실행 중인 유관 앱 목록 (psutil)
    - 스냅샷 시각
    - 최근 기억 요약 (memory_service)
  · BACK 이벤트 발생 시 restore() 호출
    - 저장된 컨텍스트 로드
    - 워크스페이스가 있었으면 재전환
    - 복귀 인사 메시지 생성

저장 위치: ./data/context.json

Public API:
  ctx.snapshot()          → dict (저장된 컨텍스트)
  ctx.restore()           → dict (복원 결과 + 인사 메시지)
  ctx.get_last()          → dict | None (최근 스냅샷)
════════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

_CTX_FILE = Path("./data/context.json")

# 추적 대상 앱 이름 (소문자)
_TRACKED_APPS: set[str] = {
    "code.exe", "code",                   # VS Code
    "notepad.exe", "notepad",             # 메모장
    "notepad++.exe",                      # Notepad++
    "pycharm64.exe", "pycharm",           # PyCharm
    "idea64.exe", "idea",                 # IntelliJ
    "webstorm64.exe",                     # WebStorm
    "chrome.exe", "chrome",               # Chrome
    "msedge.exe", "msedge",              # Edge
    "firefox.exe", "firefox",             # Firefox
    "slack.exe", "slack",                 # Slack
    "teams.exe", "teams",                 # Teams
    "discord.exe", "discord",             # Discord
    "explorer.exe",                       # 파일 탐색기
    "excel.exe", "excel",                 # Excel
    "word.exe", "winword",               # Word
    "powerpnt.exe",                       # PowerPoint
    "terminal.exe", "wt.exe",             # Windows Terminal
    "cmd.exe",                            # 명령 프롬프트
    "python.exe", "python",               # Python
    "node.exe", "node",                   # Node.js
}


class ContextService:
    """작업 컨텍스트 스냅샷 및 복원 서비스."""

    def __init__(self) -> None:
        _CTX_FILE.parent.mkdir(parents=True, exist_ok=True)
        self._last: dict | None = None

    # ── 공개 API ──────────────────────────────────────────────────────────────

    def snapshot(self) -> dict:
        """현재 작업 상태를 저장."""
        from app.services.workspace_service import workspace
        from app.services.memory_service    import memory

        running_apps = self._get_running_apps()
        mem_summary  = memory.get_summary()
        recent       = memory.get_recent(1)
        last_topic   = recent[0]["user"][:80] if recent else ""

        ctx = {
            "ts":             datetime.now().isoformat(timespec="seconds"),
            "workspace_key":  workspace.current,
            "workspace_name": (workspace.get(workspace.current) or {}).get("name") if workspace.current else None,
            "running_apps":   running_apps,
            "last_topic":     last_topic,
            "mem_total":      mem_summary.get("total", 0),
        }

        self._last = ctx
        _CTX_FILE.write_text(
            json.dumps(ctx, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"[Context] 스냅샷 저장 — 앱: {running_apps}, 워크스페이스: {ctx['workspace_key']}")
        return ctx

    def restore(self) -> dict:
        """저장된 컨텍스트 기반 복원 수행 + 복귀 인사 생성."""
        ctx = self._load()
        if not ctx:
            return {"restored": False, "message": "Welcome back, Sir.", "workspace_restored": False}

        away_since   = ctx.get("ts", "")
        ws_key       = ctx.get("workspace_key")
        ws_name      = ctx.get("workspace_name") or ws_key
        running_apps = ctx.get("running_apps", [])
        last_topic   = ctx.get("last_topic", "")

        # 워크스페이스 복원
        ws_restored = False
        if ws_key:
            try:
                from app.services.workspace_service import workspace
                workspace.switch(ws_key)
                ws_restored = True
                print(f"[Context] 워크스페이스 복원: {ws_key}")
            except Exception as e:
                print(f"[Context] 워크스페이스 복원 실패: {e}")

        # 복귀 인사 메시지 구성
        parts: list[str] = ["Welcome back, Sir."]
        if ws_name and ws_restored:
            parts.append(f"{ws_name} 환경을 복원했습니다.")
        if last_topic:
            parts.append(f"자리를 비우시기 전 '{last_topic[:40]}' 관련 작업을 하고 계셨습니다.")
        if running_apps:
            app_list = ", ".join(running_apps[:3])
            parts.append(f"실행 중이던 앱: {app_list}.")

        message = " ".join(parts)
        return {
            "restored":          True,
            "message":           message,
            "workspace_key":     ws_key,
            "workspace_name":    ws_name,
            "workspace_restored": ws_restored,
            "running_apps":      running_apps,
            "last_topic":        last_topic,
            "away_since":        away_since,
        }

    def get_last(self) -> dict | None:
        return self._last or self._load()

    # ── 내부 ──────────────────────────────────────────────────────────────────

    def _get_running_apps(self) -> list[str]:
        """psutil로 실행 중인 유관 앱 이름 목록 반환."""
        try:
            import psutil
            found: list[str] = []
            seen:  set[str]  = set()
            for proc in psutil.process_iter(["name"]):
                name = (proc.info.get("name") or "").lower()
                if name in _TRACKED_APPS and name not in seen:
                    seen.add(name)
                    # 사람이 읽기 쉬운 이름으로 변환
                    display = name.replace(".exe", "").replace("64", "").title()
                    found.append(display)
            return found[:6]
        except Exception:
            return []

    def _load(self) -> dict | None:
        try:
            return json.loads(_CTX_FILE.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return None


context = ContextService()
