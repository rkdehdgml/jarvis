from fastapi import APIRouter
from app.config import settings

router = APIRouter()


@router.get("")
async def health():
    return {
        "status": "ok",
        "service": settings.app_name,
        "provider": settings.ai_provider,
    }
