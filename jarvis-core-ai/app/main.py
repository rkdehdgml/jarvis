from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings
from app.routers import chat, vision, speech, health, os_control, tts, presence, proactive, tasks, resources, workspace, scheduler, telegram, weather

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="JARVIS — Core AI service (FastAPI)",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8080", "http://localhost:3000", "null"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router,      prefix="/api/health", tags=["health"])
app.include_router(chat.router,        prefix="/api/chat",   tags=["chat"])
app.include_router(speech.router,      prefix="/api/speech", tags=["speech"])
app.include_router(vision.router,      prefix="/api/vision", tags=["vision"])
app.include_router(os_control.router,  prefix="/api/os",     tags=["os-control"])
app.include_router(tts.router,      prefix="/api/tts",      tags=["tts"])
app.include_router(presence.router,  prefix="/api/presence",  tags=["presence"])
app.include_router(proactive.router, prefix="/api/proactive", tags=["proactive"])
app.include_router(tasks.router,     prefix="/api/tasks",     tags=["tasks"])
app.include_router(resources.router,  prefix="/api/resources",  tags=["resources"])
app.include_router(workspace.router,  prefix="/api/workspace",  tags=["workspace"])
app.include_router(scheduler.router,  prefix="/api/scheduler",  tags=["scheduler"])
app.include_router(telegram.router,   prefix="/api/telegram",   tags=["telegram"])
app.include_router(weather.router,    prefix="/api/weather",    tags=["weather"])


@app.on_event("startup")
async def startup():
    print(f"[JARVIS] Core AI started — active provider: {settings.ai_provider}")

    # 재석 감지 서비스 시작
    import asyncio
    from app.services.presence_service import presence as _presence
    from app.services.proactive_service import proactive as _proactive
    loop = asyncio.get_event_loop()
    _presence.start(loop)

    # 능동적 제안 서비스 시작
    _proactive.start(loop)

    # 시스템 자원 모니터링 시작
    from app.services.resource_monitor import monitor as _monitor
    _monitor.start(loop)

    # 스케줄러 서비스 시작
    from app.services.scheduler_service import scheduler as _scheduler
    _scheduler.start(loop)

    # 텔레그램 봇 시작
    from app.services.telegram_service import telegram_bot as _telegram
    _telegram.start(loop)
