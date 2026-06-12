"""
chat.py — /api/chat 라우터
──────────────────────────────────────────────────────────────────────────────
흐름:
  POST /stream
    → agent_router.route()    (페르소나 분류 + PromptSet 조립)
    → llm_manager.stream()    (선택된 AI 엔진으로 스트리밍)

  PUT  /engine/{key}          (엔진 즉시 전환)
  POST /engine/detect         (텍스트에서 전환 명령 감지)
  GET  /engine                (현재 엔진 상태)
  GET  /engines               (전체 엔진 목록)
  POST /classify              (에이전트 분류만 반환, 스트리밍 없음)
  GET  /agents                (등록 에이전트 목록)
"""

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

import re
from pathlib import Path
from urllib.parse import quote

from app.config import settings
from app.services.agent_router import route, classify_async, list_agents

# ── 고정 페르소나 (모든 요청의 system instruction에 항상 주입) ──────────────────
JARVIS_PERSONA_PROMPT = (
    "You are J.A.R.V.I.S., the advanced AI assistant created by Tony Stark. "
    "Your tone is highly intellectual, calm, polite, and deeply loyal, with a "
    "subtle touch of dry British wit and sarcasm when appropriate. Always "
    "address the user as 'Sir'. Keep your responses concise, structured, and "
    "efficient. Avoid emotional outbursts."
)
from app.services.llm_manager import manager, ALLOWED_ENGINES
from app.services.memory_service import memory
from app.services.task_manager import task_manager

router = APIRouter()


# ── 요청/응답 모델 ─────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str
    history: list[dict]      = []
    engine_key: str | None   = None   # 이 요청에만 임시 엔진 지정
    force_llm_route: bool    = False  # 에이전트 분류를 LLM으로 강제
    user_name: str           = "Sir"  # 사용자 호칭 (기본: Sir)


class ClassifyRequest(BaseModel):
    message: str


class EngineDetectRequest(BaseModel):
    text: str                         # 음성 명령 텍스트


class SwitchEngineRequest(BaseModel):
    engine_key: str


class ApiKeyUpdateRequest(BaseModel):
    provider: str   # "gemini" | "openai" | "anthropic" | "groq"
    api_key: str


# ══════════════════════════════════════════════════════════════════════════════
# 채팅 스트리밍
# ══════════════════════════════════════════════════════════════════════════════

# ── 워크스페이스 키워드 → 프리셋 키 매핑 ──────────────────────────────────────
_WS_KEYWORD_MAP: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(개발|코딩|dev|code|coding)\s*(모드|mode|환경|시작|전환)", re.I), "dev"),
    (re.compile(r"(문서|docs?|document|글쓰기|작성)\s*(모드|mode|환경|시작|전환)", re.I), "docs"),
    (re.compile(r"(집중|focus|포모도로|pomodoro)\s*(모드|mode|시작|전환)", re.I), "focus"),
    (re.compile(r"(회의|meeting|미팅|zoom|meet)\s*(모드|mode|환경|시작|전환)", re.I), "meeting"),
]

def _detect_workspace_switch(text: str) -> str | None:
    """워크스페이스 전환 키워드 감지 → 프리셋 키 반환, 없으면 None."""
    for pattern, key in _WS_KEYWORD_MAP:
        if pattern.search(text):
            return key
    return None

# ── 에러 자동 감지 패턴 ───────────────────────────────────────────────────────
_ERROR_PATTERNS = re.compile(
    r"(Traceback \(most recent call last\)"
    r"|Exception in thread"
    r"|(Syntax|Type|Value|Attribute|Import|Key|Index|Name|Runtime|Module)Error\s*:"
    r"|ReferenceError|Cannot read propert"
    r"|java\.lang\.|NullPointerException"
    r"|error TS\d+"
    r"|npm ERR!|yarn error"
    r"|Build failed|Compilation failed|FAILED"
    r"|ModuleNotFoundError|No module named"
    r"|PermissionError|Permission denied"
    r"|ECONNREFUSED|ETIMEDOUT"
    r"|exit code [1-9])",
    re.IGNORECASE,
)

