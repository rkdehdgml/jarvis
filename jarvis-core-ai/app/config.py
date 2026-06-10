from pydantic_settings import BaseSettings
from typing import Literal


class Settings(BaseSettings):
    # AI Provider switching
    ai_provider: Literal["ollama", "claude", "openai", "gemini"] = "ollama"

    # Anthropic
    anthropic_api_key: str = ""
    claude_model: str = "claude-sonnet-4-6"

    # OpenAI
    openai_api_key: str = ""
    openai_model: str = "gpt-4o"

    # Google Gemini
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"

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

    # App
    app_name: str = "JARVIS Core AI"
    debug: bool = False

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
