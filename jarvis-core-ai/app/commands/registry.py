"""
registry.py — 내장 명령(Builtin Commands) 레지스트리
════════════════════════════════════════════════════════════════════════════════
설계:
  · 명령 하나 = (이름, 정규식 패턴, 비동기 핸들러)
  · 핸들러는 re.Match와 원문 텍스트를 받아 CommandResult를 반환
  · match_command()가 COMMAND_TABLE을 순서대로 검사 — 첫 매칭만 실행

규칙:
  · 새 명령은 각 기능 모듈에서 @register(...) 데코레이터로 추가
  · 매칭되지 않으면 None을 반환 → 호출부(chat.py)는 기존 Claude Code 흐름으로 폴백
════════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional


@dataclass
class CommandResult:
    """내장 명령 실행 결과 — 채팅 응답 텍스트(+TTS)로 그대로 사용 가능."""
    text: str
    speak: bool = True
    data: dict = field(default_factory=dict)


Handler = Callable[[re.Match, str], Awaitable[CommandResult]]


@dataclass
class CommandSpec:
    name: str
    pattern: re.Pattern
    handler: Handler
    description: str = ""


COMMAND_TABLE: list[CommandSpec] = []

# ── 슬립 모드 ────────────────────────────────────────────────────────────────
# True인 동안 "wake up"/"일어나"/"기상" 외 모든 명령은 무시(짧은 안내만 반환)된다.
_SLEEP_MODE = False
_WAKE_RE = re.compile(r"(?:wake\s*up|일어나|기상)", re.IGNORECASE)


def set_sleep_mode(value: bool) -> bool:
    """슬립 모드를 설정하고, 변경 전 상태를 반환한다."""
    global _SLEEP_MODE
    previous = _SLEEP_MODE
    _SLEEP_MODE = value
    return previous


def is_sleep_mode() -> bool:
    return _SLEEP_MODE


def register(name: str, pattern: str, description: str = ""):
    """COMMAND_TABLE에 명령을 등록하는 데코레이터.

    pattern은 re.IGNORECASE로 컴파일된다.
    """
    compiled = re.compile(pattern, re.IGNORECASE)

    def deco(fn: Handler) -> Handler:
        COMMAND_TABLE.append(CommandSpec(name=name, pattern=compiled, handler=fn, description=description))
        return fn

    return deco


async def match_command(text: str) -> Optional[CommandResult]:
    """텍스트를 COMMAND_TABLE에 순서대로 매칭. 매칭 없으면 None."""
    text = (text or "").strip()
    if not text:
        return None

    if _SLEEP_MODE and not _WAKE_RE.search(text):
        return CommandResult(
            text="(슬립 모드 중입니다 — 'wake up' 또는 '일어나'라고 말씀해 주세요.)",
            speak=False,
        )

    for spec in COMMAND_TABLE:
        m = spec.pattern.search(text)
        if not m:
            continue
        try:
            return await spec.handler(m, text)
        except Exception as e:
            print(f"[Commands] '{spec.name}' 실행 오류: {e}", file=sys.stderr)
            return CommandResult(text=f"'{spec.name}' 명령 실행 중 오류가 발생했습니다: {e}")

    return None
