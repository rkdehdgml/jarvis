"""
os_control.py — /api/os 라우터
──────────────────────────────────────────────────────────────────────────────
엔드포인트:
  POST /api/os/plan      → 자연어 명령을 액션 플랜 JSON으로만 반환 (실행 없음)
  POST /api/os/run       → 계획 생성 + 실행을 NDJSON 스트림으로 반환
  POST /api/os/execute   → 이미 만들어진 ActionPlan JSON을 받아 실행
  GET  /api/os/screenshots/{filename} → 저장된 스크린샷 파일 다운로드

NDJSON 스트림 이벤트 (한 줄 = 이벤트 하나):
  planning  → LLM 계획 수립 시작
  plan      → 파싱된 전체 계획 요약
  start     → 개별 액션 실행 직전
  done      → 개별 액션 성공
  error     → 개별 액션 실패
  finish    → 전체 실행 완료 (성공/실패 집계)
"""

import asyncio
import base64
import io
import json
import time
from urllib.parse import quote

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from pathlib import Path
from typing import Annotated

from app.services.os_agent import agent, ActionPlan, OsAction, _parser

router = APIRouter()

_SCREENSHOT_DIR = Path("./data/screenshots")


def _check_permission(mode: str | None) -> None:
    """X-Jarvis-Permission 헤더가 LIMITED면 OS 제어 요청을 거부."""
    if mode and mode.upper() == "LIMITED":
        raise HTTPException(
            status_code=403,
            detail="기능 제한 모드에서는 PC 자동 제어를 사용할 수 없습니다.",
        )


# ── 요청 모델 ─────────────────────────────────────────────────────────────────

class OsCommandRequest(BaseModel):
    command: str
    stop_on_error: bool = False     # 실패 시 즉시 중단 여부


class OsPlanExecuteRequest(BaseModel):
    """이미 생성된 ActionPlan JSON을 직접 실행할 때 사용."""
    thought: str
    actions: list[dict]             # os_agent.md 스키마 그대로 전달
    stop_on_error: bool = False


# ══════════════════════════════════════════════════════════════════════════════
# POST /plan — 액션 계획 미리보기 (실행 없음)
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/plan")
async def get_action_plan(
    req: OsCommandRequest,
    x_jarvis_permission: Annotated[str | None, Header()] = None,
):
    """자연어 명령 → LLM → ActionPlan JSON 반환 (실행하지 않음)."""
    _check_permission(x_jarvis_permission)
    try:
        plan = await agent.plan(req.command)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"계획 생성 실패: {e}")

    return {
        "thought":      plan.thought,
        "action_count": len(plan.actions),
        "dangerous":    plan.dangerous,
        "risk_reason":  plan.risk_reason,
        "actions": [
            {"index": a.index, "type": a.type, "param": a.param}
            for a in plan.actions
        ],
    }


# ══════════════════════════════════════════════════════════════════════════════
# POST /run — 자연어 명령을 받아 계획 + 실행을 한번에 스트리밍
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/run")
async def run_command(
    req: OsCommandRequest,
    x_jarvis_permission: Annotated[str | None, Header()] = None,
):
    """자연어 명령 → 계획 수립 → 즉시 실행 → NDJSON 로그 스트리밍.

    각 줄은 독립된 JSON 이벤트 객체.
    클라이언트(대시보드)는 각 줄을 파싱해 실시간 진행 상황을 표시.

    Example stream:
      {"event":"planning","message":"'메모장 열어줘' 분석 중..."}
      {"event":"plan","thought":"...","action_count":5}
      {"event":"start","index":0,"action_type":"hotkey","log":"자비스가 [WIN] 단축키를 누릅니다..."}
      {"event":"done","index":0,"action_type":"hotkey","success":true,"duration_ms":52.3}
      ...
      {"event":"finish","success":5,"failed":0,"total":5}
    """
    _check_permission(x_jarvis_permission)

    async def generate():
        async for line in agent.run_stream(
            user_command  = req.command,
            stop_on_error = req.stop_on_error,
        ):
            yield line + "\n"       # NDJSON: 각 이벤트를 개행으로 구분

    return StreamingResponse(
        generate(),
        media_type = "application/x-ndjson; charset=utf-8",
        headers    = {"X-OS-Command": quote(req.command[:80])},
    )