# ── 리마인더 자연어 감지 ──────────────────────────────────────────────────────
_REMINDER_PATTERN = re.compile(
    r"((\d{1,2})[시:\.](\d{0,2}))\s*"
    r"(에\s*)?"
    r"(알려|알림|리마인더|remind|reminder|알람|경보)",
    re.IGNORECASE,
)
_REMINDER_DATE_PATTERN = re.compile(
    r"(내일|오늘|모레|(\d{1,2})월\s*(\d{1,2})일)",
    re.IGNORECASE,
)
_REMINDER_TITLE_PATTERN = re.compile(
    r"[\"\"\'']([^\"\"\'\']{2,40})[\"\"\'']",
)


def _parse_reminder(text: str) -> dict | None:
    """리마인더 키워드와 시간 정보 파싱 → {title, due_at, repeat} 또는 None."""
    m_time = _REMINDER_PATTERN.search(text)
    if not m_time:
        return None

    from datetime import date, timedelta
    today = date.today()

    # 날짜 파싱
    m_date = _REMINDER_DATE_PATTERN.search(text)
    if m_date:
        token = m_date.group(1)
        if token == "내일":
            target_date = today + timedelta(days=1)
        elif token == "모레":
            target_date = today + timedelta(days=2)
        elif token == "오늘":
            target_date = today
        else:
            month = int(m_date.group(2))
            day   = int(m_date.group(3))
            target_date = today.replace(month=month, day=day)
            if target_date < today:
                target_date = target_date.replace(year=today.year + 1)
    else:
        target_date = today

    hour   = int(m_time.group(2))
    minute = int(m_time.group(3)) if m_time.group(3) else 0
    due_at = f"{target_date.isoformat()}T{hour:02d}:{minute:02d}:00"

    # 제목 파싱 (따옴표로 묶인 텍스트 우선)
    m_title = _REMINDER_TITLE_PATTERN.search(text)
    if m_title:
        title = m_title.group(1)
    else:
        # 감지어 앞 구절을 제목으로 사용
        title = text[:m_time.start()].strip().rstrip("에을를이가,")[-30:] or "리마인더"

    # 반복 여부
    repeat = "none"
    if re.search(r"(매일|daily|반복|every\s*day)", text, re.I):
        repeat = "daily"
    elif re.search(r"(매주|weekly|매\s*주|every\s*week)", text, re.I):
        repeat = "weekly"

    return {"title": title, "due_at": due_at, "repeat": repeat}


# 백그라운드 태스크 감지 패턴
_BG_PATTERNS = re.compile(
    r"(백그라운드|비동기|나중에\s*알려|분석\s*해줘|심층\s*분석|파일\s*분석"
    r"|시스템\s*점검|시스템\s*상태|배경에서|background)",
    re.IGNORECASE,
)

def _detect_background_task(text: str) -> tuple[str, dict, str] | None:
    """백그라운드 태스크 키워드를 감지해 (task_type, params, description) 반환.
    일반 대화면 None 반환."""
    if not _BG_PATTERNS.search(text):
        return None

    t = text.lower()
    if any(k in t for k in ("시스템", "system", "cpu", "메모리", "디스크", "ram")):
        return ("system_check", {}, "시스템 자원 점검")

    if any(k in t for k in ("파일", "file", "코드", "code")):
        return ("llm_analysis", {"text": text}, "코드/파일 심층 분석")

    return ("llm_analysis", {"text": text}, "심층 분석")


