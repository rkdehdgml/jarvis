"""
agent_router.py — Intent Classification & Prompt Routing Engine
════════════════════════════════════════════════════════════════════════════════
외부 라이브러리 의존성 없음. 표준 라이브러리(json, re, pathlib)만 사용.

분류 전략 (2계층):
  1차 — KeywordScorer   : 정규식 기반 가중치 점수 → 즉시, 오프라인
  2차 — LlmClassifier   : 분류 전용 시스템 프롬프트 → 1차 신뢰도 낮을 때만 호출

Public API:
    route(user_text, history)  →  PromptSet      ← LLM 매니저에 바로 전달
    classify(user_text)        →  RoutingResult  ← 분류 결과만 필요할 때
    list_agents()              →  list[AgentProfile]
════════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ── 경로 설정 ─────────────────────────────────────────────────────────────────
# 이 파일 위치: app/services/agent_router.py
# 프롬프트 위치: ../../prompts/
_PROMPTS_DIR = Path(__file__).parent.parent.parent / "prompts"

# LLM 분류를 호출하는 신뢰도 임계값 (이 값 미만이면 LLM 재판단 요청)
_LLM_FALLBACK_THRESHOLD = 0.30


# ══════════════════════════════════════════════════════════════════════════════
# 1. 데이터 모델
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class AgentProfile:
    """에이전트 하나에 대한 메타데이터."""
    key: str
    name: str
    description: str
    prompt_file: str                    # prompts/ 내 파일명
    keywords: dict[str, float]          # {패턴(정규식): 가중치}
    is_default: bool = False            # 분류 실패 시 폴백


@dataclass
class RoutingResult:
    """분류 엔진이 반환하는 라우팅 결정."""
    agent_key: str
    agent_name: str
    confidence: float                   # 0.0 ~ 1.0
    method: str                         # "keyword" | "llm" | "fallback"
    reasoning: str
    scores: dict[str, float] = field(default_factory=dict)  # 전 에이전트 점수


@dataclass
class PromptSet:
    """LLM 매니저(4단계)에 전달되는 최종 프롬프트 셋.

    ai_router.stream_chat(messages=ps.messages, system=ps.system) 으로 바로 사용.
    """
    system: str                         # 에이전트 페르소나 (마크다운 전체)
    messages: list[dict]                # [{"role": "user"|"assistant", "content": str}]
    agent_key: str
    agent_name: str
    routing: RoutingResult


# ══════════════════════════════════════════════════════════════════════════════
# 2. 에이전트 레지스트리 (키워드 규칙 포함)
# ══════════════════════════════════════════════════════════════════════════════

_AGENT_REGISTRY: list[AgentProfile] = [

    AgentProfile(
        key         = "os_agent",
        name        = "OS Control Agent",
        description = (
            "사용자의 PC 화면을 직접 조작해 실제 작업을 수행 — 프로그램/앱 실행 및 종료, "
            "파일·폴더 열기, 웹 브라우저 탐색 및 검색, 마우스 클릭, 텍스트 입력, 스크롤, "
            "단축키, 스크린샷 등 화면 제어가 필요한 모든 요청. "
            "단순히 정보를 설명하거나 대화·조언을 나누는 요청은 해당하지 않음."
        ),
        prompt_file = "os_agent.md",
        keywords    = {
            # 실행/종료
            r"\b(열어|켜줘|켜|실행해|실행시켜|시작해|꺼줘|종료해|닫아)\b":           1.8,
            # 탐색/검색
            r"\b(검색해|찾아줘|찾아봐|들어가|접속해|이동해|이동하)\b":               1.6,
            # 클릭/조작
            r"\b(클릭|눌러|선택해|드래그)\b":                                       1.8,
            # 입력
            r"\b(입력해|타이핑|쳐줘|작성해줘)\b":                                   1.4,
            # 화면/캡처
            r"\b(스크롤|캡처|스크린샷|화면을|화면에)\b":                            1.6,
            # 파일/다운로드
            r"\b(다운로드|설치해|저장해|복사해|붙여넣)\b":                          1.3,
            # 앱/사이트 이름
            r"\b(크롬|브라우저|네이버|유튜브|구글|엣지|chrome|browser|youtube|google)\b": 1.7,
        },
    ),

    AgentProfile(
        key         = "executive_assistant",
        name        = "Executive Assistant",
        description = "일정·업무·이메일·회의·프로젝트 관리",
        prompt_file = "executive_assistant.md",
        keywords    = {
            # 일정·캘린더
            r"\b(일정|스케줄|schedule|calendar|캘린더)\b":          2.0,
            r"\b(예약|예약하|booking|book|reserve)\b":              1.8,
            r"\b(회의|미팅|meeting|conference|콜)\b":               1.8,
            r"\b(리마인더|reminder|알림|alarm)\b":                  1.5,
            # 업무·태스크
            r"\b(할일|태스크|task|to.?do|업무|작업)\b":             1.8,
            r"\b(마감|deadline|기한|due)\b":                         1.6,
            r"\b(프로젝트|project|기획|계획|plan)\b":                1.4,
            r"\b(우선순위|priority|중요)\b":                         1.3,
            # 커뮤니케이션
            r"\b(이메일|email|메일|mail|draft|초안)\b":              1.7,
            r"\b(보고서|report|문서|document|doc)\b":                1.4,
            r"\b(발표|presentation|ppt|슬라이드)\b":                 1.3,
        },
    ),

    AgentProfile(
        key         = "health_coach",
        name        = "Health Coach",
        description = "식단·영양·운동·수면·체성분 관리",
        prompt_file = "health_coach.md",
        keywords    = {
            # 식단·영양
            r"\b(식단|diet|다이어트|nutrition|영양)\b":              2.0,
            r"\b(칼로리|calorie|kcal|단백질|protein)\b":             1.9,
            r"\b(탄수화물|carb|지방|fat|식이)\b":                    1.7,
            r"\b(식사|meal|아침|점심|저녁|breakfast|lunch|dinner)\b": 1.5,
            r"\b(보충제|supplement|비타민|vitamin|protein powder)\b": 1.6,
            # 운동·피트니스
            r"\b(운동|exercise|workout|트레이닝|training|gym|헬스)\b": 2.0,
            r"\b(근력|근육|muscle|strength|weight lifting|벌크)\b":   1.8,
            r"\b(유산소|cardio|달리기|running|런닝|사이클)\b":         1.7,
            r"\b(스트레칭|stretching|flexibility|mobility)\b":        1.4,
            # 회복·수면
            r"\b(수면|sleep|불면|피로|recovery|휴식)\b":              1.6,
            r"\b(체중|weight|몸무게|체지방|bmi)\b":                   1.5,
            r"\b(건강|health|몸|신체|피부|face)\b":                   1.0,
        },
    ),

    AgentProfile(
        key         = "life_coach",
        name        = "Life Coach",
        description = "일상 대화·감정·동기부여·인간관계·개인 성장",
        prompt_file = "life_coach.md",
        keywords    = {
            # 감정·멘탈
            r"\b(힘들|지쳐|우울|불안|걱정|스트레스|stress|anxiety)\b": 2.0,
            r"\b(외로움|외로|lonely|sad|슬프|감정|emotion)\b":         1.9,
            r"\b(화가|짜증|frustrated|angry|분노|화남)\b":             1.7,
            r"\b(행복|happy|기쁨|joy|감사|grateful)\b":               1.4,
            # 동기·성장
            r"\b(동기|motivation|의욕|습관|habit|루틴|routine)\b":     1.9,
            r"\b(목표|goal|꿈|dream|성장|growth|발전)\b":              1.7,
            r"\b(자신감|confidence|자존감|self.esteem)\b":             1.8,
            r"\b(게으름|procrastinat|미루|lazy)\b":                    1.6,
            # 인간관계
            r"\b(친구|friend|가족|family|연인|partner|relationship)\b": 1.8,
            r"\b(갈등|conflict|싸움|fight|화해|apologize)\b":          1.7,
            r"\b(외톨이|소통|communication|대화|말)\b":                 1.4,
            # 일상 잡담
            r"\b(요즘|lately|어떻게|how are|오늘|today|일상)\b":        0.8,
            r"\b(고민|걱정|advice|조언|어떡해|어쩌)\b":                 1.5,
        },
        is_default  = True,   # 분류 불명확 시 기본값
    ),
]

# key → profile 빠른 조회
_REGISTRY_MAP: dict[str, AgentProfile] = {p.key: p for p in _AGENT_REGISTRY}
_DEFAULT_AGENT: AgentProfile = next(p for p in _AGENT_REGISTRY if p.is_default)


# ══════════════════════════════════════════════════════════════════════════════
# 3. 1차 분류 — KeywordScorer (표준 라이브러리만 사용)
# ══════════════════════════════════════════════════════════════════════════════

class _KeywordScorer:
    """정규식 패턴 가중치 합산으로 에이전트별 점수를 계산한다."""

    def __init__(self) -> None:
        # 컴파일된 패턴 캐시: {(agent_key, pattern): re.Pattern}
        self._cache: dict[tuple, re.Pattern] = {}

    def _compile(self, agent_key: str, pattern: str) -> re.Pattern:
        k = (agent_key, pattern)
        if k not in self._cache:
            self._cache[k] = re.compile(pattern, re.IGNORECASE)
        return self._cache[k]

    def score(self, text: str) -> dict[str, float]:
        """각 에이전트의 원시 가중치 점수를 반환."""
        raw: dict[str, float] = {}
        for profile in _AGENT_REGISTRY:
            total = sum(profile.keywords.values())          # 최대 가능 점수
            earned = 0.0
            for pattern, weight in profile.keywords.items():
                rx = self._compile(profile.key, pattern)
                # 매칭 횟수에 비례해 점수 누적 (최대 3회 캡)
                matches = min(len(rx.findall(text)), 3)
                earned += weight * matches
            raw[profile.key] = earned / total if total else 0.0
        return raw

    def classify(self, text: str) -> RoutingResult:
        scores = self.score(text)
        best_key = max(scores, key=scores.__getitem__)
        best_score = scores[best_key]

        # 점수가 너무 낮으면 기본 에이전트로 폴백
        if best_score < 0.05:
            profile = _DEFAULT_AGENT
            return RoutingResult(
                agent_key  = profile.key,
                agent_name = profile.name,
                confidence = 0.0,
                method     = "fallback",
                reasoning  = "키워드 매칭 점수 없음 — 기본 에이전트 사용",
                scores     = scores,
            )

        # 0~1 정규화 (전체 점수 합 기준)
        total = sum(scores.values()) or 1.0
        normalized = {k: v / total for k, v in scores.items()}
        confidence = normalized[best_key]

        profile = _REGISTRY_MAP[best_key]
        return RoutingResult(
            agent_key  = profile.key,
            agent_name = profile.name,
            confidence = round(confidence, 3),
            method     = "keyword",
            reasoning  = (
                f"키워드 점수 상위: {best_key}={best_score:.3f} "
                f"(정규화 {confidence:.1%})"
            ),
            scores     = {k: round(v, 4) for k, v in scores.items()},
        )


# ══════════════════════════════════════════════════════════════════════════════
# 4. 2차 분류 — LlmClassifier (신뢰도 낮을 때만 호출)
# ══════════════════════════════════════════════════════════════════════════════

# LLM에게 전달하는 분류 전용 시스템 프롬프트
_CLASSIFIER_SYSTEM_PROMPT = """\
You are JARVIS's intent classifier. Your ONLY job is to classify the user's message
into exactly one of the following agent categories.

