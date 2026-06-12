import os

from pydantic_settings import BaseSettings
from typing import Literal


class Settings(BaseSettings):
    # AI Provider switching
    ai_provider: Literal["ollama", "claude", "openai", "gemini", "groq"] = "ollama"

    # Anthropic
    anthropic_api_key: str = ""
    claude_model: str = "claude-sonnet-4-6"

    # OpenAI
    openai_api_key: str = ""
    openai_model: str = "gpt-4o"

    # Google Gemini
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    gemini_classifier_model: str = "gemini-2.5-flash-lite"

    # Groq
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"

    # Ollama (local)
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.2"

    # ChromaDB (local vector store)
    chroma_persist_dir: str = "./data/chroma"

    # Whisper
    whisper_model_size: Literal["tiny", "base", "small", "medium", "large"] = "base"
    whisper_device: str = "cpu"

    # Spring Boot backend
    dashboard_backend_url: str = "http://localhost:8080"

    # Telegram Bot
    telegram_bot_token: str = ""        # BotFather에서 발급받은 토큰
    telegram_allowed_ids: str = ""      # 허용된 Chat ID 목록 (쉼표 구분)
    telegram_poll_interval: int = 5     # polling 간격(초)

    # 내장 명령 (app/commands) — 외부 API 키
    # 뉴스: https://newsapi.org 에서 무료 발급
    news_api_key: str = ""
    # Gmail SMTP 전송: Google 계정 "앱 비밀번호" 필요 (일반 로그인 비밀번호 아님)
    gmail_address: str = ""
    gmail_app_password: str = ""

    # OS Agent — PC 제어 실행 환경
    # 화면 해상도(좌표 기반 자동화 안내용). 비워두면 pyautogui.size()로 자동 감지.
    os_screen_width: int = 0
    os_screen_height: int = 0
    # Chrome 사용자 프로필 경로(기존 로그인 세션 재사용). 비워두면 OS별 기본 경로 사용.
    os_chrome_user_data_dir: str = ""
    # 스크린샷/녹화 저장 폴더
    os_captures_dir: str = "./data/captures"
    # 다운로드 폴더. 비워두면 ~/Downloads 사용.
    os_downloads_dir: str = ""
    # 재사용 가능한 자동화 스크립트 저장 폴더
    os_scripts_dir: str = "./scripts"

    # App
    app_name: str = "JARVIS Core AI"
    debug: bool = False

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()


def resolved_chrome_user_data_dir() -> str:
    """Chrome 사용자 프로필 경로. 설정값이 없으면 OS별 기본 경로를 반환한다."""
    if settings.os_chrome_user_data_dir:
        return settings.os_chrome_user_data_dir
    home = os.path.expanduser("~")
    if os.name == "nt":
        return os.path.join(home, "AppData", "Local", "Google", "Chrome", "User Data")
    if sys_platform_is_mac():
        return os.path.join(home, "Library", "Application Support", "Google", "Chrome")
    return os.path.join(home, ".config", "google-chrome")


def resolved_downloads_dir() -> str:
    """다운로드 폴더. 설정값이 없으면 ~/Downloads를 반환한다."""
    if settings.os_downloads_dir:
        return settings.os_downloads_dir
    return os.path.join(os.path.expanduser("~"), "Downloads")


def sys_platform_is_mac() -> bool:
    import sys
    return sys.platform == "darwin"
