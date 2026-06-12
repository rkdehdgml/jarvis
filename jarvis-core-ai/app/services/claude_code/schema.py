"""
schema.py — Claude Code 통합 모듈의 데이터 모델
════════════════════════════════════════════════════════════════════════════════
  · ClaudeCodeSettings : 사용자 설정 파일(claude_settings.json)의 스키마
  · ClaudeStatus       : 래퍼가 자비스에 전달하는 상태 구분
  · CC* dataclass      : stream-json 출력을 변환한 타입 이벤트

주의: 이 패키지는 사용자의 Claude 구독으로만 동작해야 한다.
      다른 AI SDK(anthropic/openai 등) import 및 .env 키 접근 절대 금지.
════════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Union

from pydantic import BaseModel, ConfigDict, Field


# ══════════════════════════════════════════════════════════════════════════════
# 1. 사용자 설정 스키마 — claude_settings.json
#    (설정 모달 UI와 통합 모듈이 공유하는 단일 설정 파일)
# ══════════════════════════════════════════════════════════════════════════════

class ClaudeCodeSettings(BaseModel):
    # extra="forbid": api_key 같은 정의 외 필드 주입을 스키마 차원에서 거부
    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    model: Optional[str] = None                  # None → --model 미지정 (플랜 기본 모델)
    allowed_tools: list[str] = Field(default_factory=lambda: ["Read", "WebSearch"])
    max_turns: int = 10                          # 호출당 최대 에이전트 턴 수
    timeout_sec: float = 120.0                   # 호출당 타임아웃 (초)
    hourly_call_limit: int = 30                  # 시간당 최대 호출 횟수
    daily_call_limit: int = 200                  # 일일 최대 호출 횟수
    daily_cost_warn_usd: float = 5.0             # 일일 추정 비용 경고 임계값 (USD)
    allow_api_key_billing: bool = False          # ★ True일 때만 ANTHROPIC_API_KEY 통과 (종량 과금)
    claude_path: Optional[str] = None            # claude 바이너리 수동 지정 경로
    claude_path_wsl: Optional[str] = None        # Windows→WSL 패스스루 시 리눅스 측 절대 경로


# ══════════════════════════════════════════════════════════════════════════════
# 2. 상태 구분 — 예외가 아닌 '상태'로 자비스에 전달
# ══════════════════════════════════════════════════════════════════════════════

class ClaudeStatus(str, Enum):
    READY           = "ready"             # 정상 사용 가능
    NOT_INSTALLED   = "not_installed"     # claude CLI 미설치 — 설치 안내 필요
    NOT_LOGGED_IN   = "not_logged_in"     # 본인 Claude 계정으로 로그인 필요
    BUDGET_EXCEEDED = "budget_exceeded"   # 자비스 자체 호출 예산 초과
    LIMIT_REACHED   = "limit_reached"     # 구독 사용량 한도 도달 — 리셋까지 대기
    TIMEOUT         = "timeout"           # 호출 타임아웃으로 강제 종료
    ERROR           = "error"             # 그 외 오류


# ══════════════════════════════════════════════════════════════════════════════
# 3. 스트림 이벤트 — stream-json 라인을 타입으로 변환한 결과
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class CCInit:
    """system/init 이벤트 — 세션 시작 정보."""
    session_id: str
    model: str
    api_key_source: str          # "none"이면 구독(OAuth) 인증 — 과금 감시에 사용
    tools: list[str] = field(default_factory=list)


@dataclass
class CCTextDelta:
    """실시간 텍스트 조각 (stream_event/content_block_delta/text_delta)."""
    text: str


@dataclass
class CCToolUse:
    """assistant 메시지 내 tool_use 블록 — UI 상태 표시용."""
    name: str
    input_preview: str = ""


@dataclass
class CCToolResult:
    """user 메시지 내 tool_result 블록."""
    tool_use_id: str = ""
    is_error: bool = False


@dataclass
class CCResult:
    """result 이벤트 — 호출 1회의 최종 결과 + 사용량."""
    subtype: str
    is_error: bool
    result_text: str
    total_cost_usd: float
    num_turns: int
    duration_ms: int
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    session_id: str


@dataclass
class CCStatusEvent:
    """비정상/대기 상태 알림 (미설치·미로그인·예산초과·한도도달·타임아웃·오류)."""
    status: ClaudeStatus
    message: str
    reset_at: Optional[int] = None        # 한도 도달 시 리셋 시각 (epoch 초)


@dataclass
class CCWarning:
    """경고 이벤트 (예: 일일 추정 비용 임계값 초과)."""
    message: str


CCEvent = Union[CCInit, CCTextDelta, CCToolUse, CCToolResult,
                CCResult, CCStatusEvent, CCWarning]
