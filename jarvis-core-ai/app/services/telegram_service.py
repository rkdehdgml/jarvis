"""
telegram_service.py — 텔레그램 봇 원격 제어 서비스
════════════════════════════════════════════════════════════════════════════════
동작 원리:
  · polling 방식: FastAPI 시작 시 백그라운드 스레드가 텔레그램 API를 주기적으로 폴링
  · 허용된 Chat ID에서 온 명령만 처리 (화이트리스트)
  · 명령 → 핸들러 → 결과를 텔레그램으로 전송

지원 명령:
  /start, /help           사용법 안내
  /status                 PC 상태 (CPU/RAM/현재 JARVIS 상태)
  /screenshot             화면 캡처 → 이미지 전송
  /run [앱]               앱 실행
  /volume [0-100]         볼륨 설정
  /lock                   PC 잠금
  /jarvis [질문]          JARVIS AI에게 질문 → 답변 전송
  /reminders              등록된 리마인더 목록

보안:
  · TELEGRAM_ALLOWED_IDS에 없는 Chat ID는 전부 거부
  · 위험 패턴 (_DANGEROUS_PATTERNS) 명령 차단
════════════════════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import asyncio
import re
import subprocess
import threading
import time
from typing import Optional

import httpx

from app.config import settings

# ── 위험 명령 패턴 ─────────────────────────────────────────────────────────────
_DANGEROUS = re.compile(
    r"(rm\s+-rf|format\s+[a-z]:|\brmdir\b|shutdown\s+/[srfh]"
    r"|del\s+/[sfq]|\bdd\b.*of=|mkfs\b|fdisk\b)",
    re.IGNORECASE,
)

_BASE_URL = "https://api.telegram.org/bot{token}"


class TelegramService:
    """텔레그램 봇 polling 기반 원격 제어 서비스 (싱글턴)."""

    def __init__(self) -> None:
        self._token:   str        = settings.telegram_bot_token
        self._allowed: set[int]   = self._parse_allowed_ids()
        self._interval: int       = settings.telegram_poll_interval
        self._running:  bool      = False
        self._offset:   int       = 0
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        # SSE 구독자 (원격 명령 실행 알림)
        self._subscribers: list[asyncio.Queue] = []
        self._lock = threading.Lock()

    # ── 공개 API ──────────────────────────────────────────────────────────────

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        if not self._token:
            print("[Telegram] TELEGRAM_BOT_TOKEN 미설정 — 텔레그램 봇 비활성화")
            return
        if not self._allowed:
            print("[Telegram] TELEGRAM_ALLOWED_IDS 미설정 — 텔레그램 봇 비활성화")
            return
        if self._running:
            return
        self._loop    = loop
        self._running = True
        t = threading.Thread(target=self._poll_loop, daemon=True, name="TelegramBot")
        t.start()
        print(f"[Telegram] 봇 시작 (허용 ID: {self._allowed})")

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=20)
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            try:
                self._subscribers.remove(q)
            except ValueError:
                pass

    def is_active(self) -> bool:
        return self._running and bool(self._token)

    # ── Polling 루프 ───────────────────────────────────────────────────────────

    def _poll_loop(self) -> None:
        base = _BASE_URL.format(token=self._token)
        with httpx.Client(timeout=30) as client:
            while self._running:
                try:
                    updates = self._get_updates(client, base)
                    for upd in updates:
                        self._handle_update(client, base, upd)
                except Exception as e:
                    print(f"[Telegram] polling 오류: {e}")
                time.sleep(self._interval)

    def _get_updates(self, client: httpx.Client, base: str) -> list[dict]:
        params: dict = {"timeout": 10, "offset": self._offset}
        r = client.get(f"{base}/getUpdates", params=params)
        r.raise_for_status()
        data    = r.json()
        updates = data.get("result", [])
        if updates:
            self._offset = updates[-1]["update_id"] + 1
        return updates

    def _handle_update(self, client: httpx.Client, base: str, upd: dict) -> None:
        msg = upd.get("message") or upd.get("edited_message")
        if not msg:
            return

        chat_id  = msg["chat"]["id"]
        text     = (msg.get("text") or "").strip()

        if not text:
            return

        # 보안: 화이트리스트 확인
        if chat_id not in self._allowed:
            self._send(client, base, chat_id, "⛔ 접근 권한이 없습니다.")
            print(f"[Telegram] 거부된 Chat ID: {chat_id}")
            return

        print(f"[Telegram] 수신: {text!r} (from {chat_id})")
        reply = self._dispatch(text)
        self._send(client, base, chat_id, reply["text"])

        # 이미지 첨부 (스크린샷)
        if reply.get("photo"):
            self._send_photo(client, base, chat_id, reply["photo"])

        # SSE로 오버레이에 알림
        self._broadcast_event(text, reply["text"])

    # ── 명령 디스패치 ──────────────────────────────────────────────────────────

    def _dispatch(self, text: str) -> dict:
        """명령 텍스트 → {"text": str, "photo": bytes|None}"""
        lower = text.lower().strip()

        if lower in ("/start", "/help", "도움말", "help"):
            return {"text": self._help_text()}

        if lower in ("/status", "상태", "status"):
            return {"text": self._cmd_status()}

        if lower in ("/screenshot", "스크린샷", "화면"):
            return self._cmd_screenshot()

        m = re.match(r"(/run|실행|앱\s*열어)\s+(.+)", text, re.I)
        if m:
            return {"text": self._cmd_run(m.group(2).strip())}

        m = re.match(r"(/volume|볼륨)\s+(\d{1,3})", text, re.I)
        if m:
            return {"text": self._cmd_volume(int(m.group(2)))}

        if lower in ("/lock", "잠금", "lock"):
            return {"text": self._cmd_lock()}

        m = re.match(r"(/jarvis|자비스|jarvis)\s+(.+)", text, re.I | re.S)
        if m:
            return {"text": self._cmd_jarvis(m.group(2).strip())}

        if lower in ("/reminders", "리마인더", "알림 목록"):
            return {"text": self._cmd_reminders()}

        return {"text": f"❓ 알 수 없는 명령입니다.\n/help 를 입력해 사용법을 확인하세요."}

    # ── 개별 명령 핸들러 ───────────────────────────────────────────────────────

    def _help_text(self) -> str:
        return (
            "🤖 *JARVIS 원격 제어*\n\n"
            "/status — PC 상태\n"
            "/screenshot — 화면 캡처\n"
            "/run [앱] — 앱 실행\n"
            "/volume [0\\-100] — 볼륨 조절\n"
            "/lock — PC 잠금\n"
            "/jarvis [질문] — AI 질문\n"
            "/reminders — 리마인더 목록\n"
        )

    def _cmd_status(self) -> str:
        try:
            import psutil
            cpu  = psutil.cpu_percent(interval=1)
            ram  = psutil.virtual_memory()
            disk = psutil.disk_usage("/")
            return (
                f"💻 *PC 상태*\n"
                f"CPU: {cpu:.1f}%\n"
                f"RAM: {ram.percent:.1f}% ({ram.used//1024//1024}MB / {ram.total//1024//1024}MB)\n"
                f"Disk: {disk.percent:.1f}% 사용 중"
            )
        except Exception as e:
            return f"상태 조회 실패: {e}"

    def _cmd_screenshot(self) -> dict:
        try:
            import pyautogui
            import io
            img = pyautogui.screenshot()
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return {"text": "📸 화면 캡처", "photo": buf.getvalue()}
        except Exception as e:
            return {"text": f"스크린샷 실패: {e}"}

    def _cmd_run(self, app: str) -> str:
        if _DANGEROUS.search(app):
            return "⛔ 위험한 명령은 실행할 수 없습니다."
        try:
            subprocess.Popen(app, shell=True)
            return f"✅ 실행됨: {app}"
        except Exception as e:
            return f"실행 실패: {e}"

    def _cmd_volume(self, level: int) -> str:
        level = max(0, min(100, level))
        try:
            # Windows: nircmd 또는 PowerShell
            script = (
                f"$obj = New-Object -ComObject WScript.Shell; "
                f"1..50 | ForEach-Object {{ $obj.SendKeys([char]174) }}; "
                f"$vol = [math]::Round({level} / 2); "
                f"1..$vol | ForEach-Object {{ $obj.SendKeys([char]175) }}"
            )
            subprocess.run(
                ["powershell", "-Command", script],
                capture_output=True, timeout=5
            )
            return f"🔊 볼륨 {level}%로 설정"
        except Exception as e:
            return f"볼륨 설정 실패: {e}"

    def _cmd_lock(self) -> str:
        try:
            subprocess.run(["rundll32.exe", "user32.dll,LockWorkStation"])
            return "🔒 PC가 잠겼습니다."
        except Exception as e:
            return f"잠금 실패: {e}"

    def _cmd_jarvis(self, question: str) -> str:
        try:
            resp = httpx.post(
                "http://localhost:8000/api/chat/stream",
                json={"message": question, "history": []},
                timeout=30,
            )
            return resp.text[:1000] if resp.text else "응답 없음"
        except Exception as e:
            return f"JARVIS 질문 실패: {e}"

    def _cmd_reminders(self) -> str:
        try:
            resp = httpx.get("http://localhost:8000/api/scheduler/reminders", timeout=5)
            items = resp.json()
            if not items:
                return "📅 등록된 리마인더가 없습니다."
            lines = ["📅 *리마인더 목록*"]
            for r in items[:10]:
                due = r.get("due_at", "").replace("T", " ")[:16]
                lines.append(f"• {r['title']} — {due}")
            return "\n".join(lines)
        except Exception as e:
            return f"리마인더 조회 실패: {e}"

    # ── 전송 헬퍼 ─────────────────────────────────────────────────────────────

    def _send(self, client: httpx.Client, base: str, chat_id: int, text: str) -> None:
        try:
            client.post(f"{base}/sendMessage", json={
                "chat_id":    chat_id,
                "text":       text[:4096],
                "parse_mode": "Markdown",
            })
        except Exception as e:
            print(f"[Telegram] 전송 실패: {e}")

    def _send_photo(self, client: httpx.Client, base: str, chat_id: int, photo: bytes) -> None:
        try:
            client.post(
                f"{base}/sendPhoto",
                data={"chat_id": chat_id},
                files={"photo": ("screenshot.png", photo, "image/png")},
                timeout=20,
            )
        except Exception as e:
            print(f"[Telegram] 이미지 전송 실패: {e}")

    # ── SSE 브로드캐스트 ───────────────────────────────────────────────────────

    def _broadcast_event(self, command: str, reply: str) -> None:
        payload = {"event": "telegram_command", "command": command, "reply": reply}
        if not self._loop or not self._loop.is_running():
            return
        with self._lock:
            for q in list(self._subscribers):
                asyncio.run_coroutine_threadsafe(
                    self._safe_put(q, payload), self._loop
                )

    @staticmethod
    async def _safe_put(q: asyncio.Queue, item: dict) -> None:
        try:
            q.put_nowait(item)
        except asyncio.QueueFull:
            pass

    # ── 내부 유틸 ─────────────────────────────────────────────────────────────

    def _parse_allowed_ids(self) -> set[int]:
        raw = settings.telegram_allowed_ids
        ids: set[int] = set()
        for part in raw.split(","):
            part = part.strip()
            if part.lstrip("-").isdigit():
                ids.add(int(part))
        return ids


telegram_bot = TelegramService()
