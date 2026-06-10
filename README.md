# JARVIS — Personal AI Assistant

## Architecture

```
jarvis/
├── jarvis-core-ai/          ← Python FastAPI  (port 8000)
└── jarvis-dashboard-backend/ ← Spring Boot     (port 8080)
```

## Quick Start

### 1. Python Core AI
```bash
cd jarvis-core-ai
cp .env.example .env          # fill in API keys
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

### 2. Spring Boot Dashboard
```bash
cd jarvis-dashboard-backend
./mvnw spring-boot:run
# H2 Console → http://localhost:8080/h2-console
```

## AI Provider Switching
| Provider | Command |
|---|---|
| Local (Ollama) | `PUT /api/chat/provider/ollama` |
| Claude | `PUT /api/chat/provider/claude` |
| OpenAI | `PUT /api/chat/provider/openai` |
