"""
claude_code.py — /api/claude 라우터
──────────────────────────────────────────────────────────────────────────────
설정 모달 UI가 사용하는 Claude Code 엔진 관리 엔드포인트:

  GET  /status          현재 연결 상태 (캐시된 탐지 결과)
  POST /status/refresh  강제 재탐지 (모달의 새로고침 버튼)
  GET  /usage           오늘 사용량 집계 (게이지 표시용)
  GET  /settings        사용자 설정 로드
  PUT  /settings        사용자 설정 부분 갱신 (정의 외 필드 거부)
  POST /session/reset   새 대화 시작 (세션 초기화)
"""

from fastapi import APIRouter, HTTPException
from pydantic import ValidationError

from app.services.claude_code import get_store, get_tracker, get_wrapper

router = APIRouter()


# ══════════════════════════════════════════════════════════════════════════════
# 연결 상태
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/status")
async def get_status():
    """CLI 설치/버전/로그인/인증 방식 상태 반환 (60초 캐시)."""
    return await get_wrapper().status()


@router.post("/status/refresh")
async def refresh_status():
    """캐시를 무시하고 강제 재탐지 — 모달의 새로고침 버튼용."""
    return await get_wrapper().status(force=True)


# ══════════════════════════════════════════════════════════════════════════════
# 사용량
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/usage")
async def get_usage():
    """오늘 누적 사용량 + 예산 대비 정보 (설정 모달 게이지용)."""
    s = get_store().load()
    today = get_tracker().today()
    return {
        **today,
        "hourly_limit":   s.hourly_call_limit,
        "daily_limit":    s.daily_call_limit,
        "cost_warn_usd":  s.daily_cost_warn_usd,
        "over_warn":      today["cost_usd"] >= s.daily_cost_warn_usd,
    }


# ══════════════════════════════════════════════════════════════════════════════
# 설정
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/settings")
async def get_settings():
    """사용자 설정 파일(claude_settings.json) 전체 반환."""
    return get_store().load().model_dump()


@router.put("/settings")
async def update_settings(patch: dict):
    """설정 부분 갱신. 정의되지 않은 필드(api_key 등)는 422로 거부."""
    try:
        updated = get_store().update(patch)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=e.errors())
    return updated.model_dump()


# ══════════════════════════════════════════════════════════════════════════════
# 세션
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/session/reset")
async def reset_session():
    """대화 세션 초기화 — 다음 호출부터 새 대화로 시작."""
    get_wrapper().reset_session()
    return {"ok": True}