Available agents:
{agent_descriptions}

Special attention — "os_agent":
Choose os_agent ONLY if the user is asking JARVIS to PHYSICALLY DO something on the
PC screen RIGHT NOW (open/run/close a program, browse to a site, search something on
a website, click, type, scroll, take a screenshot, etc.) — i.e. an executable action
that requires controlling the mouse/keyboard.
Do NOT choose os_agent for: emotional talk, opinions, advice, explanations, general
questions, or requests to just "tell/explain/recommend" something verbally — even if
the sentence happens to mention an app or website name.

Examples:
- "크롬 켜서 네이버에 대전 날씨 검색해줘" → os_agent (실제 화면 조작 필요)
- "메모장 열어서 회의록 제목 적어줘" → os_agent (실제 화면 조작 필요)
- "유튜브에서 롤 하이라이트 영상 찾아서 재생해줘" → os_agent (실제 화면 조작 필요)
- "오늘 기분이 좀 안좋아" → life_coach (감정/대화, 화면 조작 아님)
- "크롬이 요즘 너무 느려서 짜증나" → life_coach (불평/대화, 화면 조작 요청 아님)
- "유튜브 알고리즘 추천 어떻게 생각해?" → life_coach (의견/대화)
- "오늘 회의 일정 알려줘" → executive_assistant (정보 조회, 화면 조작 아님)

