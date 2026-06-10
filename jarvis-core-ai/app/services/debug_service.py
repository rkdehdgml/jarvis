"""
debug_service.py — JARVIS 자가 치유 / 자동 디버깅 서비스
════════════════════════════════════════════════════════════════════════════════
동작 흐름:
  1. 에러 텍스트에서 핵심 메시지 추출
  2. DuckDuckGo HTML 검색으로 관련 스니펫 수집
  3. LLM에게 에러 + 검색 결과 전달 → 수정 코드 + 설명 생성
  4. 결과 반환 (fix_code, explanation, references)

Public API:
  debug.analyze(error_text, context) → DebugResult dict
════════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import re
from typing import Optional

# ── 에러 타입 분류 패턴 ────────────────────────────────────────────────────────
_ERROR_TYPE_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"Traceback \(most recent call last\)", re.I), "Python RuntimeError"),
    (re.compile(r"SyntaxError\s*:", re.I),                     "Python SyntaxError"),
    (re.compile(r"(TypeError|ValueError|AttributeError|ImportError|KeyError|IndexError)\s*:", re.I), "Python Exception"),
    (re.compile(r"Exception in thread|java\.lang\.",   re.I),  "Java Exception"),
    (re.compile(r"TypeError|ReferenceError|Cannot read propert", re.I), "JavaScript Error"),
    (re.compile(r"error TS\d+",                        re.I),  "TypeScript Error"),
    (re.compile(r"npm ERR!|yarn error",                re.I),  "Node.js/npm Error"),
    (re.compile(r"FAILED|Build failed|Compilation failed",re.I),"Build/Compile Error"),
    (re.compile(r"ModuleNotFoundError|No module named", re.I), "Python ModuleNotFoundError"),
    (re.compile(r"PermissionError|Permission denied",  re.I),  "Permission Error"),
    (re.compile(r"ConnectionError|ECONNREFUSED|ETIMEDOUT",re.I),"Network/Connection Error"),
    (re.compile(r"CUDA|GPU|out of memory",             re.I),  "GPU/CUDA Error"),
]


def _classify_error(text: str) -> str:
    for pattern, label in _ERROR_TYPE_PATTERNS:
        if pattern.search(text):
            return label
    return "Unknown Error"


def _extract_core_error(text: str) -> str:
    """에러 텍스트에서 핵심 한 줄 메시지 추출 (검색 쿼리용)."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    # Python traceback: 마지막 줄이 핵심
    if "Traceback (most recent call last)" in text:
        for line in reversed(lines):
            if re.match(r"\w+Error:|Exception:", line):
                return line[:120]
    # 그 외: Error: 혹은 exception 포함 첫 줄
    for line in lines:
        if re.search(r"(error|exception|failed|cannot|undefined)", line, re.I):
            return line[:120]
    return lines[0][:120] if lines else text[:120]


