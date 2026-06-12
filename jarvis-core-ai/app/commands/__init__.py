"""
commands — JARVIS 내장 명령(Builtin Commands) 패키지
════════════════════════════════════════════════════════════════════════════════
chat.py의 pre-routing 단계에서 match_command(text)를 호출해 키워드 기반으로
즉시 처리 가능한 명령인지 확인한다. 매칭되면 Claude Code를 거치지 않고
바로 결과를 반환하고, 매칭되지 않으면 None을 반환해 기존 agent_router →
Claude Code 흐름으로 폴백한다.

카테고리:
  · system_control  — 볼륨/전원/앱 실행·종료/스크린샷/녹화
  · info            — 시간/IP/속도/시스템 상태/위치
  · web_media       — 유튜브/브라우저/위키피디아/뉴스/WikiHow
  · communication   — Gmail/WhatsApp
  · utility         — PDF/QR/연락처/웹캠/농담/일정/타이머/슬립모드
════════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

from app.commands.registry import CommandResult, COMMAND_TABLE, match_command, is_sleep_mode

# 각 모듈을 import해야 @register 데코레이터가 COMMAND_TABLE에 등록된다.
from app.commands import (  # noqa: F401  (등록을 위한 import)
    system_control,
    info,
    web_media,
    communication,
    utility,
)

__all__ = ["CommandResult", "COMMAND_TABLE", "match_command", "is_sleep_mode"]
