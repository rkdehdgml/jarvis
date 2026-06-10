"""
workspace.py — /api/workspace 라우터
──────────────────────────────────────────────────────────────────────────────
엔드포인트:
  GET    /api/workspace/list          → 전체 프리셋 목록
  GET    /api/workspace/{name}        → 특정 프리셋 상세
  POST   /api/workspace/switch        → 워크스페이스 전환 실행
  POST   /api/workspace/save          → 프리셋 저장
  DELETE /api/workspace/{name}        → 프리셋 삭제
  GET    /api/workspace/current       → 현재 활성 워크스페이스
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services.workspace_service import workspace

router = APIRouter()


class SwitchRequest(BaseModel):
    name: str   # 워크스페이스 키 (예: "dev", "docs")


class SaveRequest(BaseModel):
    key:         str
    name:        str
    description: str        = ""
    actions:     list[dict] = []


@router.get("/list")
async def list_workspaces():
    return {"workspaces": workspace.list_workspaces()}


@router.get("/current")
async def current_workspace():
    return {"current": workspace.current}


@router.get("/{name}")
async def get_workspace(name: str):
    preset = workspace.get(name)
    if not preset:
        raise HTTPException(status_code=404, detail=f"워크스페이스 '{name}'을 찾을 수 없습니다.")
    return {"key": name, **preset}


@router.post("/switch")
async def switch_workspace(req: SwitchRequest):
    """워크스페이스 전환 실행 (동기, 완료까지 대기)."""
    try:
        result = workspace.switch(req.name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"전환 실패: {e}")
    return result


@router.post("/save")
async def save_workspace(req: SaveRequest):
    return workspace.save(req.key, req.name, req.description, req.actions)


@router.delete("/{name}")
async def delete_workspace(name: str):
    ok = workspace.delete(name)
    if not ok:
        raise HTTPException(status_code=404, detail=f"워크스페이스 '{name}'을 찾을 수 없습니다.")
    return {"deleted": True, "key": name}