class DebugService:
    """에러 분석 → 웹 검색 → LLM 수정 코드 생성."""

    async def analyze(
        self,
        error_text: str,
        context: str = "",
        broadcast_cb=None,   # progress 이벤트 콜백 (optional)
    ) -> dict:
        """
        Args:
            error_text:   사용자가 붙여넣은 에러 로그 전문
            context:      추가 컨텍스트 (파일명, 언어 등)
            broadcast_cb: async callable(message: str) — 진행 상황 알림
        Returns:
            {
                "error_type":   str,
                "core_error":   str,
                "fix_code":     str,
                "explanation":  str,
                "references":   list[dict],  # [{title, url}]
                "applied":      False,
            }
        """
        async def _notify(msg: str):
            if broadcast_cb:
                await broadcast_cb(msg)

        await _notify("에러 타입 분류 중...")
        error_type = _classify_error(error_text)
        core_error = _extract_core_error(error_text)

        await _notify(f"웹 검색 중: {core_error[:60]}...")
        references = await self._search_web(f"{error_type} {core_error} fix solution")

        await _notify("LLM 수정 코드 생성 중...")
        fix_code, explanation = await self._generate_fix(
            error_type, error_text, core_error, references, context
        )

        return {
            "error_type":  error_type,
            "core_error":  core_error,
            "fix_code":    fix_code,
            "explanation": explanation,
            "references":  references,
            "applied":     False,
        }

    # ── 웹 검색 (DuckDuckGo HTML) ──────────────────────────────────────────────

    async def _search_web(self, query: str) -> list[dict]:
        try:
            import httpx
            from bs4 import BeautifulSoup

            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 Chrome/120 Safari/537.36"
                )
            }
            async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
                resp = await client.post(
                    "https://html.duckduckgo.com/html/",
                    data={"q": query},
                    headers=headers,
                )

            soup = BeautifulSoup(resp.text, "html.parser")
            results: list[dict] = []

            for item in soup.select(".result")[:4]:
                title_el   = item.select_one(".result__a")
                snippet_el = item.select_one(".result__snippet")
                if not snippet_el:
                    continue
                title   = title_el.get_text(strip=True) if title_el else ""
                snippet = snippet_el.get_text(strip=True)
                href    = title_el.get("href", "") if title_el else ""
                # DuckDuckGo redirect URL에서 실제 URL 추출
                if "uddg=" in href:
                    import urllib.parse
                    parsed = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
                    href   = parsed.get("uddg", [""])[0]

                results.append({"title": title, "snippet": snippet, "url": href})

            return results

        except Exception as e:
            print(f"[Debug] 웹 검색 실패: {e}")
            return []

    # ── LLM 수정 코드 생성 ────────────────────────────────────────────────────

    async def _generate_fix(
        self,
        error_type: str,
        error_text: str,
        core_error: str,
        references: list[dict],
        context: str,
    ) -> tuple[str, str]:
        """(fix_code, explanation) 반환."""
        from app.services.llm_manager import manager
        from app.services.agent_router import PromptSet, RoutingResult

        ref_text = "\n".join(
            f"- [{r['title']}]: {r['snippet'][:200]}"
            for r in references
        ) if references else "검색 결과 없음"

        system = (
            "당신은 JARVIS의 자동 디버깅 엔진입니다. "
            "주어진 에러 로그와 참고 자료를 분석하여 반드시 두 파트로 응답하세요.\n\n"
            "응답 형식 (엄격히 준수):\n"
            "===FIX_CODE===\n"
            "<수정 코드 또는 명령어만, 설명 없이>\n"
            "===EXPLANATION===\n"
            "<한국어 원인 설명 및 수정 근거 2~4문장>"
        )

        user_content = (
            f"에러 타입: {error_type}\n"
            f"핵심 에러: {core_error}\n"
            f"추가 컨텍스트: {context or '없음'}\n\n"
            f"=== 에러 전문 ===\n{error_text[:2000]}\n\n"
            f"=== 웹 검색 참고 자료 ===\n{ref_text}"
        )

        routing = RoutingResult(
            agent_key  = "task_agent",
            agent_name = "Debug Agent",
            confidence = 1.0,
            method     = "debug",
            reasoning  = "자동 디버깅 태스크",
        )
        prompt_set = PromptSet(
            system     = system,
            messages   = [{"role": "user", "content": user_content}],
            agent_key  = "task_agent",
            agent_name = "Debug Agent",
            routing    = routing,
        )

        try:
            response = await manager.run(prompt_set, max_retries=1)
            text     = response.text or ""

            fix_code    = ""
            explanation = ""

            if "===FIX_CODE===" in text and "===EXPLANATION===" in text:
                parts = text.split("===EXPLANATION===")
                fix_part = parts[0].replace("===FIX_CODE===", "").strip()
                exp_part = parts[1].strip() if len(parts) > 1 else ""
                fix_code    = fix_part
                explanation = exp_part
            else:
                # 폴백: 전체 텍스트를 설명으로
                explanation = text.strip()

            return fix_code, explanation

        except Exception as e:
            return "", f"LLM 수정 코드 생성 실패: {e}"


debug = DebugService()
