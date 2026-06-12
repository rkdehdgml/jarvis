"""
claude_code — Claude Code 헤드리스 모드 통합 패키지
════════════════════════════════════════════════════════════════════════════════
자비스의 두뇌 엔진으로 사용자의 Claude '구독'을 통해 claude CLI를 호출한다.

★ 이 패키지의 불변 조건:
  · AI 추론은 claude CLI subprocess로만 수행 (타 AI SDK import 금지)
  · subprocess env는 화이트리스트로만 구성 (.env의 타 서비스 키 차단)
  · 한도 도달 시 유료 폴백/자동 재시도 금지

사용:
  from app.services.claude_code import get_wrapper
  wrapper = get_wrapper()
════════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

from typing import Optional

from app.services.claude_code.schema import (         # noqa: F401 (re-export)
    CCEvent, CCInit, CCResult, CCStatusEvent, CCTextDelta, CCToolResult,
    CCToolUse, CCWarning, ClaudeCodeSettings, ClaudeStatus,
)

_wrapper = None
_store = None
_tracker = None


def get_store():
    """SettingsStore 싱글턴 (지연 생성)."""
    global _store
    if _store is None:
        from app.services.claude_code.settings_store import SettingsStore
        _store = SettingsStore()
    return _store


def get_tracker():
    """UsageTracker 싱글턴 (지연 생성)."""
    global _tracker
    if _tracker is None:
        from app.services.claude_code.usage_tracker import UsageTracker
        _tracker = UsageTracker()
    return _tracker


def get_wrapper():
    """ClaudeCodeWrapper 싱글턴 (지연 생성)."""
    global _wrapper
    if _wrapper is None:
        from app.services.claude_code.wrapper import ClaudeCodeWrapper
        _wrapper = ClaudeCodeWrapper(store=get_store(), tracker=get_tracker())
    return _wrapper
