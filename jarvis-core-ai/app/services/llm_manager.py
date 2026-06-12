"""
llm_manager.py — JARVIS LLM Engine Manager
════════════════════════════════════════════════════════════════════════════════
역할:
  · 활성 AI 엔진 상태를 단일 진실 공급원(single source of truth)으로 관리
  · 음성 명령 / REST / WebSocket 신호로 엔진 전환
  · PromptSet(agent_router 출력)을 선택된 엔진으로 전송 후 스트리밍 반환
  · 각 엔진별 토큰 수 · 지연시간 메타데이터 수집

지원 엔진 프리셋:
  [구독]      CLAUDE_CODE (기본 — Claude Code CLI, API 키 불필요)
  [무료 로컬]  OLLAMA_DEEPSEEK | OLLAMA_LLAMA | OLLAMA_MISTRAL
  [유료 API]  CLAUDE_HAIKU | CLAUDE_SONNET | CLAUDE_OPUS
              GPT4O_MINI  | GPT4O

Public API:
  manager.switch(key)                → EnginePreset
  manager.status()                   → dict
  manager.parse_switch_command(text) → Optional[str]  (프리셋 키)
  manager.stream(prompt_set)         → AsyncGenerator[str, None]
  manager.run(prompt_set)            → LLMResponse
════════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
import time
from dataclasses import dataclass, field, asdict
from typing import AsyncGenerator, Optional

# ── 선택적 임포트 (실행 시점에 로드) ──────────────────────────────────────────
# anthropic / openai / httpx 는 해당 엔진이 실제로 호출될 때만 import


# ══════════════════════════════════════════════════════════════════════════════
# 1. 엔진 프리셋 정의
# ══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class EnginePreset:
    key: str                 # 프리셋 식별자 (대문자 언더스코어)
    name: str                # 화면 표시 이름
    provider: str            # "ollama" | "claude" | "openai"
    model_id: str            # 실제 API에 전달할 모델 식별자
    tier: str                # "free" | "paid"
    max_tokens: int          # 최대 생성 토큰
    description: str
    is_default: bool = False


# 프리셋 레지스트리 ─────────────────────────────────────────────────────────
ENGINE_REGISTRY: dict[str, EnginePreset] = {p.key: p for p in [

    # ── 구독 (Claude Code CLI) — 기본 엔진 ──────────────────────────────────
    EnginePreset(
        key         = "CLAUDE_CODE",
        name        = "Claude Code (구독)",
        provider    = "claude_code",
        model_id    = "claude-code",
        tier        = "subscription",
        max_tokens  = 0,    # CLI가 관리 — 미사용
        description = "Claude Code 헤드리스 모드 — 구독 기반, API 키 불필요 — 기본 엔진",
        is_default  = True,
    ),

    # ── 무료 로컬 (Ollama) ──────────────────────────────────────────────────
    EnginePreset(
        key         = "OLLAMA_DEEPSEEK",
        name        = "DeepSeek-R1 (로컬)",
        provider    = "ollama",
        model_id    = "deepseek-r1:8b",
        tier        = "free",
        max_tokens  = 8192,
        description = "오프라인 추론 특화 모델 — 코딩·수학에 강함",
    ),
    EnginePreset(
        key         = "OLLAMA_LLAMA",
        name        = "Llama 3.2 (로컬)",
        provider    = "ollama",
        model_id    = "llama3.2",
        tier        = "free",
        max_tokens  = 4096,
        description = "Meta 범용 오픈소스 모델 — 빠른 일상 대화",
    ),
    EnginePreset(
        key         = "OLLAMA_QWEN",
        name        = "Qwen2.5 7B (로컬)",
        provider    = "ollama",
        model_id    = "qwen2.5:7b",
        tier        = "free",
        max_tokens  = 4096,
        description = "다국어/한국어 응답 품질이 우수한 모델",
    ),
    EnginePreset(
        key         = "OLLAMA_MISTRAL",
        name        = "Mistral 7B (로컬)",
        provider    = "ollama",
        model_id    = "mistral:7b",
        tier        = "free",
        max_tokens  = 4096,
        description = "유럽산 경량 모델 — 영어 텍스트 품질 우수",
    ),

    # ── 유료 API (Claude / Anthropic) ───────────────────────────────────────
    EnginePreset(
        key         = "CLAUDE_HAIKU",
        name        = "Claude Haiku 4.5",
        provider    = "claude",
        model_id    = "claude-haiku-4-5-20251001",
        tier        = "paid",
        max_tokens  = 8192,
        description = "가장 빠른 Claude — 간단한 작업, 저비용",
    ),
    EnginePreset(
        key         = "CLAUDE_SONNET",
        name        = "Claude Sonnet 4.6",
        provider    = "claude",
        model_id    = "claude-sonnet-4-6",
        tier        = "paid",
        max_tokens  = 16384,
        description = "균형잡힌 Claude — 복잡한 추론 + 적정 비용",
    ),
    EnginePreset(
        key         = "CLAUDE_OPUS",
        name        = "Claude Opus 4.7",
        provider    = "claude",
        model_id    = "claude-opus-4-7",
        tier        = "paid",
        max_tokens  = 32768,
        description = "최고 성능 Claude — 고난도 분석·창작",
    ),

    # ── 유료 API (OpenAI) ────────────────────────────────────────────────────
    EnginePreset(
        key         = "GPT4O_MINI",
        name        = "GPT-4o Mini",
        provider    = "openai",
        model_id    = "gpt-4o-mini",
        tier        = "paid",
        max_tokens  = 16384,
        description = "경량 GPT-4o — 저비용 실시간 작업",
    ),
    EnginePreset(
        key         = "GPT4O",
        name        = "GPT-4o",
        provider    = "openai",
        model_id    = "gpt-4o",
        tier        = "paid",
        max_tokens  = 16384,
        description = "OpenAI 플래그십 — 멀티모달·고성능",
    ),

    # ── 유료 API (Google Gemini) ─────────────────────────────────────────────
    EnginePreset(
        key         = "GEMINI_FLASH",
        name        = "Gemini 2.5 Flash",
        provider    = "gemini",
        model_id    = "gemini-2.5-flash",
        tier        = "paid",
        max_tokens  = 8192,
        description = "Google 경량 모델 — 빠른 응답, 저비용",
    ),
    EnginePreset(
        key         = "GEMINI_FLASH_LITE",
        name        = "Gemini 2.5 Flash-Lite",
        provider    = "gemini",
        model_id    = "gemini-2.5-flash-lite",
        tier        = "paid",
        max_tokens  = 8192,
        description = "Google 초경량 모델 — 가장 빠르고 저렴",
    ),
    EnginePreset(
        key         = "GEMINI_PRO",
        name        = "Gemini 2.5 Pro",
        provider    = "gemini",
        model_id    = "gemini-2.5-pro",
        tier        = "paid",
        max_tokens  = 16384,
        description = "Google 플래그십 — 고난도 추론·긴 컨텍스트",
    ),

    # ── 유료/무료 API (Groq) ──────────────────────────────────────────────────
    EnginePreset(
        key         = "GROQ_LLAMA_70B",
        name        = "Llama 3.3 70B (Groq)",
        provider    = "groq",
        model_id    = "llama-3.3-70b-versatile",
        tier        = "free",
        max_tokens  = 8192,
        description = "Groq LPU 초고속 추론 — 대형 모델, 무료 한도 제공",
    ),
    EnginePreset(
        key         = "GROQ_LLAMA_8B",
        name        = "Llama 3.1 8B Instant (Groq)",
        provider    = "groq",
        model_id    = "llama-3.1-8b-instant",
        tier        = "free",
        max_tokens  = 8192,
        description = "Groq LPU 초고속 추론 — 경량 모델, 가장 빠른 응답",
    ),
]}

# 기본 엔진 프리셋 키
_DEFAULT_ENGINE_KEY = next(k for k, v in ENGINE_REGISTRY.items() if v.is_default)

# ── 엔진 잠금 (절대 변경 금지) ────────────────────────────────────────────────
# JARVIS는 사용자의 Claude Code 구독만 두뇌로 사용한다. 다른 엔진으로의 전환은
# (UI/음성명령/엔진 목록 조회 실패 폴백 등 어떤 경로로도) 허용되지 않는다.
ALLOWED_ENGINES: frozenset[str] = frozenset({"CLAUDE_CODE"})


# ══════════════════════════════════════════════════════════════════════════════
# 2. 응답 데이터 모델
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class LLMResponse:
    """run() 메서드가 반환하는 완성된 응답 + 메타데이터."""
    text: str
    engine_key: str
    engine_name: str
    agent_key: str
    agent_name: str
    prompt_tokens: int      = 0
    completion_tokens: int  = 0
    total_tokens: int       = 0
    latency_sec: float      = 0.0
    routing_method: str     = ""
    routing_confidence: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class _TokenAccumulator:
    """스트리밍 중 토큰 수집용 내부 상태."""
    prompt: int     = 0
    completion: int = 0

    @property
    def total(self) -> int:
        return self.prompt + self.completion


# ══════════════════════════════════════════════════════════════════════════════
# 3. 음성 명령 파싱 규칙
#    패턴 → 프리셋 키 매핑 (우선순위: 더 구체적인 패턴을 앞에 배치)
# ══════════════════════════════════════════════════════════════════════════════

_SWITCH_PATTERNS: list[tuple[str, str]] = [

    # ── Claude Code (구독) — 일반 claude 패턴보다 먼저 매칭 ──────────────────
    (r"\b(claude\s*code|클로드\s*코드|코드\s*엔진)\b",  "CLAUDE_CODE"),

    # ── DeepSeek ─────────────────────────────────────────────────────────────
    (r"\b(deepseek|딥시크|deep\s*seek)\b",           "OLLAMA_DEEPSEEK"),

    # ── Llama ────────────────────────────────────────────────────────────────
    (r"\b(llama|라마|lama)\b",                        "OLLAMA_LLAMA"),

    # ── Mistral ──────────────────────────────────────────────────────────────
    (r"\b(mistral|미스트랄|미스트럴)\b",              "OLLAMA_MISTRAL"),

    # ── 로컬/무료 총칭 → DeepSeek (기본 로컬) ────────────────────────────────
    (r"\b(ollama|올라마|로컬\s*모드?|local|무료\s*모드?)\b", "OLLAMA_DEEPSEEK"),

    # ── Claude Opus ──────────────────────────────────────────────────────────
    (r"\b(claude|클로드)\s*(opus|오퍼스|오퍼|4\.7)\b", "CLAUDE_OPUS"),

    # ── Claude Haiku ─────────────────────────────────────────────────────────
    (r"\b(claude|클로드)\s*(haiku|하이쿠|4\.5)\b",    "CLAUDE_HAIKU"),

    # ── Claude Sonnet (기본 Claude) ──────────────────────────────────────────
    (r"\b(claude|클로드)\s*(sonnet|소넷|4\.6)?\b",    "CLAUDE_SONNET"),

    # ── GPT-4o Mini ──────────────────────────────────────────────────────────
    (r"\b(gpt.?4o?.?mini|지피티.?미니|mini\s*gpt)\b", "GPT4O_MINI"),

    # ── GPT-4o / OpenAI 총칭 ─────────────────────────────────────────────────
    (r"\b(gpt.?4o|openai|open\s*ai|챗지피티|chatgpt|gpt)\b", "GPT4O"),

    # ── Gemini Pro ───────────────────────────────────────────────────────────
    (r"\b(gemini|제미나이|제미니)\s*(pro|프로|2\.5)\b", "GEMINI_PRO"),

    # ── Gemini Flash-Lite ────────────────────────────────────────────────────
    (r"\b(gemini|제미나이|제미니)\s*(flash.?lite|플래시\s*라이트)\b", "GEMINI_FLASH_LITE"),

    # ── Gemini Flash / 총칭 ──────────────────────────────────────────────────
    (r"\b(gemini|제미나이|제미니)\s*(flash|플래시|2\.5|2\.0)?\b", "GEMINI_FLASH"),

    # ── Groq Llama 8B (경량) ─────────────────────────────────────────────────
    (r"\b(groq|그록|그로크)\s*(8b|8\s*billion|작은|경량|빠른)\b", "GROQ_LLAMA_8B"),

    # ── Groq 총칭 → Llama 70B (기본) ─────────────────────────────────────────
    (r"\b(groq|그록|그로크)\s*(70b|llama|라마)?\b", "GROQ_LLAMA_70B"),
]

# 트리거 동사 (이 단어와 함께 등장해야 명령으로 인정)
_TRIGGER_WORDS = re.compile(
    r"(바꿔|바꿔줘|변경|전환|스위치|switch|change|use|써줘?|사용|켜줘?|모드|mode)",
    re.IGNORECASE,
)


# ══════════════════════════════════════════════════════════════════════════════
# 4. LLM Manager 클래스
# ══════════════════════════════════════════════════════════════════════════════

class LLMManager:
    """JARVIS의 두뇌 — AI 엔진 상태 관리 및 PromptSet 실행."""

    def __init__(self, default_key: str = _DEFAULT_ENGINE_KEY) -> None:
        if default_key not in ENGINE_REGISTRY:
            raise ValueError(f"알 수 없는 엔진 키: {default_key!r}")
        self._active_key: str         = default_key
        self._lock: asyncio.Lock      = asyncio.Lock()
        self._switch_history: list[dict] = []

    # ── 상태 조회 ─────────────────────────────────────────────────────────────

    @property
    def active_preset(self) -> EnginePreset:
        return ENGINE_REGISTRY[self._active_key]

    def status(self) -> dict:
        p = self.active_preset
        return {
            "engine_key":   p.key,
            "engine_name":  p.name,
            "provider":     p.provider,
            "model_id":     p.model_id,
            "tier":         p.tier,
            "description":  p.description,
            "switch_count": len(self._switch_history),
        }

    def list_engines(self) -> list[dict]:
        active = self._active_key
        return [
            {**asdict(p), "is_active": p.key == active}
            for p in ENGINE_REGISTRY.values()
        ]

    # ── 엔진 전환 ─────────────────────────────────────────────────────────────

    def switch(self, key: str) -> EnginePreset:
        """엔진 프리셋을 전환하고 새 프리셋을 반환.

        Args:
            key: ENGINE_REGISTRY의 키 (예: "CLAUDE_SONNET")

        Raises:
            ValueError: 알 수 없는 키
        """
        key = key.upper().strip()
        if key not in ALLOWED_ENGINES:
            raise ValueError(
                f"'{key}' 엔진으로 전환할 수 없습니다 — JARVIS는 CLAUDE_CODE "
                f"엔진으로 고정되어 있으며 전환이 허용되지 않습니다."
            )
        prev = self._active_key
        self._active_key = key
        self._switch_history.append({
            "from": prev,
            "to":   key,
            "ts":   time.time(),
        })
        preset = ENGINE_REGISTRY[key]
        print(
            f"[JARVIS] 엔진 전환: {prev} → {key}  "
            f"({preset.name}, {preset.tier})"
        )
        return preset

    # ── 음성 명령 파싱 ────────────────────────────────────────────────────────

    @staticmethod
    def parse_switch_command(text: str) -> Optional[str]:
        """텍스트에서 엔진 전환 명령을 감지하고 프리셋 키를 반환.

        트리거 동사가 없어도 엔진 이름만 명확히 언급되면 감지.
        반환값이 None이면 전환 명령 없음.

        Examples:
            "자비스, Claude 소넷으로 바꿔줘"  → "CLAUDE_SONNET"
            "딥시크 모드 켜줘"                → "OLLAMA_DEEPSEEK"
            "오늘 날씨 어때?"                 → None
        """
        if not text:
            return None

        has_trigger = bool(_TRIGGER_WORDS.search(text))

        for pattern, preset_key in _SWITCH_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                # 트리거 동사가 있거나, 아예 엔진 이름만 단독으로 언급된 경우
                if has_trigger or len(text.strip().split()) <= 4:
                    return preset_key

        return None

    # ══════════════════════════════════════════════════════════════════════════
    # 5. 엔진별 스트리밍 구현
    # ══════════════════════════════════════════════════════════════════════════

    async def _stream_ollama(
        self,
        preset: EnginePreset,
        messages: list[dict],
        system: str,
        tokens: _TokenAccumulator,
    ) -> AsyncGenerator[str, None]:
        """Ollama /api/chat 스트리밍 호출."""
        import httpx

        from app.config import settings
        base_url = settings.ollama_base_url

        full_messages = (
            [{"role": "system", "content": system}] if system else []
        ) + messages

        # 마지막 사용자 메시지에 한국어 응답 리마인더를 덧붙여 recency 효과 활용
        if full_messages and full_messages[-1].get("role") == "user":
            full_messages = full_messages[:-1] + [{
                **full_messages[-1],
                "content": (
                    full_messages[-1]["content"]
                    + "\n\n(반드시 한국어로만 답하세요. 다른 언어를 섞지 마세요.)"
                ),
            }]

        payload = {
            "model":    preset.model_id,
            "messages": full_messages,
            "stream":   True,
            "options":  {"num_predict": preset.max_tokens, "temperature": 0.4},
        }

        try:
            async with httpx.AsyncClient(timeout=180) as client:
                async with client.stream(
                    "POST",
                    f"{base_url}/api/chat",
                    json=payload,
                ) as resp:
                    if resp.status_code != 200:
                        error_body = await resp.aread()
                        error_text = error_body.decode("utf-8", errors="replace")
                        yield (
                            f"\n[JARVIS 오류] Ollama 오류 (HTTP {resp.status_code}): {error_text[:200]}\n"
                            f"모델 '{preset.model_id}'이 설치되어 있는지 확인하세요 (`ollama list`)."
                        )
                        return
                    async for line in resp.aiter_lines():
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                        except json.JSONDecodeError:
                            continue

                        # 토큰 수 수집 (마지막 라인에 포함)
                        if data.get("done"):
                            tokens.prompt     = data.get("prompt_eval_count", 0)
                            tokens.completion = data.get("eval_count", 0)
                            break

                        content = data.get("message", {}).get("content", "")
                        if content:
                            yield content

        except httpx.ConnectError:
            yield (
                "\n[JARVIS 오류] Ollama 서버에 연결할 수 없습니다. "
                "`ollama serve` 명령으로 서버를 시작해 주세요."
            )
        except Exception as e:
            yield f"\n[JARVIS 오류] Ollama 스트리밍 오류: {e}"

    async def _stream_claude(
        self,
        preset: EnginePreset,
        messages: list[dict],
        system: str,
        tokens: _TokenAccumulator,
    ) -> AsyncGenerator[str, None]:
        """Anthropic Claude 스트리밍 호출."""
        import anthropic
        from app.config import settings

        api_key = settings.anthropic_api_key
        if not api_key:
            yield "\n[JARVIS 오류] ANTHROPIC_API_KEY가 설정되지 않았습니다."
            return

        client = anthropic.AsyncAnthropic(api_key=api_key)
        try:
            async with client.messages.stream(
                model      = preset.model_id,
                max_tokens = preset.max_tokens,
                system     = system,
                messages   = messages,
            ) as stream:
                async for text in stream.text_stream:
                    yield text

                # 스트림 종료 후 사용량 수집
                final = await stream.get_final_message()
                tokens.prompt     = final.usage.input_tokens
                tokens.completion = final.usage.output_tokens

        except anthropic.AuthenticationError:
            yield "\n[JARVIS 오류] Claude API 키가 유효하지 않습니다."
        except anthropic.RateLimitError:
            yield "\n[JARVIS 오류] Claude API 요청 한도를 초과했습니다. 잠시 후 재시도하세요."
        except Exception as e:
            yield f"\n[JARVIS 오류] Claude 호출 실패: {e}"

    async def _stream_openai(
        self,
        preset: EnginePreset,
        messages: list[dict],
        system: str,
        tokens: _TokenAccumulator,
    ) -> AsyncGenerator[str, None]:
        """OpenAI 스트리밍 호출."""
        from openai import AsyncOpenAI, AuthenticationError, RateLimitError
        from app.config import settings

        api_key = settings.openai_api_key
        if not api_key:
            yield "\n[JARVIS 오류] OPENAI_API_KEY가 설정되지 않았습니다."
            return

        client = AsyncOpenAI(api_key=api_key)
        full_messages = (
            [{"role": "system", "content": system}] if system else []
        ) + messages

        try:
            stream = await client.chat.completions.create(
                model      = preset.model_id,
                messages   = full_messages,
                max_tokens = preset.max_tokens,
                stream     = True,
                stream_options={"include_usage": True},   # 토큰 수 포함 요청
            )
            async for chunk in stream:
                # 마지막 청크에 usage 포함 (stream_options 사용 시)
                if chunk.usage:
                    tokens.prompt     = chunk.usage.prompt_tokens
                    tokens.completion = chunk.usage.completion_tokens

                delta = chunk.choices[0].delta.content if chunk.choices else None
                if delta:
                    yield delta

        except AuthenticationError:
            yield "\n[JARVIS 오류] OpenAI API 키가 유효하지 않습니다."
        except RateLimitError:
            yield "\n[JARVIS 오류] OpenAI API 요청 한도를 초과했습니다."
        except Exception as e:
            yield f"\n[JARVIS 오류] OpenAI 호출 실패: {e}"

    async def _stream_gemini(
        self,
        preset: EnginePreset,
        messages: list[dict],
        system: str,
        tokens: _TokenAccumulator,
    ) -> AsyncGenerator[str, None]:
        """Google Gemini 스트리밍 호출 (google-genai SDK)."""
        from google import genai
        from google.genai import types
        from google.genai.errors import APIError
        from app.config import settings

        api_key = settings.gemini_api_key
        if not api_key:
            yield "\n[JARVIS 오류] GEMINI_API_KEY가 설정되지 않았습니다."
            return

        # OpenAI/Anthropic 스타일 메시지 → Gemini 형식("model" 역할, "parts" 필드)으로 변환
        contents = [
            types.Content(
                role  = "model" if m.get("role") == "assistant" else "user",
                parts = [types.Part.from_text(text=m.get("content", ""))],
            )
            for m in messages
        ]

        client = genai.Client(api_key=api_key)
        config = types.GenerateContentConfig(
            system_instruction = system or None,
            max_output_tokens  = preset.max_tokens,
            temperature        = 0.4,
        )

        try:
            stream = await client.aio.models.generate_content_stream(
                model    = preset.model_id,
                contents = contents,
                config   = config,
            )
            async for chunk in stream:
                if chunk.text:
                    yield chunk.text
                if chunk.usage_metadata:
                    tokens.prompt     = chunk.usage_metadata.prompt_token_count or 0
                    tokens.completion = chunk.usage_metadata.candidates_token_count or 0

        except APIError as e:
            if e.code in (401, 403):
                yield "\n[JARVIS 오류] Gemini API 키가 유효하지 않습니다."
            elif e.code == 429:
                yield "\n[JARVIS 오류] Gemini API 요청 한도를 초과했습니다. 잠시 후 재시도하세요."
            else:
                yield f"\n[JARVIS 오류] Gemini 호출 실패: {e}"
        except Exception as e:
            yield f"\n[JARVIS 오류] Gemini 호출 실패: {e}"

    async def _stream_groq(
        self,
        preset: EnginePreset,
        messages: list[dict],
        system: str,
        tokens: _TokenAccumulator,
    ) -> AsyncGenerator[str, None]:
        """Groq 스트리밍 호출 (OpenAI 호환 API)."""
        from openai import AsyncOpenAI, AuthenticationError, RateLimitError
        from app.config import settings

        api_key = settings.groq_api_key
        if not api_key:
            yield "\n[JARVIS 오류] GROQ_API_KEY가 설정되지 않았습니다."
            return

        client = AsyncOpenAI(api_key=api_key, base_url="https://api.groq.com/openai/v1")
        full_messages = (
            [{"role": "system", "content": system}] if system else []
        ) + messages

        try:
            stream = await client.chat.completions.create(
                model      = preset.model_id,
                messages   = full_messages,
                max_tokens = preset.max_tokens,
                stream     = True,
                stream_options={"include_usage": True},
            )
            async for chunk in stream:
                if chunk.usage:
                    tokens.prompt     = chunk.usage.prompt_tokens
                    tokens.completion = chunk.usage.completion_tokens

                delta = chunk.choices[0].delta.content if chunk.choices else None
                if delta:
                    yield delta

        except AuthenticationError:
            yield "\n[JARVIS 오류] Groq API 키가 유효하지 않습니다."
        except RateLimitError:
            yield "\n[JARVIS 오류] Groq API 요청 한도를 초과했습니다. 잠시 후 재시도하세요."
        except Exception as e:
            yield f"\n[JARVIS 오류] Groq 호출 실패: {e}"

    async def _stream_claude_code(
        self,
        preset: EnginePreset,
        messages: list[dict],
        system: str,
        tokens: _TokenAccumulator,
    ) -> AsyncGenerator[str, None]:
        """Claude Code CLI(구독) 스트리밍 호출.

        대화 연속성은 CLI 세션(--resume)이 담당하므로 마지막 사용자 메시지만
        전달한다. 페르소나(system)는 --append-system-prompt로 주입된다.
        미설치/미로그인/예산초과/한도도달은 한국어 안내문으로 변환해 yield하고,
        기계가 읽을 상태는 tokens.extra → 메타 청크에 병합된다.
        """
        from app.services.claude_code import (
            CCResult, CCStatusEvent, CCTextDelta, CCWarning, get_wrapper,
        )

        wrapper = get_wrapper()
        prompt = messages[-1].get("content", "") if messages else ""
        extra: dict = {}
        tokens.extra = extra            # stream()이 메타 청크에 병합

        try:
            async for ev in wrapper.stream(prompt, system=system, resume=True):
                if isinstance(ev, CCTextDelta):
                    yield ev.text
                elif isinstance(ev, CCResult):
                    tokens.prompt     = ev.input_tokens
                    tokens.completion = ev.output_tokens
                    extra.update({
                        "status":     "ok",
                        "cost_usd":   ev.total_cost_usd,
                        "num_turns":  ev.num_turns,
                        "session_id": ev.session_id,
                    })
                elif isinstance(ev, CCStatusEvent):
                    extra.update({"status": ev.status.value,
                                  "reset_at": ev.reset_at})
                    yield f"\n[JARVIS] {ev.message}"
                elif isinstance(ev, CCWarning):
                    extra.setdefault("warnings", []).append(ev.message)
                    yield f"\n[JARVIS 경고] {ev.message}"
                # CCInit / CCToolUse / CCToolResult: 채팅 텍스트 미출력
        except Exception as e:
            yield f"\n[JARVIS 오류] Claude Code 호출 실패: {e}"

    # ══════════════════════════════════════════════════════════════════════════
    # 6. 엔진 디스패처
    # ══════════════════════════════════════════════════════════════════════════

    def _get_stream_generator(
        self,
        preset: EnginePreset,
        messages: list[dict],
        system: str,
        tokens: _TokenAccumulator,
    ) -> AsyncGenerator[str, None]:
        """프로바이더에 따라 올바른 스트리밍 제너레이터를 반환."""
        dispatch = {
            "claude_code": self._stream_claude_code,
            "ollama":      self._stream_ollama,
            "claude":      self._stream_claude,
            "openai":      self._stream_openai,
            "gemini":      self._stream_gemini,
            "groq":        self._stream_groq,
        }
        handler = dispatch.get(preset.provider)
        if handler is None:
            raise ValueError(f"지원하지 않는 프로바이더: {preset.provider!r}")
        return handler(preset, messages, system, tokens)

    # ══════════════════════════════════════════════════════════════════════════
    # 7. Public: stream() — PromptSet 기반 스트리밍
    # ══════════════════════════════════════════════════════════════════════════

    async def stream(
        self,
        prompt_set,                      # agent_router.PromptSet
        override_key: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        """PromptSet을 현재(또는 지정) 엔진으로 전송하고 텍스트 청크를 스트리밍.

        Args:
            prompt_set:   agent_router.route()의 반환값
            override_key: 이 요청에만 임시로 다른 엔진 사용 (상태 변경 없음)

        Yields:
            응답 텍스트 청크 (str)
        """
        # 엔진 잠금: 허용되지 않은 override_key는 무시하고 현재 활성 엔진을 사용
        key     = override_key.upper().strip() if override_key else None
        preset  = ENGINE_REGISTRY.get(key if key in ALLOWED_ENGINES else self._active_key,
                                      self.active_preset)
        tokens  = _TokenAccumulator()

        async for chunk in self._get_stream_generator(
            preset   = preset,
            messages = prompt_set.messages,
            system   = prompt_set.system,
            tokens   = tokens,
        ):
            yield chunk

        # 스트림 완료 후 메타데이터를 마지막 청크로 전송
        # 클라이언트가 파싱하여 UI 업데이트에 활용 가능
        meta = {
            "engine":     preset.key,
            "model":      preset.model_id,
            "agent":      prompt_set.agent_key,
            "tokens":     tokens.total,
            "p_tokens":   tokens.prompt,
            "c_tokens":   tokens.completion,
            # claude_code 등 프로바이더별 부가 메타 (status, cost_usd, session_id 등)
            **getattr(tokens, "extra", {}),
        }
        yield f"\x00{json.dumps(meta, ensure_ascii=False)}"   # NUL prefix로 메타 청크 표시

    # ══════════════════════════════════════════════════════════════════════════
    # 8. Public: run() — 완성된 응답 반환
    # ══════════════════════════════════════════════════════════════════════════

    async def run(
        self,
        prompt_set,
        override_key: Optional[str] = None,
        max_retries: int = 2,
    ) -> LLMResponse:
        """PromptSet을 실행하고 완성된 LLMResponse를 반환.

        stream()과 달리 전체 응답을 모아서 반환하므로 배치 처리 / 테스트에 적합.
        네트워크 오류 시 max_retries 횟수만큼 지수 백오프로 재시도.
        """
        # 엔진 잠금: 허용되지 않은 override_key는 무시하고 현재 활성 엔진을 사용
        key     = override_key.upper().strip() if override_key else None
        preset  = ENGINE_REGISTRY.get(key if key in ALLOWED_ENGINES else self._active_key,
                                      self.active_preset)
        tokens  = _TokenAccumulator()
        chunks: list[str] = []
        t_start = time.perf_counter()

        for attempt in range(max_retries + 1):
            chunks.clear()
            try:
                async for chunk in self._get_stream_generator(
                    preset   = preset,
                    messages = prompt_set.messages,
                    system   = prompt_set.system,
                    tokens   = tokens,
                ):
                    # 메타 청크 제외
                    if not chunk.startswith("\x00"):
                        chunks.append(chunk)
                break   # 성공 시 재시도 루프 종료

            except Exception as e:
                if attempt == max_retries:
                    print(f"[LLMManager] 최대 재시도 초과: {e}", file=sys.stderr)
                    chunks = [f"[JARVIS 오류] 응답 생성 실패: {e}"]
                    break
                delay = 1.0 * (2 ** attempt)
                print(f"[LLMManager] 재시도 {attempt+1}/{max_retries} "
                      f"({delay:.0f}s 후): {e}", file=sys.stderr)
                await asyncio.sleep(delay)

        return LLMResponse(
            text                = "".join(chunks).strip(),
            engine_key          = preset.key,
            engine_name         = preset.name,
            agent_key           = prompt_set.agent_key,
            agent_name          = prompt_set.agent_name,
            prompt_tokens       = tokens.prompt,
            completion_tokens   = tokens.completion,
            total_tokens        = tokens.total,
            latency_sec         = round(time.perf_counter() - t_start, 3),
            routing_method      = prompt_set.routing.method,
            routing_confidence  = prompt_set.routing.confidence,
        )


# ══════════════════════════════════════════════════════════════════════════════
# 9. 모듈 레벨 싱글턴 — 전체 앱이 공유하는 단일 인스턴스
# ══════════════════════════════════════════════════════════════════════════════

manager = LLMManager(default_key=_DEFAULT_ENGINE_KEY)