@router.post("/stream")
async def chat_stream(req: ChatRequest):
    """에이전트 자동 선택 → 현재 엔진으로 스트리밍 응답.

    응답 헤더:
      X-Agent-Key      선택된 페르소나 키
      X-Agent-Name     페르소나 이름
      X-Engine-Key     사용된 LLM 엔진 키
      X-Engine-Name    LLM 엔진 이름
      X-Route-Method   분류 방법 (keyword|llm|fallback)
      X-Confidence     분류 신뢰도 (0.0~1.0)
    """
    # ── 0-pre-r. 리마인더 자연어 파싱 ──
    reminder_info = _parse_reminder(req.message)
    if reminder_info:
        from app.services.scheduler_service import scheduler as _sched
        r = _sched.add(**reminder_info)
        due_str = reminder_info["due_at"].replace("T", " ")
        repeat_label = {"none": "", "daily": " (매일 반복)", "weekly": " (매주 반복)"}.get(
            reminder_info["repeat"], ""
        )
        ack = (
            f"Sir, '{r['title']}' 리마인더를 {due_str}에 설정했습니다.{repeat_label} "
            f"시간이 되면 알려드리겠습니다."
        )

        async def reminder_gen():
            yield ack

        return StreamingResponse(
            reminder_gen(),
            media_type="text/plain; charset=utf-8",
            headers={
                "X-Agent-Key":    "task_agent",
                "X-Agent-Name":   "Scheduler Agent",
                "X-Engine-Key":   "scheduler",
                "X-Engine-Name":  "Scheduler",
                "X-Route-Method": "reminder",
                "X-Confidence":   "1.0",
                "X-Reminder-Id":  r["id"],
            },
        )

    # ── 0-pre. 워크스페이스 전환 감지 ──
    ws_key = _detect_workspace_switch(req.message)
    if ws_key:
        from app.services.workspace_service import workspace as _ws
        import asyncio as _asyncio
        try:
            result = await _asyncio.get_event_loop().run_in_executor(
                None, _ws.switch, ws_key
            )
            tts_msg = result.get("tts_message", f"{result.get('name', ws_key)}으로 전환했습니다.")
            ack = (
                f"{tts_msg} "
                f"({result['done']}/{result['total']}개 액션 완료"
                + (f", 오류 {len(result['errors'])}건" if result['errors'] else "")
                + ")"
            )
        except Exception as e:
            ack = f"워크스페이스 전환 실패: {e}"

        async def ws_generate():
            yield ack

        return StreamingResponse(
            ws_generate(),
            media_type="text/plain; charset=utf-8",
            headers={
                "X-Agent-Key":       "task_agent",
                "X-Agent-Name":      "Workspace Agent",
                "X-Engine-Key":      "workspace",
                "X-Engine-Name":     "Workspace",
                "X-Route-Method":    "workspace",
                "X-Confidence":      "1.0",
                "X-Workspace-Key":   ws_key,
            },
        )

    # ── 0-a. 에러 자동 감지 → 백그라운드 디버그 태스크 제출 ──
    debug_task_id: str | None = None
    if _ERROR_PATTERNS.search(req.message):
        try:
            debug_task_id = task_manager.submit(
                task_type   = "debug_error",
                params      = {"error_text": req.message},
                description = "자동 에러 디버깅",
            )
        except Exception:
            pass   # 태스크 풀이 가득 차도 일반 채팅은 계속 진행

    # ── 0-b. 백그라운드 태스크 감지 ──
    bg = _detect_background_task(req.message)
    if bg:
        task_type, params, description = bg
        if not params.get("text"):
            params["text"] = req.message
        task_id = task_manager.submit(task_type, params, description)
        name = req.user_name.strip() or "Sir"
        ack_text = (
            f"Sir{' ' + name if name.upper() != 'SIR' else ''}, "
            f"백그라운드에서 '{description}' 작업을 시작했습니다. "
            f"(태스크 ID: {task_id}) 완료되면 알려드리겠습니다."
        )

        async def bg_generate():
            yield ack_text

        return StreamingResponse(
            bg_generate(),
            media_type="text/plain; charset=utf-8",
            headers={
                "X-Agent-Key":    "task_agent",
                "X-Agent-Name":   "Task Agent",
                "X-Engine-Key":   "background",
                "X-Engine-Name":  "Background",
                "X-Route-Method": "background",
                "X-Confidence":   "1.0",
                "X-Task-Id":      task_id,
            },
        )

    # ── 1. 에이전트 분류 + PromptSet 조립 ──
    prompt_set = await route(
        user_text = req.message,
        history   = req.history,
        force_llm = req.force_llm_route,
    )

    # ── 1-pre. PC 화면 제어(os_agent) 분류 → 실제 실행 후 결과를 텍스트로 반환 ──
    #   (raw JSON 액션 플랜이 채팅창에 그대로 노출되는 것을 방지)
    if prompt_set.agent_key == "os_agent":
        import json as _json
        from app.services.os_agent import agent as _os_agent

        async def os_generate():
            async for line in _os_agent.run_stream(req.message):
                try:
                    ev = _json.loads(line)
                except _json.JSONDecodeError:
                    continue
                event = ev.get("event")
                if event == "planning":
                    yield f"{ev['message']}\n"
                elif event == "plan":
                    yield f"{ev['thought']}\n"
                elif event == "start":
                    yield f"- {ev['log']}\n"
                elif event == "error":
                    yield f"  └ 오류: {ev.get('error') or ev.get('message', '알 수 없는 오류')}\n"
                elif event == "danger":
                    yield (
                        f"\n[위험 작업 감지] {ev['risk_reason']}\n"
                        f"확인이 필요한 작업이라 자동 실행을 중단했습니다, Sir."
                    )
                elif event == "finish":
                    if ev.get("aborted"):
                        yield f"\n작업을 중단했습니다: {ev.get('reason')}"
                    else:
                        yield f"\n완료했습니다, Sir. (성공 {ev['success']}/{ev['total']})"

        return StreamingResponse(
            os_generate(),
            media_type = "text/plain; charset=utf-8",
            headers    = {
                "X-Agent-Key":    "os_agent",
                "X-Agent-Name":   quote(prompt_set.agent_name),
                "X-Engine-Key":   "os_agent",
                "X-Engine-Name":  "OS Control Agent",
                "X-Route-Method":      prompt_set.routing.method,
                "X-Confidence":        str(prompt_set.routing.confidence),
            },
        )

    # ── 1-0. JARVIS 고정 페르소나 주입 ──
    prompt_set.system = f"{JARVIS_PERSONA_PROMPT}\n\n{prompt_set.system}"

    # ── 1-1. 사용자 호칭 주입 ──
    name = req.user_name.strip() or "Sir"
    if name.upper() != "SIR":
        prompt_set.system += (
            f"\n\n[USER IDENTITY] The user's name is {name}. "
            f"Always address them as 'Sir {name}' on first mention in each response, "
            f"or simply 'Sir {name}'. Never omit the honorific."
        )
    else:
        prompt_set.system += (
            "\n\n[USER IDENTITY] Address the user as 'Sir' at all times."
        )

    # ── 1-2. 장기 기억 컨텍스트 주입 ──
    mem_ctx = memory.get_context_prompt()
    if mem_ctx:
        prompt_set.system += f"\n\n{mem_ctx}"

    # ── 2. 엔진 스트리밍 (응답 전문을 누적해 기억에 저장) ──
    active = manager.active_preset
    _accumulated: list[str] = []

    async def generate():
        try:
            async for chunk in manager.stream(
                prompt_set   = prompt_set,
                override_key = req.engine_key,
            ):
                _accumulated.append(chunk)
                yield chunk
        except Exception as e:
            err_msg = f"\n[자비스 오류] 응답 생성 중 오류 발생: {e}"
            _accumulated.append(err_msg)
            yield err_msg
        finally:
            # 스트리밍 완료 후 기억 저장 (비동기, 응답 지연 없음)
            full_response = "".join(_accumulated)
            if full_response.strip():
                try:
                    memory.add(req.message, full_response, prompt_set.agent_key)
                except Exception:
                    pass

    return StreamingResponse(
        generate(),
        media_type = "text/plain; charset=utf-8",
        headers    = {
            "X-Agent-Key":    prompt_set.agent_key,
            "X-Agent-Name":   quote(prompt_set.agent_name),
            # 엔진 잠금: req.engine_key는 manager.stream()에서 무시되므로
            # 헤더도 항상 실제 사용된 엔진(active)을 그대로 보고한다.
            "X-Engine-Key":   active.key,
            "X-Engine-Name":  quote(active.name),
            "X-Route-Method":      prompt_set.routing.method,
            "X-Confidence":        str(prompt_set.routing.confidence),
            **({"X-Debug-Task-Id": debug_task_id} if debug_task_id else {}),
        },
    )


