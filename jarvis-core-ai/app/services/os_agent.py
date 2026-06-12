"""
os_agent.py — JARVIS OS Control Execution Engine
════════════════════════════════════════════════════════════════════════════════
역할:
  · LLM이 생성한 JSON Action Plan을 파싱하고 pyautogui로 순차 실행
  · 실행 중 각 원자 명령마다 실시간 로그를 JSON 라인으로 yield
  · Spring Boot 대시보드 WebSocket으로 전송 가능한 NDJSON 스트림 출력

지원 액션 타입:
  click · write · press · hotkey · wait · screenshot · scroll · open_url

NDJSON 스트림 이벤트 타입:
  plan     → 파싱된 계획 요약 (실행 전 1회)
  start    → 개별 액션 시작
  done     → 개별 액션 완료
  error    → 개별 액션 실패
  finish   → 전체 실행 완료 요약

Public API:
  agent.plan(user_command)           → ActionPlan
  agent.execute_stream(plan)         → AsyncGenerator[str, None]  (NDJSON 라인)
  agent.run_stream(user_command)     → AsyncGenerator[str, None]  (plan + execute)
════════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
import sys
import time
import webbrowser
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncGenerator, Optional

from app.config import settings

# ── 경로 ─────────────────────────────────────────────────────────────────────
_PROMPTS_DIR    = Path(__file__).parent.parent.parent / "prompts"
_SCREENSHOT_DIR = Path("./data/screenshots")
_SCRIPTS_DIR    = Path(settings.os_scripts_dir)

# ── 허용된 액션 타입 ──────────────────────────────────────────────────────────
_VALID_TYPES = frozenset(
    {"click", "write", "press", "hotkey", "wait", "screenshot", "scroll", "open_url", "run_script"}
)

# run_script 실행 제한 시간 기본값(초)
_DEFAULT_SCRIPT_TIMEOUT = 120.0

# ── 시각화용 딜레이 ───────────────────────────────────────────────────────────
# 각 액션 사이에 두어 사람이 화면을 따라갈 수 있게 하는 의도적 지연(초)
_ACTION_DELAY = 0.4
# 글자 단위 타이핑 시 한 글자당 지연(초)
_TYPE_CHAR_DELAY = 0.35

# ── 위험 작업 패턴 (패턴, 사유) ────────────────────────────────────────────────
_DANGEROUS_PATTERNS: list[tuple[str, str]] = [
    # 파일/폴더 삭제
    (r"\b(del|rmdir|rd\s+/s|rm\s+-r|shutil\.rmtree|삭제|지우|제거|format)\b",
     "파일 또는 폴더 삭제 작업"),
    # 시스템 핵심 경로
    (r"(system32|windows\\system|c:\\windows|c:/windows|program files)",
     "Windows 시스템 경로 접근"),
    # 레지스트리
    (r"\b(regedit|registry|레지스트리|reg\s+delete|reg\s+add)\b",
     "레지스트리 수정"),
    # 프로세스 강제 종료
    (r"\b(taskkill|tskill|강제.*종료|kill\s+-9|terminate)\b",
     "프로세스 강제 종료"),
    # 대규모 파일 작업
    (r"(\*\.\*|전체.*삭제|모든.*파일.*삭제|mass\s+delete)",
     "대규모 파일 작업"),
    # 관리자 권한 실행
    (r"\b(runas|sudo|관리자.*실행|run\s+as\s+admin)\b",
     "관리자 권한 명령 실행"),
    # 네트워크·방화벽 설정
    (r"\b(netsh|방화벽.*해제|firewall.*off|ipconfig.*release)\b",
     "네트워크 또는 방화벽 설정 변경"),
    # 디스크 포맷
    (r"\b(format\s+[a-z]:?|diskpart|fdisk)\b",
     "디스크 포맷 또는 파티션 작업"),
    # 시스템 종료/재부팅/절전
    (r"\b(shutdown|reboot|재부팅|절전|시스템\s*종료)\b",
     "시스템 종료/재부팅/절전 작업"),
    # 이메일 전송
    (r"(이메일|메일|email|gmail).{0,30}(전송|발송|보내|send)|"
     r"(전송|발송|send).{0,30}(이메일|메일|email|gmail)",
     "이메일 전송 작업"),
]


# ══════════════════════════════════════════════════════════════════════════════
# 1. 데이터 모델
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class OsAction:
    type: str
    param: Any          # 타입별로 상이 (str | float | dict | list | None)
    index: int = 0      # 전체 액션 배열 내 인덱스 (실행 시점에 주입)


@dataclass
class ActionPlan:
    thought: str
    actions: list[OsAction]
    raw: dict      = field(default_factory=dict, repr=False)
    dangerous: bool = False
    risk_reason: str = ""


@dataclass
class ActionResult:
    index: int
    action_type: str
    success: bool
    log: str                        # 사람이 읽을 수 있는 한국어 설명
    duration_ms: float
    error: str              = ""
    screenshot_path: str    = ""    # screenshot 액션일 때만 설정
    output: str             = ""    # run_script 액션의 stdout/stderr

    def to_ndjson(self, event: str) -> str:
        """NDJSON 한 줄 문자열로 직렬화."""
        d = {
            "event":         event,
            "index":         self.index,
            "action_type":   self.action_type,
            "success":       self.success,
            "log":           self.log,
            "duration_ms":   round(self.duration_ms, 1),
        }
        if self.error:
            d["error"] = self.error
        if self.screenshot_path:
            d["screenshot_path"] = self.screenshot_path
        if self.output:
            d["output"] = self.output
        return json.dumps(d, ensure_ascii=False)


# ══════════════════════════════════════════════════════════════════════════════
# 2. 로그 메시지 생성기
# ══════════════════════════════════════════════════════════════════════════════

def _make_log(action: OsAction) -> str:
    """액션 타입과 파라미터를 한국어 자연어 설명으로 변환."""
    t, p = action.type, action.param

    if t == "click":
        if isinstance(p, dict):
            btn    = p.get("button", "left")
            clicks = p.get("clicks", 1)
            x, y   = p.get("x"), p.get("y")
            btn_ko = {"left": "왼쪽", "right": "오른쪽", "middle": "가운데"}.get(btn, btn)
            dbl    = " 더블" if clicks == 2 else ""
            pos    = f" ({x}, {y}) 좌표를" if x is not None else " 현재 위치를"
            return f"자비스가{pos}{dbl} {btn_ko} 클릭합니다..."
        return "자비스가 마우스를 클릭합니다..."

    if t == "write":
        preview = str(p)[:20] + ("..." if len(str(p)) > 20 else "")
        return f"자비스가 '{preview}'를 입력합니다..."

    if t == "press":
        return f"자비스가 [{str(p).upper()}] 키를 누릅니다..."

    if t == "hotkey":
        combo = "+".join(str(k).upper() for k in (p if isinstance(p, list) else [p]))
        return f"자비스가 [{combo}] 단축키를 누릅니다..."

    if t == "wait":
        return f"자비스가 {p}초 대기합니다... (프로그램 응답 대기)"

    if t == "screenshot":
        return "자비스가 현재 화면을 캡처합니다..."

    if t == "scroll":
        direction = "아래로" if not isinstance(p, dict) else \
                    ("위로" if p.get("direction") == "up" else "아래로")
        clicks = p.get("clicks", 3) if isinstance(p, dict) else 3
        return f"자비스가 스크롤을 {direction} {clicks}칸 내립니다..."

    if t == "open_url":
        short = str(p)[:50] + ("..." if len(str(p)) > 50 else "")
        return f"자비스가 브라우저에서 '{short}'를 엽니다..."

    if t == "run_script":
        name = p.get("name", "script") if isinstance(p, dict) else "script"
        return f"자비스가 '{name}.py' 스크립트를 작성하고 실행합니다..."

    return f"자비스가 [{t}] 액션을 실행합니다..."


# ══════════════════════════════════════════════════════════════════════════════
# 3. 동기 실행 함수 (pyautogui는 동기 — thread executor에서 호출)
# ══════════════════════════════════════════════════════════════════════════════

def _sync_execute(action: OsAction) -> ActionResult:
    """단일 액션을 동기적으로 실행. 반드시 thread executor 안에서 호출해야 함."""
    import pyautogui

    pyautogui.FAILSAFE = True   # 마우스를 화면 모서리로 이동하면 즉시 중단
    pyautogui.PAUSE   = 0.05   # 각 pyautogui 호출 사이 50ms 자동 대기

    t, p = action.type, action.param
    t_start = time.perf_counter()
    screenshot_path = ""
    output = ""

    try:
        # ── click ──────────────────────────────────────────────────────────
        if t == "click":
            kwargs: dict[str, Any] = {"button": "left", "clicks": 1}
            if isinstance(p, dict):
                if "x" in p and "y" in p:
                    kwargs["x"] = p["x"]
                    kwargs["y"] = p["y"]
                kwargs["button"]  = p.get("button", "left")
                kwargs["clicks"]  = p.get("clicks", 1)
            pyautogui.click(**kwargs)

        # ── write ──────────────────────────────────────────────────────────
        elif t == "write":
            # ASCII/한글 구분 없이 항상 클립보드 붙여넣기 사용.
            # pyautogui.write()는 실제 키 스캔코드를 입력하므로 현재 OS의
            # 한/영 IME 상태에 따라 ASCII 입력이 한글로 조합되는 등 오작동할
            # 수 있다. 클립보드 붙여넣기(Ctrl+V)는 IME 조합을 거치지 않고
            # 클립보드의 유니코드 텍스트를 그대로 입력하므로 IME 상태와
            # 무관하게 항상 정확하다. 글자 단위로 붙여넣어 타이핑 효과는 유지한다.
            import pyperclip
            text = str(p)
            for ch in text:
                pyperclip.copy(ch)
                pyautogui.hotkey("ctrl", "v")
                time.sleep(_TYPE_CHAR_DELAY)

        # ── press ──────────────────────────────────────────────────────────
        elif t == "press":
            pyautogui.press(str(p))

        # ── hotkey ─────────────────────────────────────────────────────────
        elif t == "hotkey":
            keys = p if isinstance(p, list) else [str(p)]
            pyautogui.hotkey(*[str(k) for k in keys])

        # ── wait ───────────────────────────────────────────────────────────
        elif t == "wait":
            time.sleep(float(p))

        # ── screenshot ─────────────────────────────────────────────────────
        elif t == "screenshot":
            _SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
            ts       = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            filename = f"jarvis_{ts}.png"
            save_path = _SCREENSHOT_DIR / filename
            img = pyautogui.screenshot()
            img.save(str(save_path))
            screenshot_path = str(save_path)

        # ── scroll ─────────────────────────────────────────────────────────
        elif t == "scroll":
            clicks    = 3
            direction = "down"
            if isinstance(p, dict):
                clicks    = int(p.get("clicks", 3))
                direction = p.get("direction", "down")
            amount = -clicks if direction == "down" else clicks
            pyautogui.scroll(amount)

        # ── open_url ───────────────────────────────────────────────────────
        elif t == "open_url":
            webbrowser.open(str(p))

        # ── run_script ─────────────────────────────────────────────────────
        elif t == "run_script":
            if not isinstance(p, dict) or not p.get("code"):
                raise ValueError("run_script에는 'code' 파라미터(Python 소스)가 필요합니다.")

            name = re.sub(r"[^a-zA-Z0-9_\-]", "_", str(p.get("name") or "")).strip("_")
            if not name:
                name = f"script_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"

            _SCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
            script_path = _SCRIPTS_DIR / f"{name}.py"
            script_path.write_text(p["code"], encoding="utf-8")

            timeout = float(p.get("timeout", _DEFAULT_SCRIPT_TIMEOUT))
            proc = subprocess.run(
                [sys.executable, str(script_path)],
                capture_output=True,
                text=True,
                timeout=timeout,
                encoding="utf-8",
                errors="replace",
            )
            output = (proc.stdout or "") + (proc.stderr or "")
            if proc.returncode != 0:
                raise RuntimeError(f"스크립트 실행 실패 (exit {proc.returncode}): {output[-1000:]}")

        else:
            raise ValueError(f"지원하지 않는 액션 타입: {t!r}")

    except Exception as e:
        duration = (time.perf_counter() - t_start) * 1000
        return ActionResult(
            index          = action.index,
            action_type    = t,
            success        = False,
            log            = _make_log(action),
            duration_ms    = duration,
            error          = str(e),
            screenshot_path= screenshot_path,
            output         = output,
        )

    duration = (time.perf_counter() - t_start) * 1000
    return ActionResult(
        index          = action.index,
        action_type    = t,
        success        = True,
        log            = _make_log(action),
        duration_ms    = duration,
        screenshot_path= screenshot_path,
        output         = output,
    )


# ══════════════════════════════════════════════════════════════════════════════
# 4. JSON 파싱 및 검증
# ══════════════════════════════════════════════════════════════════════════════

class _PlanParser:

    @staticmethod
    def _strip_markdown(text: str) -> str:
        text = text.strip()
        text = re.sub(r"```(?:json)?", "", text)
        text = text.strip("`").strip()
        return text

    @staticmethod
    def _extract_json(text: str) -> dict:
        # 가장 바깥쪽 { } 추출
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise ValueError("LLM 응답에서 JSON 객체를 찾을 수 없습니다.")
        return json.loads(match.group())

    @staticmethod
    def _assess_danger(thought: str, actions: list[OsAction]) -> tuple[bool, str]:
        """thought + 모든 액션 파라미터를 위험 패턴과 대조. (위험 여부, 사유) 반환."""
        targets: list[str] = [thought.lower()]
        for action in actions:
            p = action.param
            if isinstance(p, str):
                targets.append(p.lower())
            elif isinstance(p, dict):
                targets.extend(str(v).lower() for v in p.values())
            elif isinstance(p, list):
                targets.extend(str(v).lower() for v in p)

        combined = " ".join(targets)
        for pattern, reason in _DANGEROUS_PATTERNS:
            if re.search(pattern, combined, re.IGNORECASE):
                return True, reason
        return False, ""

    @classmethod
    def parse(cls, raw_text: str) -> ActionPlan:
        cleaned = cls._strip_markdown(raw_text)
        data    = cls._extract_json(cleaned)

        thought = data.get("thought", "")
        raw_actions: list[dict] = data.get("actions", [])

        if not isinstance(raw_actions, list):
            raise ValueError(f"'actions' 필드가 배열이어야 합니다: {type(raw_actions)}")

        actions: list[OsAction] = []
        for i, raw in enumerate(raw_actions):
            action_type = raw.get("type", "").lower().strip()
            if action_type not in _VALID_TYPES:
                print(
                    f"[OsAgent] 경고: 인덱스 {i}의 알 수 없는 타입 '{action_type}' — 건너뜁니다.",
                    file=sys.stderr,
                )
                continue
            actions.append(OsAction(
                type  = action_type,
                param = raw.get("param"),
                index = i,
            ))

        if not actions:
            raise ValueError("파싱된 액션이 없습니다.")

        dangerous, risk_reason = cls._assess_danger(thought, actions)
        return ActionPlan(
            thought     = thought,
            actions     = actions,
            raw         = data,
            dangerous   = dangerous,
            risk_reason = risk_reason,
        )


_parser = _PlanParser()


# ══════════════════════════════════════════════════════════════════════════════
# 5. OS Agent 클래스
# ══════════════════════════════════════════════════════════════════════════════

def _resolve_screen_size() -> tuple[int, int]:
    """화면 해상도. 설정값이 있으면 사용, 없으면 pyautogui로 감지, 실패 시 1920x1080."""
    if settings.os_screen_width and settings.os_screen_height:
        return settings.os_screen_width, settings.os_screen_height
    try:
        import pyautogui
        return pyautogui.size()
    except Exception:
        return 1920, 1080


def _render_env_info(system: str) -> str:
    """프롬프트 내 실행 환경 placeholder를 실제 설정값으로 치환."""
    from app.config import resolved_chrome_user_data_dir, resolved_downloads_dir

    width, height = _resolve_screen_size()
    replacements = {
        "{{SCREEN_WIDTH}}":          str(width),
        "{{SCREEN_HEIGHT}}":         str(height),
        "{{CHROME_USER_DATA_DIR}}":  resolved_chrome_user_data_dir(),
        "{{CAPTURES_DIR}}":          str(Path(settings.os_captures_dir).resolve()),
        "{{DOWNLOADS_DIR}}":         resolved_downloads_dir(),
        "{{SCRIPTS_DIR}}":           str(Path(settings.os_scripts_dir).resolve()),
        "{{PYTHON_EXECUTABLE}}":     sys.executable,
    }
    for placeholder, value in replacements.items():
        system = system.replace(placeholder, value)
    return system


class OsAgent:
    """LLM 계획 생성 → pyautogui 실행 → NDJSON 스트리밍 로그."""

    # ── 계획 생성 ──────────────────────────────────────────────────────────────

    async def plan(self, user_command: str) -> ActionPlan:
        """유저 명령 → LLM 호출 → ActionPlan 반환."""
        from app.services.llm_manager import manager
        from app.services.agent_router import PromptSet, RoutingResult

        system_path = _PROMPTS_DIR / "os_agent.md"
        if not system_path.exists():
            raise FileNotFoundError(f"OS 에이전트 프롬프트 없음: {system_path}")

        system = _render_env_info(system_path.read_text(encoding="utf-8"))

        # agent_router를 우회하고 OS Agent 전용 시스템 프롬프트를 직접 주입
        routing = RoutingResult(
            agent_key   = "os_agent",
            agent_name  = "OS Control Agent",
            confidence  = 1.0,
            method      = "direct",
            reasoning   = "OS 제어 요청 — 직접 라우팅",
        )
        prompt_set = PromptSet(
            system     = system,
            messages   = [{"role": "user", "content": user_command}],
            agent_key  = "os_agent",
            agent_name = "OS Control Agent",
            routing    = routing,
        )

        response = await manager.run(prompt_set, max_retries=1)
        return _parser.parse(response.text)

    # ── 실행 스트림 ────────────────────────────────────────────────────────────

    async def execute_stream(
        self,
        plan: ActionPlan,
        stop_on_error: bool = False,
    ) -> AsyncGenerator[str, None]:
        """ActionPlan을 실행하고 각 단계를 NDJSON 라인으로 yield.

        Args:
            plan:           plan() 메서드가 반환한 ActionPlan
            stop_on_error:  True이면 액션 실패 시 즉시 중단

        Yields:
            JSON 문자열 한 줄 (이벤트 타입: plan / start / done / error / finish)
        """
        loop = asyncio.get_event_loop()

        # ── plan 이벤트 ──
        yield json.dumps({
            "event":        "plan",
            "thought":      plan.thought,
            "action_count": len(plan.actions),
        }, ensure_ascii=False)

        success_count = 0
        fail_count    = 0

        for action in plan.actions:
            # ── 의도적 지연 — 동작 사이에 사람이 화면을 따라갈 시간을 준다 ──
            if action.index > 0:
                await asyncio.sleep(_ACTION_DELAY)

            # ── start 이벤트 ──
            yield json.dumps({
                "event":       "start",
                "index":       action.index,
                "action_type": action.type,
                "log":         _make_log(action),
            }, ensure_ascii=False)

            # 동기 pyautogui 호출을 thread executor로 감싸서 이벤트 루프 차단 방지
            result: ActionResult = await loop.run_in_executor(
                None,
                _sync_execute,
                action,
            )

            if result.success:
                success_count += 1
                yield result.to_ndjson("done")
            else:
                fail_count += 1
                yield result.to_ndjson("error")
                if stop_on_error:
                    yield json.dumps({
                        "event":   "finish",
                        "aborted": True,
                        "reason":  f"인덱스 {action.index} 실패로 중단: {result.error}",
                        "success": success_count,
                        "failed":  fail_count,
                        "total":   len(plan.actions),
                    }, ensure_ascii=False)
                    return

        # ── finish 이벤트 ──
        yield json.dumps({
            "event":   "finish",
            "aborted": False,
            "success": success_count,
            "failed":  fail_count,
            "total":   len(plan.actions),
        }, ensure_ascii=False)

    # ── 계획 + 실행 통합 스트림 ────────────────────────────────────────────────

    async def run_stream(
        self,
        user_command: str,
        stop_on_error: bool = False,
    ) -> AsyncGenerator[str, None]:
        """유저 자연어 명령 → LLM 계획 → 실행 → NDJSON 스트림.

        Spring Boot WebSocket 혹은 FastAPI StreamingResponse에서 직접 소비 가능.
        """
        # ── planning 이벤트 ──
        yield json.dumps({
            "event":   "planning",
            "message": f"'{user_command[:60]}' 분석 중...",
        }, ensure_ascii=False)

        try:
            plan = await self.plan(user_command)
        except Exception as e:
            yield json.dumps({
                "event":   "error",
                "message": f"액션 계획 생성 실패: {e}",
            }, ensure_ascii=False)
            return

        # 위험 작업 감지 → 사용자 확인 없이 실행하지 않음
        if plan.dangerous:
            yield json.dumps({
                "event":        "danger",
                "thought":      plan.thought,
                "risk_reason":  plan.risk_reason,
                "action_count": len(plan.actions),
                "actions": [
                    {"type": a.type, "param": a.param}
                    for a in plan.actions
                ],
            }, ensure_ascii=False)
            return

        async for line in self.execute_stream(plan, stop_on_error=stop_on_error):
            yield line


# ══════════════════════════════════════════════════════════════════════════════
# 6. 모듈 레벨 싱글턴
# ══════════════════════════════════════════════════════════════════════════════

agent = OsAgent()
