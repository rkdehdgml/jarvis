"""
workspace_service.py — JARVIS 워크스페이스 스위칭 서비스
════════════════════════════════════════════════════════════════════════════════
동작 원리:
  · JSON 파일에 저장된 워크스페이스 프리셋 로드
  · switch(name) 호출 시 정의된 액션을 순차 실행:
      open_url  → 브라우저에서 URL 오픈
      open_app  → subprocess로 애플리케이션 실행
      hotkey    → pyautogui 단축키
      wait      → 지연 대기
  · 현재 활성 워크스페이스 상태 관리

기본 프리셋:
  dev      — 개발 모드: VSCode + GitHub + localhost
  docs     — 문서 모드: Notepad + Google Docs
  focus    — 집중 모드: 브라우저 최소화 + 타이머
  meeting  — 회의 모드: 화상회의 + 캘린더

프리셋 저장 위치: ./data/workspaces.json

Public API:
  ws.switch(name)          → dict (실행된 액션 수 등)
  ws.list_workspaces()     → list[dict]
  ws.save(name, actions, description) → dict
  ws.delete(name)          → bool
  ws.current              → str | None
════════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import json
import subprocess
import time
import webbrowser
from pathlib import Path
from typing import Any

_WS_FILE = Path("./data/workspaces.json")

# ── 기본 프리셋 ───────────────────────────────────────────────────────────────
_DEFAULT_PRESETS: dict[str, dict] = {
    "dev": {
        "name":        "개발 모드",
        "description": "코딩 작업 환경 — IDE, GitHub, 로컬서버",
        "tts_message": "개발 모드로 전환합니다, Sir.",
        "actions": [
            {"type": "open_app", "param": "code",                "label": "VS Code 실행"},
            {"type": "wait",     "param": 2.0,                   "label": "앱 로딩 대기"},
            {"type": "open_url", "param": "https://github.com",  "label": "GitHub 오픈"},
            {"type": "open_url", "param": "http://localhost:3000","label": "로컬 서버 오픈"},
        ],
    },
    "docs": {
        "name":        "문서 모드",
        "description": "문서 작성 환경 — 메모장, Google Docs",
        "tts_message": "문서 작업 모드로 전환합니다, Sir.",
        "actions": [
            {"type": "open_url", "param": "https://docs.google.com/document/u/0/",
             "label": "Google Docs 오픈"},
            {"type": "open_app", "param": "notepad", "label": "메모장 실행"},
        ],
    },
    "focus": {
        "name":        "집중 모드",
        "description": "방해 최소화 — 알림 차단, Pomodoro 타이머",
        "tts_message": "집중 모드를 시작합니다. 방해 요소를 최소화합니다, Sir.",
        "actions": [
            {"type": "open_url", "param": "https://pomofocus.io", "label": "Pomodoro 타이머 오픈"},
            {"type": "hotkey",   "param": ["win", "d"],           "label": "모든 창 최소화"},
        ],
    },
    "meeting": {
        "name":        "회의 모드",
        "description": "화상회의 환경 — 캘린더, 화상회의 앱",
        "tts_message": "회의 모드로 전환합니다, Sir.",
        "actions": [
            {"type": "open_url", "param": "https://calendar.google.com", "label": "Google 캘린더"},
            {"type": "open_url", "param": "https://meet.google.com",      "label": "Google Meet"},
        ],
    },
}


class WorkspaceService:
    """워크스페이스 프리셋 관리 및 전환 서비스."""

    def __init__(self) -> None:
        _WS_FILE.parent.mkdir(parents=True, exist_ok=True)
        self._presets: dict[str, dict] = self._load()
        self._current: str | None = None

    # ── 공개 API ──────────────────────────────────────────────────────────────

    @property
    def current(self) -> str | None:
        return self._current

    def list_workspaces(self) -> list[dict]:
        return [
            {
                "key":         k,
                "name":        v.get("name", k),
                "description": v.get("description", ""),
                "action_count": len(v.get("actions", [])),
                "is_active":   k == self._current,
            }
            for k, v in self._presets.items()
        ]

    def get(self, name: str) -> dict | None:
        return self._presets.get(name)

    def switch(self, name: str) -> dict:
        """워크스페이스 전환 실행. 완료된 액션 수 반환."""
        preset = self._presets.get(name)
        if not preset:
            raise ValueError(f"워크스페이스 '{name}'을 찾을 수 없습니다.")

        actions = preset.get("actions", [])
        done    = 0
        errors  = []

        for action in actions:
            try:
                self._execute_action(action)
                done += 1
            except Exception as e:
                errors.append(f"{action.get('label', action['type'])}: {e}")

        self._current = name
        return {
            "workspace":   name,
            "name":        preset.get("name", name),
            "tts_message": preset.get("tts_message", f"{preset.get('name', name)}으로 전환했습니다."),
            "total":       len(actions),
            "done":        done,
            "errors":      errors,
        }

    def save(self, key: str, name: str, description: str, actions: list[dict]) -> dict:
        """새 프리셋 저장 (기존 키가 있으면 덮어씀)."""
        self._presets[key] = {
            "name":        name,
            "description": description,
            "tts_message": f"{name}으로 전환합니다, Sir.",
            "actions":     actions,
        }
        self._persist()
        return {"key": key, "saved": True}

    def delete(self, key: str) -> bool:
        if key not in self._presets:
            return False
        del self._presets[key]
        self._persist()
        return True

    # ── 액션 실행 ──────────────────────────────────────────────────────────────

    def _execute_action(self, action: dict) -> None:
        t = action.get("type", "")
        p = action.get("param")

        if t == "open_url":
            webbrowser.open_new_tab(str(p))

        elif t == "open_app":
            # Windows: start 명령으로 앱 이름 실행
            subprocess.Popen(
                ["start", "", str(p)],
                shell=True,
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
            )

        elif t == "hotkey":
            import pyautogui
            keys = p if isinstance(p, list) else [str(p)]
            pyautogui.hotkey(*[str(k) for k in keys])

        elif t == "wait":
            time.sleep(float(p))

        elif t == "write":
            import pyautogui
            pyautogui.write(str(p), interval=0.05)

        elif t == "press":
            import pyautogui
            pyautogui.press(str(p))

        else:
            raise ValueError(f"지원하지 않는 액션 타입: {t!r}")

    # ── 영속화 ──────────────────────────────────────────────────────────────────

    def _load(self) -> dict:
        try:
            data = json.loads(_WS_FILE.read_text(encoding="utf-8"))
            # 기본 프리셋과 병합 (기본값은 사용자 설정으로 덮어씀)
            merged = dict(_DEFAULT_PRESETS)
            merged.update(data)
            return merged
        except (FileNotFoundError, json.JSONDecodeError):
            return dict(_DEFAULT_PRESETS)

    def _persist(self) -> None:
        _WS_FILE.write_text(
            json.dumps(self._presets, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


workspace = WorkspaceService()