# ══════════════════════════════════════════════════════════════════════════════
# POST /execute — 이미 만들어진 플랜을 직접 실행
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/execute")
async def execute_plan(
    req: OsPlanExecuteRequest,
    x_jarvis_permission: Annotated[str | None, Header()] = None,
):
    """미리 생성된 ActionPlan JSON을 받아 즉시 실행 후 NDJSON 스트리밍.

    대시보드에서 유저가 계획을 검토한 뒤 '실행' 버튼을 누를 때 사용.
    """
    _check_permission(x_jarvis_permission)
    # dict 리스트 → ActionPlan 재구성
    try:
        raw = {"thought": req.thought, "actions": req.actions}
        import json as _json
        plan = _parser.parse(_json.dumps(raw))
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"플랜 파싱 실패: {e}")

    async def generate():
        async for line in agent.execute_stream(
            plan          = plan,
            stop_on_error = req.stop_on_error,
        ):
            yield line + "\n"

    return StreamingResponse(
        generate(),
        media_type = "application/x-ndjson; charset=utf-8",
    )


# ══════════════════════════════════════════════════════════════════════════════
# GET /screenshots/{filename} — 스크린샷 파일 서빙
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/screenshots/{filename}")
async def get_screenshot(filename: str):
    """저장된 스크린샷 PNG 파일을 반환."""
    # 경로 탈출 방지
    safe_name = Path(filename).name
    file_path = _SCREENSHOT_DIR / safe_name

    if not file_path.exists() or not file_path.suffix.lower() == ".png":
        raise HTTPException(status_code=404, detail="스크린샷 파일을 찾을 수 없습니다.")

    return FileResponse(str(file_path), media_type="image/png")


@router.get("/screen-stream")
async def screen_stream(
    x_jarvis_permission: Annotated[str | None, Header()] = None,
    fps: int = 3,
):
    """WORKING 상태 중 현재 화면을 JPEG 프레임 SSE로 실시간 스트리밍.

    각 이벤트 형식:
      data: {"frame": "<base64-JPEG>", "ts": <unix_timestamp>, "w": <px>, "h": <px>}

    클라이언트가 연결을 끊으면 자동으로 스트리밍을 종료합니다.
    """
    _check_permission(x_jarvis_permission)

    interval = max(0.1, 1.0 / min(fps, 10))   # 최대 10 FPS

    async def generate():
        try:
            import pyautogui
            from PIL import Image

            while True:
                try:
                    img: Image.Image = pyautogui.screenshot()
                    # 16:9 비율로 리사이즈 (480×270) — 네트워크/CPU 절약
                    img = img.resize((480, 270), Image.LANCZOS)

                    buf = io.BytesIO()
                    img.save(buf, format="JPEG", quality=55, optimize=True)
                    b64 = base64.b64encode(buf.getvalue()).decode()

                    payload = json.dumps(
                        {"frame": b64, "ts": round(time.time(), 3), "w": 480, "h": 270},
                        ensure_ascii=False,
                    )
                    yield f"data: {payload}\n\n"

                except Exception as e:
                    yield f"data: {json.dumps({'error': str(e)})}\n\n"

                await asyncio.sleep(interval)

        except asyncio.CancelledError:
            pass   # 클라이언트 연결 종료 — 정상 종료

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.get("/screenshots")
async def list_screenshots():
    """저장된 스크린샷 목록 반환."""
    if not _SCREENSHOT_DIR.exists():
        return {"screenshots": []}
    files = sorted(_SCREENSHOT_DIR.glob("*.png"), reverse=True)
    return {
        "screenshots": [
            {"filename": f.name, "size_kb": round(f.stat().st_size / 1024, 1)}
            for f in files[:50]   # 최근 50개
        ]
    }