Rules:
- Return ONLY a valid JSON object. No markdown, no explanation, no extra text.
- Choose the single best-fit agent_key.
- Set confidence between 0.0 (uncertain) and 1.0 (certain).

Required output format:
{{
  "agent_key": "<one of the agent keys above>",
  "confidence": <float 0.0-1.0>,
  "reasoning": "<one sentence explaining the choice>"
}}
"""


class _LlmClassifier:
    """LLM을 사용한 정밀 분류 — ai_router 의존 없이 직접 API 호출."""

    def _build_system_prompt(self) -> str:
        desc_lines = "\n".join(
            f"- {p.key}: {p.description}"
            for p in _AGENT_REGISTRY
        )
        return _CLASSIFIER_SYSTEM_PROMPT.format(agent_descriptions=desc_lines)

    def _parse_response(self, raw: str) -> Optional[dict]:
        """LLM 응답에서 JSON을 안전하게 추출."""
        raw = raw.strip()
        # 마크다운 코드 블록 제거
        raw = re.sub(r"```(?:json)?", "", raw).strip("`").strip()
        # 첫 번째 {...} 추출
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            return None
        try:
            data = json.loads(match.group())
            if "agent_key" in data and data["agent_key"] in _REGISTRY_MAP:
                return data
        except (json.JSONDecodeError, KeyError):
            pass
        return None

    async def classify(self, user_text: str) -> Optional[RoutingResult]:
        """Claude Code CLI를 호출하여 분류 결과를 반환. 실패 시 None."""
        try:
            from app.services.claude_code import CCStatusEvent, CCTextDelta, get_wrapper

            system = self._build_system_prompt()
            full_response: list[str] = []

            wrapper = get_wrapper()
            # 분류는 채팅 세션과 무관한 1회성 호출이므로 세션을 이어가지 않으며,
            # 분류 호출이 끝난 뒤 실제 대화 세션 ID를 덮어쓰지 않도록 복원한다.
            saved_session_id = wrapper.session_id
            try:
                async for ev in wrapper.stream(user_text, system=system, resume=False):
                    if isinstance(ev, CCTextDelta):
                        full_response.append(ev.text)
                    elif isinstance(ev, CCStatusEvent):
                        print(f"[Router] Claude Code 분류 호출 실패: {ev.message}", file=sys.stderr)
                        return None
            finally:
                wrapper.session_id = saved_session_id

            raw = "".join(full_response)
            parsed = self._parse_response(raw)
            if not parsed:
                return None

            key     = parsed["agent_key"]
            profile = _REGISTRY_MAP[key]
            return RoutingResult(
                agent_key  = key,
                agent_name = profile.name,
                confidence = float(parsed.get("confidence", 0.8)),
                method     = "llm",
                reasoning  = parsed.get("reasoning", "LLM 분류"),
                scores     = {},
            )

        except Exception as e:
            print(f"[Router] LLM 분류 실패: {e}", file=sys.stderr)
            return None


# ══════════════════════════════════════════════════════════════════════════════
# 5. 프롬프트 파일 로더
# ══════════════════════════════════════════════════════════════════════════════

def load_agent_prompt(agent_key: str) -> str:
    """agent_key에 해당하는 prompts/{file}.md를 읽어 반환.

    파일이 없으면 jarvis_persona.md 폴백 → 그것도 없으면 빈 문자열.
    """
    profile  = _REGISTRY_MAP.get(agent_key, _DEFAULT_AGENT)
    target   = _PROMPTS_DIR / profile.prompt_file
    fallback = _PROMPTS_DIR / "jarvis_persona.md"

    for path in (target, fallback):
        if path.exists():
            try:
                return path.read_text(encoding="utf-8")
            except OSError as e:
                print(f"[Router] 파일 읽기 오류 {path}: {e}", file=sys.stderr)

    return ""  # 모든 폴백 실패


# ══════════════════════════════════════════════════════════════════════════════
# 6. 컨텍스트 결합 — PromptSet 빌더
# ══════════════════════════════════════════════════════════════════════════════

def _build_prompt_set(
    user_text: str,
    routing: RoutingResult,
    history: list[dict],
) -> PromptSet:
    """분류 결과 + 대화 기록 → PromptSet (LLM 매니저 입력 포맷)."""
    system_prompt = load_agent_prompt(routing.agent_key)
    system_prompt += (
        "\n\n## Language (IMPORTANT)\n"
        "Respond ONLY in Korean (한국어), written exclusively in the Hangul "
        "script (한글). Every single word of your reply must be Korean — do "
        "not mix in English, Chinese, Japanese, Vietnamese, French, or any "
        "other language, even for single words or phrases. "
        "Never use Chinese characters/Hanja (漢字, CJK ideographs) — for "
        "example write '유연성' (NOT '靈活性'), '효율성' (NOT '效率性'). "
        "If you don't know a Korean term, use a natural Korean approximation "
        "or transliteration in Hangul instead of switching scripts. "
        "Only use another language if the user explicitly asks you to."
    )

    # 히스토리에 현재 유저 메시지 추가
    messages: list[dict] = list(history) + [
        {"role": "user", "content": user_text}
    ]

    return PromptSet(
        system     = system_prompt,
        messages   = messages,
        agent_key  = routing.agent_key,
        agent_name = routing.agent_name,
        routing    = routing,
    )


# ══════════════════════════════════════════════════════════════════════════════
# 7. Public API
# ══════════════════════════════════════════════════════════════════════════════

_keyword_scorer = _KeywordScorer()
_llm_classifier = _LlmClassifier()


def classify(user_text: str) -> RoutingResult:
    """유저 텍스트 → RoutingResult (동기, 키워드 기반).

    LLM 분류가 필요한 경우 classify_async() 또는 route()를 대신 사용하세요.
    """
    if not user_text or not user_text.strip():
        raise ValueError("user_text는 비어있을 수 없습니다.")
    return _keyword_scorer.classify(user_text.strip())


async def classify_async(user_text: str, force_llm: bool = False) -> RoutingResult:
    """유저 텍스트 → RoutingResult (키워드 1차 + 신뢰도 낮으면 LLM 2차 보정).

    특정 키워드에 갇히지 않고 "PC 화면 제어가 필요한 작업 요청인지" 여부를
    문맥적으로 판단해야 하므로, 키워드 점수가 애매한 경우 LLM이 최종 판단한다.
    """
    if not user_text or not user_text.strip():
        raise ValueError("user_text는 비어있을 수 없습니다.")

    text    = user_text.strip()
    routing = _keyword_scorer.classify(text)

    if force_llm or routing.confidence < _LLM_FALLBACK_THRESHOLD:
        llm_result = await _llm_classifier.classify(text)
        if llm_result is not None:
            routing = llm_result

    return routing


async def route(
    user_text: str,
    history: list[dict] | None = None,
    force_llm: bool = False,
) -> PromptSet:
    """유저 텍스트 + 히스토리 → PromptSet.

    1차: 키워드 점수 계산
    2차: 신뢰도 < threshold 이거나 force_llm=True 이면 LLM 재분류
    최종 PromptSet을 ai_router.stream_chat()에 바로 전달 가능.

    Args:
        user_text:  유저 입력 문자열
        history:    이전 대화 메시지 리스트 [{"role": ..., "content": ...}]
        force_llm:  True이면 키워드 점수 무관하게 항상 LLM 분류 사용
    """
    if not user_text or not user_text.strip():
        raise ValueError("user_text는 비어있을 수 없습니다.")

    text    = user_text.strip()
    history = history or []

    routing = await classify_async(text, force_llm=force_llm)

    # ── 컨텍스트 결합 → PromptSet ──
    return _build_prompt_set(text, routing, history)


def list_agents() -> list[AgentProfile]:
    """등록된 전체 에이전트 프로파일 목록 반환."""
    return list(_AGENT_REGISTRY)


def get_agent(key: str) -> Optional[AgentProfile]:
    """에이전트 키로 프로파일 조회. 없으면 None."""
    return _REGISTRY_MAP.get(key)
