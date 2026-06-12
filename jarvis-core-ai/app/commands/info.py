"""
info.py — 정보 제공 내장 명령
════════════════════════════════════════════════════════════════════════════════
지원 명령:
  · 현재 시간 / 요일
  · IP 주소 조회 (공용 + 내부)
  · 인터넷 속도 측정 (speedtest-cli)
  · 시스템 상태 (CPU / RAM / 디스크) — psutil
  · 현재 위치 (공용 IP 기반 지오코딩, ip-api.com — 무료, 키 불필요)
════════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import asyncio
import socket
from datetime import datetime

from app.commands.registry import CommandResult, register

_WEEKDAY_KO = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]


# ══════════════════════════════════════════════════════════════════════════════
# 1. 현재 시간 / 요일
# ══════════════════════════════════════════════════════════════════════════════

@register("현재 시간", r"(지금|현재)\s*(시간|몇\s*시)")
async def current_time(m, text: str) -> CommandResult:
    now = datetime.now()
    return CommandResult(text=f"현재 시간은 {now.strftime('%H시 %M분')}입니다.")


@register("오늘 요일", r"(오늘|지금)\s*(?:이|은|는)?\s*(?:무슨\s*)?요일")
async def current_weekday(m, text: str) -> CommandResult:
    now = datetime.now()
    weekday = _WEEKDAY_KO[now.weekday()]
    return CommandResult(text=f"오늘은 {now.strftime('%Y년 %m월 %d일')} {weekday}입니다.")


# ══════════════════════════════════════════════════════════════════════════════
# 2. IP 주소 조회
# ══════════════════════════════════════════════════════════════════════════════

@register("IP 주소", r"(?:내|제|나의)?\s*(?:ip|아이피)\s*(?:주소)?\s*(?:알려줘|조회|확인)?")
async def get_ip_address(m, text: str) -> CommandResult:
    loop = asyncio.get_event_loop()

    def _fetch():
        import requests
        try:
            public_ip = requests.get("https://api.ipify.org", timeout=5).text.strip()
        except requests.RequestException:
            public_ip = "조회 실패"

        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            s.close()
        except OSError:
            local_ip = "조회 실패"

        return public_ip, local_ip

    public_ip, local_ip = await loop.run_in_executor(None, _fetch)
    return CommandResult(text=f"공용 IP: {public_ip}, 내부 IP: {local_ip}")


# ══════════════════════════════════════════════════════════════════════════════
# 3. 인터넷 속도 측정
# ══════════════════════════════════════════════════════════════════════════════

@register("인터넷 속도", r"인터넷\s*속도|속도\s*측정|네트워크\s*속도")
async def measure_internet_speed(m, text: str) -> CommandResult:
    loop = asyncio.get_event_loop()

    def _run():
        import speedtest
        st = speedtest.Speedtest()
        st.get_best_server()
        download = st.download() / 1_000_000  # Mbps
        upload = st.upload() / 1_000_000
        ping = st.results.ping
        return download, upload, ping

    try:
        download, upload, ping = await loop.run_in_executor(None, _run)
    except Exception as e:
        return CommandResult(text=f"인터넷 속도 측정에 실패했습니다: {e}")

    return CommandResult(
        text=(
            f"인터넷 속도 측정 결과 — 다운로드: {download:.1f} Mbps, "
            f"업로드: {upload:.1f} Mbps, 핑: {ping:.0f} ms"
        ),
        data={"download_mbps": download, "upload_mbps": upload, "ping_ms": ping},
    )


# ══════════════════════════════════════════════════════════════════════════════
# 4. 시스템 상태 (CPU / RAM / 디스크)
# ══════════════════════════════════════════════════════════════════════════════

@register("시스템 상태", r"시스템\s*(?:상태|상황)|(?:cpu|램|메모리|디스크)\s*(?:사용량|상태)")
async def system_status(m, text: str) -> CommandResult:
    import sys as _sys

    import psutil

    cpu = psutil.cpu_percent(interval=0.5)
    mem = psutil.virtual_memory()
    disk_path = (__import__("os").environ.get("SystemDrive", "C:") + "\\") if _sys.platform == "win32" else "/"
    disk = psutil.disk_usage(disk_path)

    return CommandResult(
        text=(
            f"시스템 상태 — CPU: {cpu:.0f}%, "
            f"RAM: {mem.percent:.0f}% ({mem.used / (1024**3):.1f}GB / {mem.total / (1024**3):.1f}GB), "
            f"디스크: {disk.percent:.0f}% ({disk.used / (1024**3):.1f}GB / {disk.total / (1024**3):.1f}GB)"
        ),
        data={"cpu_percent": cpu, "ram_percent": mem.percent, "disk_percent": disk.percent},
    )


# ══════════════════════════════════════════════════════════════════════════════
# 5. 현재 위치 (공용 IP 기반 지오코딩 — 무료, 키 불필요)
# ══════════════════════════════════════════════════════════════════════════════

@register("현재 위치", r"(?:내|제|나의|현재)?\s*(?:위치|지금\s*어디|어디야)")
async def current_location(m, text: str) -> CommandResult:
    loop = asyncio.get_event_loop()

    def _fetch():
        import requests
        resp = requests.get("http://ip-api.com/json/?lang=ko", timeout=5)
        resp.raise_for_status()
        return resp.json()

    try:
        data = await loop.run_in_executor(None, _fetch)
    except Exception as e:
        return CommandResult(text=f"위치 조회에 실패했습니다: {e}")

    if data.get("status") != "success":
        return CommandResult(text="위치 조회에 실패했습니다.")

    location = f"{data.get('country', '')} {data.get('regionName', '')} {data.get('city', '')}".strip()
    return CommandResult(
        text=f"현재 위치(IP 기반 추정)는 {location} 입니다.",
        data={"lat": data.get("lat"), "lon": data.get("lon"), "city": data.get("city")},
    )
