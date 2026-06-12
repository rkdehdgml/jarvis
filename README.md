# JARVIS — Personal AI Assistant

음성/텍스트로 대화하며 PC를 직접 제어하고, 일정·뉴스·미디어·메모 등을 관리해주는
개인용 AI 비서입니다. 두뇌는 **Claude Code CLI**로 고정되어 있어, 내장 명령으로
처리되지 않는 모든 요청은 사용자의 Claude Code 구독을 통해 응답합니다.

## 아키텍처

```
jarvis/
├── jarvis-core-ai/           ← Python FastAPI   (포트 8000) — AI 두뇌 / 명령 처리
├── jarvis-dashboard-backend/ ← Spring Boot      (포트 8080) — 작업/일정 대시보드 백엔드
└── jarvis-overlay/           ← Electron Overlay        — 데스크톱 오버레이 UI
```

| 컴포넌트 | 역할 |
|---|---|
| **jarvis-core-ai** | 채팅/음성 라우팅, 에이전트(페르소나) 분류, 내장 명령 디스패처, OS 제어, TTS/STT, 일정/리마인더, 텔레그램 봇 |
| **jarvis-dashboard-backend** | 작업·일정 데이터 저장(H2 DB) 및 대시보드 API |
| **jarvis-overlay** | 항상 위에 떠 있는 데스크톱 오버레이 (전체/미니 모드), 음성 인식 트리거, TTS 출력 |

## 기술 스택

- **AI Core**: Python 3.12, FastAPI, Pydantic v2, Uvicorn
- **AI 엔진**: Claude Code CLI (`claude -p`, headless subprocess 스트리밍) — 고정 엔진
- **음성**: faster-whisper(STT), edge-tts(TTS, 한국어 `ko-KR-SunHiNeural`/`ko-KR-InJoonNeural`)
- **비전/자동화**: OpenCV, mediapipe, pyautogui, pyperclip, selenium
- **대시보드**: Spring Boot 3 (Java 21), Spring Data JPA, H2, WebSocket, Actuator
- **데스크톱 UI**: Electron

## 핵심 기능

### 1. 에이전트(페르소나) 라우팅
사용자 발화를 Claude Code 분류기가 분석해 아래 페르소나 중 하나로 라우팅합니다
(`app/services/agent_router.py`, `prompts/*.md`).

| 에이전트 | 역할 |
|---|---|
| `os_agent` | 실제 화면 조작이 필요한 PC 제어 작업 |
| `executive_assistant` | 일정/정보 조회, 비서 업무 |
| `health_coach` | 건강/생활 습관 코칭 |
| `life_coach` | 감정적 대화, 의견/조언 (기본값) |

### 2. 내장 명령 (`app/commands/`, 총 41개)
키워드 패턴에 매칭되면 Claude Code를 거치지 않고 즉시 처리됩니다(`chat.py` "0-c" 단계).
매칭되지 않는 모든 요청은 자동으로 Claude Code CLI(`CLAUDE_CODE` 엔진)로 위임됩니다.

- **시스템 제어**: 볼륨 조절/음소거, 종료·재시작·절전(+취소), 앱 실행/종료(`data/apps.json`),
  스크린샷, 화면+음성 녹화, 음성만 녹음
- **정보 제공**: 현재 시간/요일, IP 주소, 인터넷 속도, 시스템 상태(CPU/RAM/디스크), 현재 위치
- **웹/미디어**: 유튜브 재생/다운로드(yt-dlp), 브라우저 검색(구글/네이버), URL·사이트 열기,
  위키피디아 5줄 요약, 최신 뉴스(NewsAPI), WikiHow 방식 "~하는 방법" 안내
- **커뮤니케이션**: Gmail 전송(SMTP), WhatsApp 개인/그룹 메시지(pywhatkit)
- **유틸리티**: PDF 읽기(TTS), QR코드 생성, 연락처 추가/검색, 웹캠 사진, 프로그래밍 농담,
  오늘 일정, 대기 타이머("N분 후 깨워줘"), 슬립 모드("wake up"/"일어나"까지 무시)

### 3. Claude Code CLI 연동 (전체 폴백)
내장 명령에 매칭되지 않는 모든 요청은 `app/services/claude_code/wrapper.py`를 통해
`claude -p --output-format stream-json --include-partial-messages`로 headless 호출되고,
응답이 실시간 스트리밍되어 화면 출력 + TTS로 재생됩니다.

### 4. 오버레이 UI
- 항상 최상단에 떠 있는 Electron 창
- 자동 제어 시작 시 우측 하단 80x80 **미니 모드**로 축소, 종료 시 복원
- 음성 명령("wake up" 등) 인식 및 TTS 출력

### 5. 일정/작업 대시보드
Spring Boot + H2 기반으로 작업/일정 데이터를 저장하고 웹 대시보드로 조회합니다.

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

> 화면 녹화 / 유튜브 mp3 다운로드 기능에는 시스템에 **ffmpeg**가 설치되어 PATH에
> 등록되어 있어야 합니다. Windows 볼륨 제어(pycaw)는 Windows 환경에서만 동작합니다.

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

## 환경 변수 (`.env`)

`jarvis-core-ai/.env`에서 관리합니다. 빈 값으로 두면 해당 기능은 에러 대신
안내 메시지를 반환합니다.

| 변수 | 용도 |
|---|---|
| `NEWS_API_KEY` | "최신 뉴스 읽어줘" — https://newsapi.org 무료 발급 |
| `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD` | Gmail 전송 — Google 계정 "앱 비밀번호" 필요 |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_IDS` | 텔레그램 봇 연동 |
| `WHISPER_MODEL_SIZE`, `WHISPER_DEVICE` | 음성 인식(STT) 모델 설정 |
| `OS_*` (config.py) | 스크린샷/녹화/다운로드 경로, Chrome 프로필 경로 등 |

> `AI_PROVIDER` 및 각 LLM API 키(Anthropic/OpenAI/Gemini/Groq/Ollama)는 분류기 보조용으로
> 남아있을 수 있으나, 실제 채팅 응답 엔진은 항상 `CLAUDE_CODE`로 고정됩니다.

## 사용 예시

```
"메모장 열어줘"            → 시스템 제어(앱 실행)
"지금 몇 시야"             → 정보 제공(현재 시간)
"고양이를 네이버에서 검색해줘" → 웹/미디어(브라우저 검색)
"OOO에게 이메일 보내줘, 제목은 ..., 내용은 ..." → 커뮤니케이션(Gmail 전송)
"30분 후에 깨워줘"          → 유틸리티(대기 타이머)
"파이썬 정렬 코드 작성해줘"  → (매칭 실패) → Claude Code CLI로 위임
```

## 테스트

```bash
cd jarvis-core-ai
python -m pytest -q
```
