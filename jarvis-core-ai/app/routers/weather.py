"""
weather.py — /api/weather 라우터
"""
from fastapi import APIRouter, HTTPException, Query
from app.services.weather_service import get_weather

router = APIRouter()


@router.get("/current")
async def current_weather(city: str = Query(default="Seoul")):
    try:
        return await get_weather(city)
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))
