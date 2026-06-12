"""
wrapper.py — Claude Code 헤드리스 모드(claude -p) 비동기 래퍼
════════════════════════════════════════════════════════════════════════════════
설계 원칙 (★ 최우선):
  · 이 모듈에서 AI 추론은 claude CLI 호출로만 이뤄진다.
    다른 AI SDK import·HTTP 호출·.env 키 접근 금지.
  · subprocess env는 화이트리스트로만 구성 — ANTHROPIC_API_KEY를 포함해
    화이트리스트 밖의 모든 변수는 전달되지 않는다.
    (allow_api_key_billing=True를 명시한 경우에만 예외 + 매 호출 경고)
  · 미설치/미로그인/예산초과/한도도달은 예외가 아닌 CCStatusEvent로 전달한다.
  · 한도 도달 시 유료 크레딧 폴백·자동 재시도는 절대 하지 않는다.

사용:
  async for event in wrapper.stream("프롬프트", system="페르소나"):
      ...
════════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from typing import AsyncGenerator, Optional

from app.services.claude_code import paths
from app.services.claude_code.detector import ClaudeDetector, _creationflags
from app.services.claude_code.schema import (
    CCEvent, CCInit, CCResult, CCStatusEvent, CCTextDelta, CCToolResult,
    CCToolUse, CCWarning, ClaudeCodeSettings, ClaudeStatus,
)
from app.services.claude_code.settings_store import SettingsStore
from app.services.claude_code.usage_tracker import UsageTracker

# ── subprocess env 화이트리스트 ────────────────────────────────────────────────
# claude CLI 동작에 필요한 최소 변수만 통과. 이 외 모든 키(.env의 타 서비스
# API 키 포함)는 subprocess에 절대 전달되지 않는다.
ENV_WHITELIST: tuple[str, ...] = (
    "PATH", "HOME", "USER", "SHELL", "LANG", "LC_ALL", "TERM",
    # claude CLI 설정 디렉토리 관련
    "CLAUDE_CONFIG_DIR", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME",
    # Windows에서 node/claude 구동에 필요한 시스템 변수
    "SYSTEMROOT", "APPDATA", "LOCALAPPDATA", "USERPROFILE", "COMSPEC",
    "TEMP", "TMP",
)

# ── 한도/로그인 오류 감지 패턴 ────────────────────────────────────────────────
_LIMIT_RE = re.compile(r"usage limit reached\|?(\d+)?", re.IGNORECASE)
_LIMIT_HINT_RE = re.compile(r"(usage limit|rate limit|한도)", re.IGNORECASE)
_LOGIN_RE = re.compile(
    r"(/login|please run.*login|not logged in|invalid api key"
    r"|oauth token (revoked|expired)|authentication[_ ]error)",
    re.IGNORECASE,
)

# 상태별 한국어 안내 메시지 (자비스 채팅에 그대로 노출 가능)
_STATUS_KO: dict[ClaudeStatus, str] = {
    ClaudeStatus.NOT_INSTALLED:
        "Claude Code CLI가 설치되어 있지 않습니다. 설정 모달의 설치 안내를 확인해 주세요.",
    ClaudeStatus.NOT_LOGGED_IN:
        "Claude 계정 로그인이 필요합니다. 터미널에서 `claude`를 실행해 `/login`을 진행해 주세요.",
}


def _fmt_reset(reset_at: Optional[int]) -> str:
    if not reset_at:
        return "잠시 후"
    try:
        return datetime.fromtimestamp(reset_at).strftime("%m월 %d일 %H:%M")
    except (ValueError, OSError, OverflowError):
        return "잠시 후"


class ClaudeCodeWrapper:
    """claude -p 호출의 단일 진입점 (동시 실행 1개로 제한)."""

    def __init__(
        self,
        store: Optional[SettingsStore] = None,
        tracker: Optional[UsageTracker] = None,
        detector: Optional[ClaudeDetector] = None,
    ) -> None:
        self._store = store or SettingsStore()
        self._tracker = tracker or UsageTracker()
        self._detector = detector or ClaudeDetector(self._store.load)
        self._sem = asyncio.Semaphore(1)          # 동시 claude 프로세스 1개 (초과분 큐잉)
        self._session_id: Optional[str] = None
        self._unknown_types_seen: set[str] = set()

        # 모듈 초기화 시점 경고: 시스템에 ANTHROPIC_API_KEY가 존재하면
        # 의도치 않은 종량 과금 위험이 있으므로 로그를 남긴다.
        if os.environ.get("ANTHROPIC_API_KEY"):
            print(
                "[ClaudeCode 경고] 시스템 환경변수에 ANTHROPIC_API_KEY가 존재합니다. "
                "기본 설정에서는 subprocess에 전달되지 않지만(구독 인증 사용), "
                "의도치 않은 API 과금을 막으려면 해당 변수를 제거하는 것을 권장합니다."
            )

    # ── 세션 ──────────────────────────────────────────────────────────────────

    @property
    def session_id(self) -> Optional[str]:
        return self._session_id

    def reset_session(self) -> None:
        """새 대화 시작 — 다음 호출부터 --resume을 붙이지 않는다."""
        self._session_id = None
        print("[ClaudeCode] 세션을 초기화했습니다.")

    # ── env / cmd 구성 ────────────────────────────────────────────────────────

    def _build_env(self, s: ClaudeCodeSettings) -> dict[str, str]:
        """화이트리스트 기반 subprocess 환경 구성.

        ANTHROPIC_API_KEY는 allow_api_key_billing=True를 명시한 경우에만
        통과시키며, 이때도 매 호출 경고 로그를 남긴다.
        """
        env = {k: v for k, v in os.environ.items() if k in ENV_WHITELIST}
        if s.allow_api_key_billing and os.environ.get("ANTHROPIC_API_KEY"):
            env["ANTHROPIC_API_KEY"] = os.environ["ANTHROPIC_API_KEY"]
            print(
                "[ClaudeCode 경고] allow_api_key_billing=True — 이번 호출은 "
                "구독이 아닌 API 종량 요금으로 청구될 수 있습니다."
            )
        return env

    def _build_cmd(
        self,
        base: list[str],
        s: ClaudeCodeSettings,
        system: Optional[str],
        resume_id: Optional[str],
    ) -> list[str]:
        """claude 실행 커맨드 구성. 프롬프트는 stdin으로 전달(인용부호 문제 방지)."""
        cmd = base + [
            "-p",
            "--output-format", "stream-json",
            "--verbose",                       # -p + stream-json 조합에 필수
            "--include-partial-messages",      # text_delta 단위 실시간 스트리밍
            "--strict-mcp-config",             # 사용자 MCP 커넥터 자동 로드 방지
            "--max-turns", str(s.max_turns),   # 폭주 방지: 호출당 최대 턴 수
        ]
        # 허용 도구 제한 (빈 리스트면 도구 전체 비활성화)
        cmd += ["--tools", ",".join(s.allowed_tools)]
        if s.allowed_tools:
            cmd += ["--allowedTools", ",".join(s.allowed_tools)]
        if s.model:
            cmd += ["--model", s.model]
        if resume_id:
            cmd += ["--resume", resume_id]
        if system:
            cmd += ["--append-system-prompt", system]
        return cmd

    # ── 상태 조회 (설정 모달용) ───────────────────────────────────────────────

    async def status(self, force: bool = False) -> dict:
        det = await self._detector.detect(force=force)
        s = self._store.load()
        return {
            **det.to_dict(),
            "model": s.model,
            "session_id": self._session_id,
            "allow_api_key_billing": s.allow_api_key_billing,
        }

    # ── 메인: 스트리밍 호출 ───────────────────────────────────────────────────

    async def stream(
        self,
        prompt: str,
        system: Optional[str] = None,
        resume: bool = True,
    ) -> AsyncGenerator[CCEvent, None]:
        """claude -p 1회 호출을 타입 이벤트 스트림으로 변환.

        미설치/미로그인/예산초과는 spawn 없이 CCStatusEvent만 yield하고 종료.
        """
        s = self._store.load()

        # ── 사전 게이트 ① CLI 탐지 ──
        det = await self._detector.detect()
        if not det.installed:
            yield CCStatusEvent(ClaudeStatus.NOT_INSTALLED,
                                _STATUS_KO[ClaudeStatus.NOT_INSTALLED])
            return
        if det.logged_in is False:
            yield CCStatusEvent(ClaudeStatus.NOT_LOGGED_IN,
                                _STATUS_KO[ClaudeStatus.NOT_LOGGED_IN])
            return

        # ── 사전 게이트 ② 호출 예산 ──
        ok, why = self._tracker.check_budget(s.hourly_call_limit, s.daily_call_limit)
        if not ok:
            yield CCStatusEvent(ClaudeStatus.BUDGET_EXCEEDED, why)
            return

        base = self._detector.base_cmd_for(det.path, det.via_wsl)
        resume_id = self._session_id if resume else None
        cmd = self._build_cmd(base, s, system, resume_id)

        async with self._sem:
            cost_before = self._tracker.today()["cost_usd"]
            self._tracker.record_call()

            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=self._build_env(s),
                    cwd=str(paths.workspace_dir()),
                    creationflags=_creationflags(),
                )
            except OSError as e:
                yield CCStatusEvent(ClaudeStatus.ERROR,
                                    f"claude 프로세스 실행 실패: {e}")
                return

            # stderr는 별도 태스크로 계속 드레인 (파이프 버퍼 데드락 방지)
            stderr_task = asyncio.create_task(proc.stderr.read())

            # 프롬프트는 stdin으로 전달
            try:
                proc.stdin.write(prompt.encode("utf-8"))
                await proc.stdin.drain()
                proc.stdin.close()
            except (BrokenPipeError, ConnectionResetError):
                pass

            deadline = time.monotonic() + s.timeout_sec
            got_result = False
            limit_reset_at: Optional[int] = None

            try:
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise asyncio.TimeoutError
                    line = await asyncio.wait_for(proc.stdout.readline(),
                                                  timeout=remaining)
                    if not line:
                        break

                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    for ev in self._dispatch_line(data):
                        if isinstance(ev, CCResult):
                            got_result = True
                            if ev.is_error or ev.subtype != "success":
                                # 한도 도달 vs 일반 오류 구분
                                status_ev = self._classify_failure(
                                    ev, limit_reset_at)
                                yield status_ev
                            else:
                                cost_after = self._tracker.record_result(
                                    cost_usd=ev.total_cost_usd,
                                    input_tokens=ev.input_tokens,
                                    output_tokens=ev.output_tokens,
                                    cache_read_tokens=ev.cache_read_tokens,
                                    cache_creation_tokens=ev.cache_creation_tokens,
                                    num_turns=ev.num_turns,
                                    duration_ms=ev.duration_ms,
                                    session_id=ev.session_id,
                                )
                                yield ev
                                if self._tracker.crossed_warn_threshold(
                                    cost_before, cost_after,
                                    s.daily_cost_warn_usd,
                                ):
                                    yield CCWarning(
                                        f"오늘 누적 추정 비용이 "
                                        f"${s.daily_cost_warn_usd:.2f} 임계값을 "
                                        f"초과했습니다 (현재 ${cost_after:.2f})."
                                    )
                        elif isinstance(ev, tuple):
                            # rate_limit_event 내부 신호
                            limit_reset_at = ev[1] or limit_reset_at
                        else:
                            yield ev

            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                stderr_task.cancel()
                yield CCStatusEvent(
                    ClaudeStatus.TIMEOUT,
                    f"호출이 {s.timeout_sec:.0f}초 제한을 초과해 강제 종료되었습니다.",
                )
                return

            rc = await proc.wait()
            stderr_text = ""
            try:
                stderr_text = (await stderr_task).decode("utf-8", errors="replace")
            except asyncio.CancelledError:
                pass

            # result 이벤트 없이 종료 → stderr 기반으로 구조화된 오류 전달
            if not got_result:
                yield self._classify_no_result(rc, stderr_text, limit_reset_at)

    # ── 라인 → 이벤트 변환 ────────────────────────────────────────────────────

    def _dispatch_line(self, data: dict) -> list:
        """stream-json 한 줄을 이벤트 목록으로 변환 (미지 타입은 무시)."""
        t = data.get("type")
        events: list = []

        if t == "system":
            if data.get("subtype") == "init":
                sid = data.get("session_id") or ""
                if sid:
                    self._session_id = sid
                src = data.get("apiKeySource", "none") or "none"
                self._detector.note_api_key_source(src)
                if src != "none":
                    print(
                        f"[ClaudeCode 경고] 이번 호출 인증이 구독(OAuth)이 아닌 "
                        f"'{src}'입니다 — API 종량 과금이 발생할 수 있습니다."
                    )
                events.append(CCInit(
                    session_id=sid,
                    model=data.get("model", ""),
                    api_key_source=src,
                    tools=data.get("tools", []) or [],
                ))

        elif t == "stream_event":
            ev = data.get("event") or {}
            if ev.get("type") == "content_block_delta":
                delta = ev.get("delta") or {}
                if delta.get("type") == "text_delta" and delta.get("text"):
                    events.append(CCTextDelta(delta["text"]))

        elif t == "assistant":
            # 텍스트는 stream_event에서만 방출 (중복 방지) — 여기서는 tool_use만
            for block in (data.get("message") or {}).get("content", []) or []:
                if block.get("type") == "tool_use":
                    preview = json.dumps(block.get("input") or {},
                                         ensure_ascii=False)[:200]
                    events.append(CCToolUse(name=block.get("name", ""),
                                            input_preview=preview))

        elif t == "user":
            for block in (data.get("message") or {}).get("content", []) or []:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    events.append(CCToolResult(
                        tool_use_id=block.get("tool_use_id", ""),
                        is_error=bool(block.get("is_error", False)),
                    ))

        elif t == "rate_limit_event":
            status = data.get("status")
            if status and status != "allowed":
                events.append(("__limit__", data.get("resetsAt")))

        elif t == "result":
            sid = data.get("session_id") or ""
            if sid:
                self._session_id = sid
            usage = data.get("usage") or {}
            events.append(CCResult(
                subtype=data.get("subtype", ""),
                is_error=bool(data.get("is_error", False)),
                result_text=str(data.get("result", "") or ""),
                total_cost_usd=float(data.get("total_cost_usd", 0.0) or 0.0),
                num_turns=int(data.get("num_turns", 0) or 0),
                duration_ms=int(data.get("duration_ms", 0) or 0),
                input_tokens=int(usage.get("input_tokens", 0) or 0),
                output_tokens=int(usage.get("output_tokens", 0) or 0),
                cache_read_tokens=int(usage.get("cache_read_input_tokens", 0) or 0),
                cache_creation_tokens=int(
                    usage.get("cache_creation_input_tokens", 0) or 0),
                session_id=sid,
            ))

        elif t and t not in self._unknown_types_seen:
            # stream-json 포맷 변동 내성: 미지 타입은 1회 로그 후 무시
            self._unknown_types_seen.add(t)
            print(f"[ClaudeCode] 알 수 없는 이벤트 타입 무시: {t}")

        return events

    # ── 실패 분류 ─────────────────────────────────────────────────────────────

    def _classify_failure(self, result: CCResult,
                          limit_reset_at: Optional[int]) -> CCStatusEvent:
        """result 이벤트가 오류일 때 한도 도달/로그인/일반 오류로 구분."""
        text = result.result_text or ""

        m = _LIMIT_RE.search(text)
        if m or limit_reset_at or (_LIMIT_HINT_RE.search(text) and "reset" in text.lower()):
            reset_at = limit_reset_at
            if m and m.group(1):
                reset_at = int(m.group(1))
            return CCStatusEvent(
                ClaudeStatus.LIMIT_REACHED,
                f"구독 사용 한도에 도달했습니다 — {_fmt_reset(reset_at)} 리셋까지 "
                f"대기가 필요합니다. (자동 재시도/유료 폴백은 하지 않습니다)",
                reset_at=reset_at,
            )

        if _LOGIN_RE.search(text):
            self._detector.note_login_failure()
            return CCStatusEvent(ClaudeStatus.NOT_LOGGED_IN,
                                 _STATUS_KO[ClaudeStatus.NOT_LOGGED_IN])

        return CCStatusEvent(
            ClaudeStatus.ERROR,
            f"Claude Code 호출 오류 ({result.subtype}): {text[:300]}",
        )

    def _classify_no_result(self, rc: int, stderr_text: str,
                            limit_reset_at: Optional[int]) -> CCStatusEvent:
        """result 이벤트 없이 프로세스가 종료된 경우의 구조화 오류."""
        tail = stderr_text.strip()[-2000:]

        m = _LIMIT_RE.search(tail)
        if m or limit_reset_at:
            reset_at = int(m.group(1)) if (m and m.group(1)) else limit_reset_at
            return CCStatusEvent(
                ClaudeStatus.LIMIT_REACHED,
                f"구독 사용 한도에 도달했습니다 — {_fmt_reset(reset_at)} 리셋까지 "
                f"대기가 필요합니다.",
                reset_at=reset_at,
            )

        if _LOGIN_RE.search(tail):
            self._detector.note_login_failure()
            return CCStatusEvent(ClaudeStatus.NOT_LOGGED_IN,
                                 _STATUS_KO[ClaudeStatus.NOT_LOGGED_IN])

        return CCStatusEvent(
            ClaudeStatus.ERROR,
            f"claude 프로세스 비정상 종료 (exit={rc}): {tail[:300] or '출력 없음'}",
        )
