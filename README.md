# JARVIS — Personal AI Assistant

## Architecture

```
jarvis/
├── jarvis-core-ai/           ← Python FastAPI   (port 8000)
├── jarvis-dashboard-backend/ ← Spring Boot      (port 8080)
└── jarvis-overlay/           ← Electron Overlay (데스크톱 UI)
```

## Quick Start

### 1. Python Core AI
```bash
cd jarvis-core-ai
cp .env .env.local             # 필요 시 키 값 채우기 (.env 참고)
pip install -r requirements.txt

# 개발 모드 실행 (uvicorn --reload)
uvicorn app.main:app --reload --port 8000

# 또는 main_standalone 진입점으로 실행 (jarvis-core-ai 디렉터리에서 -m으로 실행해야 함)
python -m app.main_standalone
```

### 2. Spring Boot Dashboard
```bash
cd jarvis-dashboard-backend
./mvnw spring-boot:run
# H2 Console → http://localhost:8080/h2-console
```

### 3. Electron Overlay
```bash
cd jarvis-overlay
npm install
npm start
```

## AI Engine (고정)

JARVIS의 두뇌는 **`CLAUDE_CODE`(사용자의 Claude Code 구독) 엔진으로 고정**되어
있으며, 다른 엔진으로의 전환은 허용되지 않습니다 (`ALLOWED_ENGINES`,
`app/services/llm_manager.py`). 엔진 목록 조회가 실패해도 항상 `CLAUDE_CODE`로
동작하며, 설정 모달에서 다른 엔진은 "사용 안 함"으로 표시됩니다.

`CLAUDE_CODE` 엔진을 사용하려면 시스템에 [Claude Code CLI](https://docs.claude.com/claude-code)가
설치되어 있고 로그인(OAuth)되어 있어야 합니다:
```bash
claude --version   # 설치 확인
claude /login       # 미로그인 시 인증
```
