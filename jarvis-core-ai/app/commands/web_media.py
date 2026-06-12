"""
web_media.py — 웹 / 미디어 내장 명령
════════════════════════════════════════════════════════════════════════════════
지원 명령:
  · 유튜브 음악 재생 (yt-dlp로 검색 → 기본 브라우저로 재생)
  · 유튜브 음악 다운로드 (yt-dlp, mp3)
  · 브라우저 검색 (구글 또는 네이버 — 명시하지 않으면 구글)
  · URL / 구글 앱 / SNS 계정 / 쇼핑 사이트 열기
  · 위키피디아 검색 (5줄 요약)
  · 최신 뉴스 읽기 (NewsAPI — 키 필요)
  · WikiHow 방식 "~하는 방법" 안내
════════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import asyncio
import re
import webbrowser
from pathlib import Path
from urllib.parse import quote

from app.commands.registry import CommandResult, register
from app.config import settings, resolved_downloads_dir

# ── 자주 쓰는 사이트 — 구글 앱 / SNS / 쇼핑 ────────────────────────────────────
SITES: dict[str, str] = {
    "유튜브": "https://youtube.com",
    "구글": "https://google.com",
    "지메일": "https://mail.google.com",
    "메일": "https://mail.google.com",
    "캘린더": "https://calendar.google.com",
    "구글 캘린더": "https://calendar.google.com",
    "드라이브": "https://drive.google.com",
    "구글 드라이브": "https://drive.google.com",
    "지도": "https://maps.google.com",
    "구글 지도": "https://maps.google.com",
    "페이스북": "https://facebook.com",
    "인스타그램": "https://instagram.com",
    "트위터": "https://twitter.com",
    "엑스": "https://x.com",
    "네이버": "https://naver.com",
    "네이버 쇼핑": "https://shopping.naver.com",
    "쿠팡": "https://www.coupang.com",
    "11번가": "https://www.11st.co.kr",
    "지마켓": "https://www.gmarket.co.kr",
    "아마존": "https://www.amazon.com",
    "깃허브": "https://github.com",
    "위키피디아": "https://ko.wikipedia.org",
}

_SITE_NAMES_PATTERN = "|".join(re.escape(n) for n in sorted(SITES, key=len, reverse=True))


# ══════════════════════════════════════════════════════════════════════════════
# 1. 유튜브 음악 재생
# ══════════════════════════════════════════════════════════════════════════════

@register("유튜브 재생", r"(?:유튜브에서\s*)?(.+?)\s*(?:음악|노래)?\s*(?:틀어줘|재생해줘|들려줘)")
async def play_youtube(m, text: str) -> CommandResult:
    query = m.group(1).strip()
    if not query:
        return CommandResult(text="재생할 곡 제목을 말씀해 주세요.")

    loop = asyncio.get_event_loop()

    def _search():
        import yt_dlp
        opts = {"format": "best", "quiet": True, "noplaylist": True, "default_search": "ytsearch1"}
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(query, download=False)
            if "entries" in info:
                info = info["entries"][0]
            return info["webpage_url"], info.get("title", query)

    try:
        url, title = await loop.run_in_executor(None, _search)
    except Exception as e:
        return CommandResult(text=f"'{query}' 검색에 실패했습니다: {e}")

    webbrowser.open(url)
    return CommandResult(text=f"'{title}'을 재생합니다.", data={"url": url})


# ══════════════════════════════════════════════════════════════════════════════
# 2. 유튜브 음악 다운로드
# ══════════════════════════════════════════════════════════════════════════════

@register("유튜브 다운로드", r"(.+?)\s*(?:음악|노래)?\s*다운로드(?:해줘|받아줘)")
async def download_youtube(m, text: str) -> CommandResult:
    query = m.group(1).strip()
    if not query:
        return CommandResult(text="다운로드할 곡 제목을 말씀해 주세요.")

    loop = asyncio.get_event_loop()
    download_dir = Path(resolved_downloads_dir())
    download_dir.mkdir(parents=True, exist_ok=True)

    def _download():
        import yt_dlp
        opts = {
            "format": "bestaudio/best",
            "quiet": True,
            "noplaylist": True,
            "default_search": "ytsearch1",
            "outtmpl": str(download_dir / "%(title)s.%(ext)s"),
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }],
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(query, download=True)
            if "entries" in info:
                info = info["entries"][0]
            return info.get("title", query)

    try:
        title = await loop.run_in_executor(None, _download)
    except Exception as e:
        return CommandResult(text=f"'{query}' 다운로드에 실패했습니다: {e} (ffmpeg가 설치되어 있어야 mp3 변환이 가능합니다.)")

    return CommandResult(text=f"'{title}'을 다운로드했습니다: {download_dir}")


# ══════════════════════════════════════════════════════════════════════════════
# 3. 브라우저 검색
# ══════════════════════════════════════════════════════════════════════════════

_SEARCH_ENGINES: dict[str, str] = {
    "구글": "https://www.google.com/search?q={}",
    "네이버": "https://search.naver.com/search.naver?query={}",
}


@register(
    "브라우저 검색",
    r"^(?:(네이버|구글)에서\s+)?(.+?)\s*(?:을|를)?\s*(?:(네이버|구글)에서)?\s*검색해줘$",
)
async def browser_search(m, text: str) -> CommandResult:
    engine = m.group(1) or m.group(3) or "구글"
    query = m.group(2).strip()
    url = _SEARCH_ENGINES[engine].format(quote(query))
    webbrowser.open(url)
    return CommandResult(text=f"{engine}에서 '{query}'을 검색했습니다.", data={"url": url})


# ══════════════════════════════════════════════════════════════════════════════
# 4. URL / 구글 앱 / SNS / 쇼핑 사이트 열기
# ══════════════════════════════════════════════════════════════════════════════

@register("사이트 열기", rf"^({_SITE_NAMES_PATTERN})\s*(?:을|를)?\s*(?:열어줘|접속해줘|이동해줘)$")
async def open_site(m, text: str) -> CommandResult:
    name = m.group(1).strip()
    url = SITES[name]
    webbrowser.open(url)
    return CommandResult(text=f"{name}을 열었습니다.", data={"url": url})


@register("URL 열기", r"(https?://[^\s]+)\s*(?:을|를)?\s*(?:열어줘|접속해줘|이동해줘)?")
async def open_url(m, text: str) -> CommandResult:
    url = m.group(1).strip()
    webbrowser.open(url)
    return CommandResult(text=f"{url}을 열었습니다.", data={"url": url})


# ══════════════════════════════════════════════════════════════════════════════
# 5. 위키피디아 검색 (5줄 요약)
# ══════════════════════════════════════════════════════════════════════════════

@register("위키피디아 검색", r"위키(?:피디아)?(?:에서)?\s*(.+?)\s*(?:검색해줘|찾아줘|알려줘)")
async def search_wikipedia(m, text: str) -> CommandResult:
    query = m.group(1).strip()
    loop = asyncio.get_event_loop()

    def _search():
        import wikipedia
        wikipedia.set_lang("ko")
        return wikipedia.summary(query, sentences=5, auto_suggest=True, redirect=True)

    try:
        summary = await loop.run_in_executor(None, _search)
    except Exception as e:
        return CommandResult(text=f"'{query}'에 대한 위키피디아 검색에 실패했습니다: {e}")

    return CommandResult(text=summary)


# ══════════════════════════════════════════════════════════════════════════════
# 6. 최신 뉴스 읽기 (NewsAPI)
# ══════════════════════════════════════════════════════════════════════════════

@register("최신 뉴스", r"(?:최신|오늘)?\s*뉴스\s*(?:읽어줘|알려줘|보여줘)")
async def read_news(m, text: str) -> CommandResult:
    if not settings.news_api_key:
        return CommandResult(
            text="뉴스 기능을 사용하려면 .env의 NEWS_API_KEY를 설정해야 합니다. "
                 "https://newsapi.org 에서 무료 키를 발급받을 수 있습니다."
        )

    loop = asyncio.get_event_loop()

    def _fetch():
        import requests
        resp = requests.get(
            "https://newsapi.org/v2/top-headlines",
            params={"country": "kr", "pageSize": 5, "apiKey": settings.news_api_key},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json().get("articles", [])

    try:
        articles = await loop.run_in_executor(None, _fetch)
    except Exception as e:
        return CommandResult(text=f"뉴스를 불러오지 못했습니다: {e}")

    if not articles:
        return CommandResult(text="현재 표시할 뉴스가 없습니다.")

    lines = [f"{i+1}. {a.get('title', '')}" for i, a in enumerate(articles)]
    return CommandResult(text="오늘의 주요 뉴스입니다.\n" + "\n".join(lines))


# ══════════════════════════════════════════════════════════════════════════════
# 7. WikiHow 방식의 "~하는 방법" 안내
# ══════════════════════════════════════════════════════════════════════════════

@register("하는 방법", r"(.+?)\s*(?:하는\s*방법|어떻게\s*해)(?:\s*알려줘|\s*가르쳐줘)?$")
async def how_to(m, text: str) -> CommandResult:
    query = m.group(1).strip()
    loop = asyncio.get_event_loop()

    def _fetch():
        import requests
        from bs4 import BeautifulSoup

        resp = requests.get(
            "https://www.wikihow.com/wikiHowTo",
            params={"search": query},
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        link = soup.select_one("a.result_link")
        if not link or not link.get("href"):
            return None, None

        article_url = link["href"]
        article = requests.get(article_url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        article.raise_for_status()
        a_soup = BeautifulSoup(article.text, "html.parser")
        steps = [li.get_text(" ", strip=True) for li in a_soup.select("div.step")][:7]
        return article_url, steps

    try:
        url, steps = await loop.run_in_executor(None, _fetch)
    except Exception as e:
        return CommandResult(text=f"'{query}' 방법을 찾는 데 실패했습니다: {e}")

    if not url or not steps:
        return CommandResult(text=f"'{query}'에 대한 WikiHow 문서를 찾지 못했습니다.")

    lines = "\n".join(f"{i+1}. {s}" for i, s in enumerate(steps))
    return CommandResult(text=f"'{query}' 하는 방법입니다.\n{lines}\n\n자세히 보기: {url}")