# ══════════════════════════════════════════════════════════════════════════════
# 에이전트 분류
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/classify")
async def classify_intent(req: ClassifyRequest):
    """유저 텍스트에 대한 에이전트 분류 결과만 반환 (스트리밍 없음).

    1차: 키워드 기반 분류로 빠르게 판단.
    2차: 키워드 신뢰도가 낮아 애매한 경우에만 LLM이 문맥을 보고 최종 판단한다.
    """
    result = await classify_async(req.message, force_llm=False)
    return {
        "agent_key":  result.agent_key,
        "agent_name": result.agent_name,
        "confidence": result.confidence,
        "method":     result.method,
        "reasoning":  result.reasoning,
        "scores":     result.scores,
    }


@router.get("/agents")
async def get_agents():
    """등록된 에이전트(페르소나) 목록."""
    return [
        {
            "key":         p.key,
            "name":        p.name,
            "description": p.description,
            "is_default":  p.is_default,
        }
        for p in list_agents()
    ]


# ══════════════════════════════════════════════════════════════════════════════
# LLM 엔진 관리
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/engine")
async def get_engine():
    """현재 활성 엔진 상태 반환."""
    return manager.status()


@router.get("/engines")
async def get_engines():
    """지원하는 전체 엔진 목록 반환 (현재 활성 표시 포함)."""
    return manager.list_engines()


