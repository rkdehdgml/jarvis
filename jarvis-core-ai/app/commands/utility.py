"""
utility.py — 유틸리티 내장 명령
════════════════════════════════════════════════════════════════════════════════
지원 명령:
  · PDF 파일 읽기 (텍스트 추출 → TTS로 읽을 수 있도록 텍스트 반환)
  · QR 코드 생성 (링크/텍스트)
  · 연락처 사전 (추가/검색) — data/contacts.json
  · 웹캠 사진 촬영
  · 무작위 프로그래밍 농담 (pyjokes)
  · 일별 일정 확인 (scheduler_service 연동)
  · 지정 시간 동안 대기 ("N분 후 깨워줘")
  · "wake up" 명령 전까지 슬립 모드 유지 (registry.py의 전역 sleep 플래그)
════════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime
from pathlib import Path

from app.commands.contacts_store import find_contact, load_contacts, save_contacts
from app.commands.registry import CommandResult, register, set_sleep_mode
from app.config import settings

_CAPTURES_DIR = Path(settings.os_captures_dir)


# ══════════════════════════════════════════════════════════════════════════════
# 1. PDF 파일 읽기 (텍스트 추출 → TTS)
# ══════════════════════════════════════════════════════════════════════════════

@register("PDF 읽기", r"(.+\.pdf)\s*(?:파일)?\s*(?:읽어줘|읽어서\s*들려줘)")
async def read_pdf(m, text: str) -> CommandResult:
    path = Path(m.group(1).strip().strip('"').strip("'"))
    if not path.exists():
        return CommandResult(text=f"파일을 찾을 수 없습니다: {path}")

    loop = asyncio.get_event_loop()

    def _extract():
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        chunks = []
        for page in reader.pages:
            chunks.append(page.extract_text() or "")
        return "\n".join(chunks).strip()

    try:
        content = await loop.run_in_executor(None, _extract)
    except Exception as e:
        return CommandResult(text=f"PDF를 읽는 데 실패했습니다: {e}")

    if not content:
        return CommandResult(text=f"{path.name}에서 텍스트를 추출하지 못했습니다.")

    # TTS로 읽기에는 너무 길 수 있으므로 앞부분만 사용
    snippet = content[:1500]
    suffix = "... (이하 생략)" if len(content) > 1500 else ""
    return CommandResult(text=f"{path.name} 내용을 읽어드립니다.\n{snippet}{suffix}")


# ══════════════════════════════════════════════════════════════════════════════
# 2. QR 코드 생성
# ══════════════════════════════════════════════════════════════════════════════

@register("QR코드 생성", r"(.+?)\s*(?:으로|를|을)?\s*qr\s*코드\s*(?:만들어줘|생성해줘)")
async def make_qr_code(m, text: str) -> CommandResult:
    content = m.group(1).strip()
    if not content:
        return CommandResult(text="QR 코드로 만들 내용을 말씀해 주세요.")

    import qrcode

    _CAPTURES_DIR.mkdir(parents=True, exist_ok=True)
    out_path = _CAPTURES_DIR / f"qrcode_{time.strftime('%Y%m%d_%H%M%S')}.png"

    img = qrcode.make(content)
    img.save(out_path)
    return CommandResult(text=f"QR 코드를 생성했습니다: {out_path}", data={"path": str(out_path)})


# ══════════════════════════════════════════════════════════════════════════════
# 3. 연락처 사전 (추가 / 검색)
# ══════════════════════════════════════════════════════════════════════════════

@register("연락처 추가", r"연락처에\s*(.+?)\s*(?:을|를)?\s*추가해줘\s*(.*)$")
async def add_contact(m, text: str) -> CommandResult:
    name = m.group(1).strip()
    rest = m.group(2)

    email_m = re.search(r"(?:이메일|메일)\s*(?:은|:)\s*(\S+@\S+)", rest)
    phone_m = re.search(r"(?:전화번호|폰번호|번호)\s*(?:은|:)\s*(\+?\d[\d\-]*)", rest)
    group_m = re.search(r"(?:그룹\s*(?:id|아이디))\s*(?:은|:)\s*(\S+)", rest)

    if not (email_m or phone_m or group_m):
        return CommandResult(text="이메일, 전화번호, 그룹ID 중 최소 하나는 함께 말씀해 주세요. "
                                   "예: '연락처에 철수 추가해줘 이메일은 a@b.com 전화번호는 +821012345678'")

    contacts = load_contacts()
    entry = contacts.get(name, {})
    if email_m:
        entry["email"] = email_m.group(1)
    if phone_m:
        entry["phone"] = phone_m.group(1)
    if group_m:
        entry["group_id"] = group_m.group(1)
    contacts[name] = entry
    save_contacts(contacts)

    return CommandResult(text=f"연락처에 '{name}'을 추가/갱신했습니다.")


@register("연락처 검색", r"(.+?)\s*(?:의)?\s*연락처\s*(?:알려줘|찾아줘|뭐야)")
async def search_contact(m, text: str) -> CommandResult:
    name = m.group(1).strip()
    contact = find_contact(name)
    if not contact:
        return CommandResult(text=f"'{name}'의 연락처를 찾을 수 없습니다.")

    parts = [f"{k}: {v}" for k, v in contact.items()]
    return CommandResult(text=f"'{name}'의 연락처 — " + ", ".join(parts))


# ══════════════════════════════════════════════════════════════════════════════
# 4. 웹캠 사진 촬영
# ══════════════════════════════════════════════════════════════════════════════

@register("웹캠 사진", r"웹캠(?:으로)?\s*(?:사진\s*)?(?:찍어줘|촬영해줘)")
async def take_webcam_photo(m, text: str) -> CommandResult:
    loop = asyncio.get_event_loop()
    _CAPTURES_DIR.mkdir(parents=True, exist_ok=True)
    out_path = _CAPTURES_DIR / f"webcam_{time.strftime('%Y%m%d_%H%M%S')}.jpg"

    def _capture():
        import cv2
        cap = cv2.VideoCapture(0)
        try:
            if not cap.isOpened():
                return False
            # 카메라 워밍업
            for _ in range(5):
                cap.read()
            ok, frame = cap.read()
            if not ok:
                return False
            cv2.imwrite(str(out_path), frame)
            return True
        finally:
            cap.release()

    ok = await loop.run_in_executor(None, _capture)
    if not ok:
        return CommandResult(text="웹캠에 접근할 수 없습니다. 카메라 연결 상태를 확인해 주세요.")

    return CommandResult(text=f"웹캠 사진을 저장했습니다: {out_path}", data={"path": str(out_path)})


# ══════════════════════════════════════════════════════════════════════════════
# 5. 무작위 프로그래밍 농담
# ══════════════════════════════════════════════════════════════════════════════

@register("프로그래밍 농담", r"(?:프로그래밍|개발자)?\s*농담\s*(?:해줘|알려줘|들려줘)")
async def programming_joke(m, text: str) -> CommandResult:
    import pyjokes
    joke = pyjokes.get_joke(language="en", category="neutral")
    return CommandResult(text=f"{joke}\n(영어 농담입니다 — pyjokes는 한국어를 지원하지 않습니다.)")


# ══════════════════════════════════════════════════════════════════════════════
# 6. 일별 일정 확인
# ══════════════════════════════════════════════════════════════════════════════

@register("오늘 일정", r"오늘\s*일정\s*(?:알려줘|확인해줘|뭐\s*있어)")
async def todays_schedule(m, text: str) -> CommandResult:
    from app.services.scheduler_service import scheduler

    today = datetime.now().date().isoformat()
    items = [r for r in scheduler.list_all() if r["due_at"].startswith(today)]
    if not items:
        return CommandResult(text="오늘 등록된 일정이 없습니다.")

    items.sort(key=lambda r: r["due_at"])
    lines = [f"- {r['due_at'][11:16]} {r['title']}" for r in items]
    return CommandResult(text="오늘의 일정입니다.\n" + "\n".join(lines))


# ══════════════════════════════════════════════════════════════════════════════
# 7. 지정 시간 동안 대기 ("N분 후 깨워줘")
# ══════════════════════════════════════════════════════════════════════════════

@register("대기 타이머", r"(\d+)\s*(분|시간|초)\s*(?:후|뒤)(?:에)?\s*(?:깨워줘|알려줘|알람)")
async def wait_timer(m, text: str) -> CommandResult:
    from app.services.scheduler_service import scheduler

    amount = int(m.group(1))
    unit = m.group(2)
    seconds = {"초": 1, "분": 60, "시간": 3600}[unit]
    delay = amount * seconds

    due_at = datetime.fromtimestamp(time.time() + delay).isoformat(timespec="seconds")
    scheduler.add(title=f"{amount}{unit} 타이머", due_at=due_at, description="요청하신 시간이 되었습니다.")

    return CommandResult(text=f"{amount}{unit} 후에 알려드리겠습니다.")


# ══════════════════════════════════════════════════════════════════════════════
# 8. 슬립 모드 ("wake up" 명령까지 유지)
# ══════════════════════════════════════════════════════════════════════════════

@register("슬립 모드 진입", r"(?:슬립\s*모드(?:로)?\s*(?:전환해줘|들어가)|자비스야?\s*자|좀\s*쉬고\s*있어)")
async def enter_sleep_mode(m, text: str) -> CommandResult:
    set_sleep_mode(True)
    return CommandResult(text="슬립 모드로 전환합니다. 'wake up' 또는 '일어나'라고 말씀하시면 다시 응답하겠습니다.")


@register("슬립 모드 해제", r"(?:wake\s*up|일어나|기상)")
async def exit_sleep_mode(m, text: str) -> CommandResult:
    was_sleeping = set_sleep_mode(False)
    if was_sleeping:
        return CommandResult(text="네, 다시 활성화되었습니다.")
    return CommandResult(text="네, 듣고 있습니다.")
