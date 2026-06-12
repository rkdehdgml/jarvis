"""
settings_store.py — claude_settings.json 읽기/쓰기
════════════════════════════════════════════════════════════════════════════════
설정 모달 UI(REST 경유)와 통합 모듈이 공유하는 단일 진실 공급원.
파일이 없거나 손상되면 안전한 기본값으로 동작한다.
════════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from pydantic import ValidationError

from app.services.claude_code import paths
from app.services.claude_code.schema import ClaudeCodeSettings


class SettingsStore:
    """claude_settings.json의 로드/저장/부분 갱신."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path or paths.settings_file_path()

    # ── 로드 ──────────────────────────────────────────────────────────────────

    def load(self) -> ClaudeCodeSettings:
        """파일에서 설정 로드. 없거나 손상 시 기본값 반환."""
        p = self.path
        if not p.exists():
            return ClaudeCodeSettings()
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return ClaudeCodeSettings.model_validate(data)
        except (json.JSONDecodeError, ValidationError, OSError) as e:
            print(f"[ClaudeCode] 설정 파일 손상 — 기본값 사용: {e}")
            return ClaudeCodeSettings()

    # ── 저장 ──────────────────────────────────────────────────────────────────

    def save(self, settings: ClaudeCodeSettings) -> None:
        p = self.path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(settings.model_dump(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # ── 부분 갱신 ─────────────────────────────────────────────────────────────

    def update(self, patch: dict) -> ClaudeCodeSettings:
        """부분 패치를 검증 후 병합·저장. 알 수 없는 필드는 ValidationError.

        Raises:
            pydantic.ValidationError: 정의되지 않은 필드 또는 타입 불일치
        """
        current = self.load()
        merged = ClaudeCodeSettings.model_validate(
            {**current.model_dump(), **patch}
        )
        self.save(merged)
        if merged.allow_api_key_billing and not current.allow_api_key_billing:
            print(
                "[ClaudeCode 경고] allow_api_key_billing이 활성화되었습니다. "
                "이후 호출은 구독 대신 API 종량 요금이 청구될 수 있습니다."
            )
        return merged
