"""
communication.py — 커뮤니케이션 내장 명령
════════════════════════════════════════════════════════════════════════════════
지원 명령:
  · Gmail 전송 (SMTP, 앱 비밀번호 — .env GMAIL_ADDRESS / GMAIL_APP_PASSWORD)
  · WhatsApp 메시지 전송 (개인/그룹, pywhatkit — WhatsApp Web 로그인 필요)

말하는 형식:
  · "철수에게 이메일 보내줘, 제목은 회의 일정, 내용은 오늘 3시에 회의입니다"
  · "철수에게 왓츠앱 보내줘: 오늘 회의는 3시입니다"
  · "개발팀 그룹에 왓츠앱 보내줘: 오늘 회의는 3시입니다"

연락처는 data/contacts.json (이름 → {email, phone, group_id})에서 조회한다.
════════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import asyncio
import smtplib
import re
from email.mime.text import MIMEText

from app.commands.contacts_store import find_contact
from app.commands.registry import CommandResult, register
from app.config import settings

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# ══════════════════════════════════════════════════════════════════════════════
# 1. Gmail 전송 (SMTP)
# ══════════════════════════════════════════════════════════════════════════════

@register(
    "이메일 전송",
    r"^(.+?)(?:에게)\s*(?:이메일|메일)\s*(?:보내줘|전송해줘)\s*[,.]?\s*"
    r"제목\s*(?:은|:)\s*(.+?)\s*[,.]?\s*내용\s*(?:은|:)\s*(.+)$",
)
async def send_email(m, text: str) -> CommandResult:
    recipient_name, subject, body = (g.strip() for g in m.groups())

    if not settings.gmail_address or not settings.gmail_app_password:
        return CommandResult(
            text="이메일 전송 기능을 사용하려면 .env의 GMAIL_ADDRESS / GMAIL_APP_PASSWORD를 설정해야 합니다. "
                 "Google 계정의 '앱 비밀번호'를 발급받아 사용하세요."
        )

    to_addr = recipient_name
    if not _EMAIL_RE.match(to_addr):
        contact = find_contact(recipient_name)
        if not contact or not contact.get("email"):
            return CommandResult(text=f"'{recipient_name}'의 이메일 주소를 연락처에서 찾을 수 없습니다.")
        to_addr = contact["email"]

    msg = MIMEText(body, _charset="utf-8")
    msg["Subject"] = subject
    msg["From"] = settings.gmail_address
    msg["To"] = to_addr

    loop = asyncio.get_event_loop()

    def _send():
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(settings.gmail_address, settings.gmail_app_password)
            server.send_message(msg)

    try:
        await loop.run_in_executor(None, _send)
    except smtplib.SMTPException as e:
        return CommandResult(text=f"이메일 전송에 실패했습니다: {e}")

    return CommandResult(text=f"{recipient_name}님께 '{subject}' 제목으로 이메일을 보냈습니다.")


# ══════════════════════════════════════════════════════════════════════════════
# 2. WhatsApp 메시지 전송 (개인/그룹)
# ══════════════════════════════════════════════════════════════════════════════

@register(
    "왓츠앱 그룹 전송",
    r"^(.+?)\s*그룹(?:에)?\s*(?:왓츠앱|whatsapp)\s*(?:메시지)?\s*(?:보내줘|전송해줘)\s*[:]?\s*(.+)$",
)
async def send_whatsapp_group(m, text: str) -> CommandResult:
    group_name, message = (g.strip() for g in m.groups())

    contact = find_contact(group_name)
    group_id = contact.get("group_id") if contact else None
    if not group_id:
        return CommandResult(text=f"'{group_name}' 그룹의 group_id를 연락처(data/contacts.json)에서 찾을 수 없습니다.")

    loop = asyncio.get_event_loop()

    def _send():
        import pywhatkit
        pywhatkit.sendwhatmsg_to_group_instantly(group_id, message, wait_time=15, tab_close=True)

    try:
        await loop.run_in_executor(None, _send)
    except Exception as e:
        return CommandResult(text=f"WhatsApp 그룹 메시지 전송에 실패했습니다: {e}")

    return CommandResult(text=f"'{group_name}' 그룹에 WhatsApp 메시지를 보냈습니다.")


@register(
    "왓츠앱 전송",
    r"^(.+?)(?:에게)\s*(?:왓츠앱|whatsapp)\s*(?:메시지)?\s*(?:보내줘|전송해줘)\s*[:]?\s*(.+)$",
)
async def send_whatsapp(m, text: str) -> CommandResult:
    recipient_name, message = (g.strip() for g in m.groups())

    phone = recipient_name
    if not phone.startswith("+"):
        contact = find_contact(recipient_name)
        if not contact or not contact.get("phone"):
            return CommandResult(text=f"'{recipient_name}'의 전화번호를 연락처에서 찾을 수 없습니다.")
        phone = contact["phone"]

    loop = asyncio.get_event_loop()

    def _send():
        import pywhatkit
        pywhatkit.sendwhatmsg_instantly(phone, message, wait_time=15, tab_close=True)

    try:
        await loop.run_in_executor(None, _send)
    except Exception as e:
        return CommandResult(text=f"WhatsApp 메시지 전송에 실패했습니다: {e}")

    return CommandResult(text=f"{recipient_name}님께 WhatsApp 메시지를 보냈습니다.")
