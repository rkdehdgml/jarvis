"""AI provider router — switches between Ollama, Claude, OpenAI, and Gemini at runtime."""
from typing import AsyncGenerator
from app.config import settings


async def stream_chat(messages: list[dict], system: str = "") -> AsyncGenerator[str, None]:
    provider = settings.ai_provider

    if provider == "claude":
        async for chunk in _stream_claude(messages, system):
            yield chunk
    elif provider == "openai":
        async for chunk in _stream_openai(messages, system):
            yield chunk
    elif provider == "gemini":
        async for chunk in _stream_gemini(messages, system):
            yield chunk
    else:
        async for chunk in _stream_ollama(messages, system):
            yield chunk


async def _stream_claude(messages: list[dict], system: str) -> AsyncGenerator[str, None]:
    import anthropic
    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    async with client.messages.stream(
        model=settings.claude_model,
        max_tokens=4096,
        system=system,
        messages=messages,
    ) as stream:
        async for text in stream.text_stream:
            yield text


async def _stream_openai(messages: list[dict], system: str) -> AsyncGenerator[str, None]:
    from openai import AsyncOpenAI
    client = AsyncOpenAI(api_key=settings.openai_api_key)
    full_messages = ([{"role": "system", "content": system}] if system else []) + messages
    async for chunk in await client.chat.completions.create(
        model=settings.openai_model,
        messages=full_messages,
        stream=True,
    ):
        delta = chunk.choices[0].delta.content
        if delta:
            yield delta


async def _stream_gemini(messages: list[dict], system: str) -> AsyncGenerator[str, None]:
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=settings.gemini_api_key)
    contents = [
        types.Content(
            role  = "model" if m.get("role") == "assistant" else "user",
            parts = [types.Part.from_text(text=m.get("content", ""))],
        )
        for m in messages
    ]
    stream = await client.aio.models.generate_content_stream(
        model    = settings.gemini_model,
        contents = contents,
        config   = types.GenerateContentConfig(system_instruction=system or None),
    )
    async for chunk in stream:
        if chunk.text:
            yield chunk.text


async def _stream_ollama(messages: list[dict], system: str) -> AsyncGenerator[str, None]:
    import httpx, json
    full_messages = ([{"role": "system", "content": system}] if system else []) + messages
    async with httpx.AsyncClient(timeout=120) as client:
        async with client.stream(
            "POST",
            f"{settings.ollama_base_url}/api/chat",
            json={"model": settings.ollama_model, "messages": full_messages, "stream": True},
        ) as resp:
            async for line in resp.aiter_lines():
                if line:
                    data = json.loads(line)
                    content = data.get("message", {}).get("content", "")
                    if content:
                        yield content
