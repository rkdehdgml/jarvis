"""
weather_service.py — 날씨 정보 서비스 (Open-Meteo, 무료 API)
"""
from __future__ import annotations
import asyncio
import time
from typing import Optional
import httpx

_CACHE_TTL = 600  # 10분 캐시

WMO_CODES: dict[int, tuple[str, str]] = {
    0:  ("맑음",        "CLEAR"),
    1:  ("대체로 맑음", "MOSTLY_CLEAR"),
    2:  ("부분 흐림",   "PARTLY_CLOUDY"),
    3:  ("흐림",        "CLOUDY"),
    45: ("안개",        "FOG"),
    48: ("결빙 안개",   "FOG"),
    51: ("가벼운 이슬비","DRIZZLE"),
    53: ("이슬비",      "DRIZZLE"),
    55: ("강한 이슬비", "DRIZZLE"),
    61: ("가벼운 비",   "RAIN"),
    63: ("비",          "RAIN"),
    65: ("강한 비",     "RAIN"),
    71: ("가벼운 눈",   "SNOW"),
    73: ("눈",          "SNOW"),
    75: ("강한 눈",     "SNOW"),
    77: ("눈발",        "SNOW"),
    80: ("소나기",      "SHOWER"),
    81: ("강한 소나기", "SHOWER"),
    82: ("폭우",        "SHOWER"),
    85: ("눈 소나기",   "SNOW"),
    95: ("뇌우",        "THUNDER"),
    96: ("우박 뇌우",   "THUNDER"),
    99: ("강한 우박 뇌우","THUNDER"),
}

WMO_ICONS: dict[str, str] = {
    "CLEAR":        "☀",
    "MOSTLY_CLEAR": "🌤",
    "PARTLY_CLOUDY":"⛅",
    "CLOUDY":       "☁",
    "FOG":          "🌫",
    "DRIZZLE":      "🌦",
    "RAIN":         "🌧",
    "SNOW":         "🌨",
    "SHOWER":       "🌦",
    "THUNDER":      "⛈",
}

_cache: dict[str, dict] = {}
_cache_ts: dict[str, float] = {}


async def get_weather(city: str = "Seoul") -> dict:
    now = time.time()
    key = city.lower()
    if key in _cache and now - _cache_ts.get(key, 0) < _CACHE_TTL:
        return _cache[key]

    async with httpx.AsyncClient(timeout=10) as client:
        geo_resp = await client.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": city, "count": 1, "language": "ko"},
        )
        geo = geo_resp.json()
        results = geo.get("results")
        if not results:
            raise ValueError(f"도시를 찾을 수 없습니다: {city}")

        loc = results[0]
        lat, lon = loc["latitude"], loc["longitude"]
        city_name = loc.get("name", city)

        w_resp = await client.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude":  lat,
                "longitude": lon,
                "current":   "temperature_2m,apparent_temperature,weather_code,"
                             "wind_speed_10m,relative_humidity_2m",
                "daily":     "weather_code,temperature_2m_max,temperature_2m_min",
                "timezone":  "auto",
                "forecast_days": 4,
            },
        )
        w = w_resp.json()
        cur  = w["current"]
        code = cur["weather_code"]
        desc, cond = WMO_CODES.get(code, ("알 수 없음", "CLEAR"))
        icon = WMO_ICONS.get(cond, "❓")

        daily = w.get("daily", {})
        forecast = []
        for i in range(min(3, len(daily.get("time", [])))):
            fc_code = daily["weather_code"][i]
            _, fc_cond = WMO_CODES.get(fc_code, ("", "CLEAR"))
            forecast.append({
                "date":    daily["time"][i],
                "icon":    WMO_ICONS.get(fc_cond, "❓"),
                "max":     round(daily["temperature_2m_max"][i]),
                "min":     round(daily["temperature_2m_min"][i]),
            })

        result = {
            "city":       city_name,
            "temp":       round(cur["temperature_2m"]),
            "feels_like": round(cur["apparent_temperature"]),
            "humidity":   cur["relative_humidity_2m"],
            "wind_speed": round(cur["wind_speed_10m"], 1),
            "condition":  desc,
            "condition_code": cond,
            "icon":       icon,
            "wmo_code":   code,
            "forecast":   forecast,
        }

    _cache[key]    = result
    _cache_ts[key] = now
    return result