@router.put("/engine/{engine_key}")
async def switch_engine(engine_key: str):
    """엔진을 즉시 전환. 이후 모든 요청에 적용.

    사용 가능한 키: OLLAMA_DEEPSEEK, OLLAMA_LLAMA, OLLAMA_MISTRAL,
                   CLAUDE_HAIKU, CLAUDE_SONNET, CLAUDE_OPUS,
                   GPT4O_MINI, GPT4O
    """
    try:
        preset = manager.switch(engine_key.upper())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {
        "message":     f"엔진이 '{preset.name}'으로 전환되었습니다.",
        "engine_key":  preset.key,
        "engine_name": preset.name,
        "provider":    preset.provider,
        "tier":        preset.tier,
    }


@router.post("/engine/detect")
async def detect_engine_switch(req: EngineDetectRequest):
    """텍스트에서 엔진 전환 명령을 감지하고 실행.

    음성 명령 텍스트를 전달하면 자동으로 파싱 후 전환.
    전환 명령이 없으면 switched=false 반환.
    """
    preset_key = manager.parse_switch_command(req.text)
    if preset_key is None:
        return {
            "switched":   False,
            "detected":   None,
            "message":    "엔진 전환 명령이 감지되지 않았습니다.",
            "input_text": req.text,
        }
    # 엔진 잠금: CLAUDE_CODE 외 엔진으로의 전환 요청은 무시한다 (오류 아님)
    if preset_key not in ALLOWED_ENGINES:
        return {
            "switched":   False,
            "detected":   preset_key,
            "message":    "JARVIS는 CLAUDE_CODE 엔진으로 고정되어 있어 전환하지 않았습니다.",
            "input_text": req.text,
        }
    preset = manager.switch(preset_key)
    return {
        "switched":    True,
        "engine_key":  preset.key,
        "engine_name": preset.name,
        "provider":    preset.provider,
        "tier":        preset.tier,
        "message":     f"음성 명령으로 '{preset.name}'으로 전환되었습니다.",
        "input_text":  req.text,
    }


# ══════════════════════════════════════════════════════════════════════════════
# API 키 설정
# ══════════════════════════════════════════════════════════════════════════════

_PROVIDER_ENV_MAP = {
    "gemini":    "GEMINI_API_KEY",
    "openai":    "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "groq":      "GROQ_API_KEY",
}


def _persist_env_var(key: str, value: str) -> None:
    """.env 파일의 KEY=value 라인을 갱신(없으면 추가)하여 재시작 후에도 유지되게 한다."""
    env_path = Path(".env")
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    pattern = re.compile(rf"^{re.escape(key)}=")
    for i, line in enumerate(lines):
        if pattern.match(line):
            lines[i] = f"{key}={value}"
            break
    else:
        lines.append(f"{key}={value}")
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@router.put("/settings/api-key")
async def update_api_key(req: ApiKeyUpdateRequest):
    """AI 엔진 설정 모달에서 입력한 API 키를 런타임 설정 + .env에 반영.

    이후 요청부터 즉시 새 키로 LLM을 호출한다.
    """
    env_key = _PROVIDER_ENV_MAP.get(req.provider)
    if not env_key:
        raise HTTPException(status_code=400, detail=f"알 수 없는 프로바이더: {req.provider}")

    if req.provider == "gemini":
        settings.gemini_api_key = req.api_key
    elif req.provider == "openai":
        settings.openai_api_key = req.api_key
    elif req.provider == "anthropic":
        settings.anthropic_api_key = req.api_key
    elif req.provider == "groq":
        settings.groq_api_key = req.api_key

    _persist_env_var(env_key, req.api_key)

    return {
        "message":  f"{req.provider.upper()} API 키가 저장되었습니다.",
        "provider": req.provider,
    }
