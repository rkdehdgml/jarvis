# JARVIS Personal AI Assistant — 빌드 및 배포 가이드

> **프로젝트 목적**  
> 영화 아이언맨의 자비스처럼 Windows 바탕화면에 동적으로 녹아드는 투명 오버레이 HUD AI 비서 시스템.  
> 유저의 일상 전반(루틴·건강·커리어·지식 스크랩 등)을 보조하며, 음성·박수·클릭으로 즉시 깨어나  
> 로컬 무료 AI(Ollama)부터 Claude·GPT-4o까지 자유롭게 전환하며 자연어로 PC를 제어한다.

---

## 목차

1. [아키텍처 개요](#1-아키텍처-개요)
2. [필수 사전 조건](#2-필수-사전-조건)
3. [환경 변수 설정](#3-환경-변수-설정)
4. [개발 환경 실행 (빌드 없이)](#4-개발-환경-실행-빌드-없이)
5. [프로덕션 빌드 — 단일 .exe 생성](#5-프로덕션-빌드--단일-exe-생성)
6. [알려진 이슈 및 수정 방법](#6-알려진-이슈-및-수정-방법)
7. [트러블슈팅](#7-트러블슈팅)

---

## 1. 아키텍처 개요

```
┌─────────────────────────────────────────────────────────┐
│           Electron HUD Overlay  (투명 데스크탑 창)        │
│   IDLE 구체 ←→ LISTENING ←→ CHATTING ←→ WORKING         │
└────────┬────────────────────────────────────┬───────────┘
         │ HTTP REST / WebSocket              │ WebSocket
         │ localhost:8080                     │ /ws-status
         ▼                                   ▼
┌─────────────────────────────────────────────────────────┐
│       Spring Boot Dashboard Backend  (port 8080)        │
│  ├─ /api/tasks        — 할 일 관리 (CRUD)               │
│  ├─ /api/logs         — 대화·OS 실행 이력               │
│  ├─ /api/ngrok        — ngrok 터널 설정 UI              │
│  ├─ /api/chat/*  ──→  FastAPI(8000) 프록시              │
│  ├─ /api/os/*    ──→  FastAPI(8000) 프록시              │
│  ├─ H2 내장 DB   (AppData\jarvis-project\data)          │
│  └─ WebSocket 상태 브로드캐스트 (/ws-status)             │
└────────────────────┬────────────────────────────────────┘
                     │ HTTP  localhost:8000
                     ▼
┌─────────────────────────────────────────────────────────┐
│          Python FastAPI Core AI  (port 8000)            │
│  ├─ /api/chat/stream  — LLM 스트리밍 응답               │
│  ├─ /api/os/run       — OS 자동화 (NDJSON)             │
│  ├─ /api/speech/*     — Whisper 음성 인식              │
│  ├─ /api/vision/*     — 손동작·얼굴 감지               │
│  ├─ agent_router      — 6개 페르소나 자동 분류          │
│  └─ llm_manager       — Ollama / Claude / GPT-4o       │
└─────────────────────────────────────────────────────────┘
         ▲
         │ WebSocket  localhost:8080/ws-status
┌────────┴───────────┐
│  Python Wake Sensor │  박수 2번 → WAKE_UP 신호
│  (jarvis-wake.exe)  │
└────────────────────┘
```

### 포트 정리

| 서비스 | 포트 | 설명 |
|--------|------|------|
| Python FastAPI | 8000 | AI 엔진 · OS 제어 · 음성 |
| Spring Boot | 8080 | DB · WebSocket · ngrok |
| Electron | — | 데스크탑 UI (포트 없음) |
| Ollama | 11434 | 로컬 LLM (별도 설치) |

---

## 2. 필수 사전 조건

### 2-1. 공통 (필수)

| 소프트웨어 | 버전 | 확인 명령 |
|-----------|------|-----------|
| Node.js | 20 LTS 이상 | `node -v` |
| Java JDK | 17 이상 | `java -version` |
| Maven | 3.8 이상 | `mvn -v` |
| Python | 3.10 이상 | `python --version` |

### 2-2. Python 패키지

```bash
cd jarvis-core-ai
pip install -r requirements.txt
```

> **주의**: `requirements.txt`에서 아래 두 항목은 현재 미사용이므로 제거를 권장합니다.
> ```
> # chromadb       ← 벡터DB, 미구현
> # langchain       ← 미사용
> ```
> 설치 시간이 크게 단축됩니다.

### 2-3. AI 엔진 준비 (선택)

| 엔진 | 준비 방법 |
|------|-----------|
| **Ollama (무료, 로컬)** | https://ollama.ai 에서 설치 후 `ollama pull deepseek-r1:7b` |
| **Claude** | https://console.anthropic.com 에서 API 키 발급 |
| **OpenAI** | https://platform.openai.com 에서 API 키 발급 |

> 최초 실행 시 Ollama만 있어도 동작합니다.

### 2-4. ngrok (외부 접근 필요 시)

ngrok 고정 도메인은 **유료 플랜(Basic 이상)** 이 필요합니다.  
로컬에서만 사용하는 경우 ngrok 설정 없이 IDLE 상태로 건너뛸 수 있습니다.

---

## 3. 환경 변수 설정

`jarvis-core-ai/.env` 파일 생성:

```env
# AI 엔진 선택 (ollama | claude | openai)
AI_PROVIDER=ollama

# Ollama 로컬 서버
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=deepseek-r1:7b

# Claude (선택)
ANTHROPIC_API_KEY=sk-ant-xxxxxxxxxxxxxxxx
CLAUDE_MODEL=claude-sonnet-4-6

# OpenAI (선택)
OPENAI_API_KEY=sk-xxxxxxxxxxxxxxxx
OPENAI_MODEL=gpt-4o

# Whisper 음성 인식
WHISPER_MODEL_SIZE=base
WHISPER_DEVICE=cpu

# 내부 서비스
DASHBOARD_BACKEND_URL=http://localhost:8080

# 디버그
DEBUG=false
```

---

## 4. 개발 환경 실행 (빌드 없이)

세 개의 터미널을 열어 각각 실행합니다.

### 터미널 1 — Python FastAPI (AI 코어)

```bash
cd jarvis-core-ai
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### 터미널 2 — Spring Boot 백엔드

```bash
cd jarvis-dashboard-backend
mvn spring-boot:run
```

또는 IntelliJ / VS Code에서 `JarvisDashboardApplication.java` 실행

### 터미널 3 — Electron HUD

```bash
cd jarvis-overlay
npm install       # 최초 1회만
npm start
```

### (선택) 박수 각성 센서 별도 실행

```bash
cd jarvis-core-ai
python app/sensors/wake_sensor.py
```

---

## 5. 프로덕션 빌드 — 단일 .exe 생성

### 5-1. 사전 준비 확인

```
jarvis-overlay/build/
  ├── icon.ico        ✅ 생성됨
  ├── LICENSE.txt     ✅ 생성됨
  └── installer.nsh   ✅ 생성됨
```

### 5-2. Step 1 — Spring Boot JAR 빌드

```bash
cd jarvis-dashboard-backend
mvn clean package -DskipTests
# 결과물: target/jarvis-dashboard-backend-0.1.0.jar
```

### 5-3. Step 2 — Python FastAPI 실행파일 빌드

> ⚠️ `sounddevice`는 PortAudio DLL을 포함시켜야 합니다.  
> 아래 명령을 그대로 사용하세요.

```bash
cd jarvis-core-ai

pip install pyinstaller

# Windows 기준 — PortAudio DLL 포함
pyinstaller \
  --onefile \
  --name jarvis-core \
  --hidden-import=sounddevice \
  --hidden-import=numpy \
  --hidden-import=websockets \
  --hidden-import=anthropic \
  --hidden-import=openai \
  --collect-all sounddevice \
  --add-data "prompts;prompts" \
  app/main_standalone.py

# 결과물을 지정 위치로 복사
mkdir -p ../jarvis-python-dist
cp dist/jarvis-core.exe ../jarvis-python-dist/
```

> **참고**: `app/main_standalone.py`는 FastAPI 서버를 단독 실행하는 엔트리포인트입니다.  
> 아래 [6-1 항목](#6-1-fastapi-패키징-누락)을 먼저 적용해 주세요.

### Step 2-b — 박수 센서 별도 빌드

```bash
cd jarvis-core-ai

pyinstaller \
  --onefile \
  --name jarvis-wake \
  --collect-all sounddevice \
  --distpath ../jarvis-python-dist \
  app/sensors/wake_sensor.py
```

### 5-4. Step 3 — Electron 인스톨러 빌드

```bash
cd jarvis-overlay
npm install
npm run build
# 결과물: dist/JARVIS-Setup-0.1.0.exe
```

### 5-5. 전체 자동 빌드 (한 번에)

```bash
cd jarvis-overlay
npm run build:all
```

### 5-6. 결과물 위치

```
jarvis-overlay/dist/
  └── JARVIS-Setup-0.1.0.exe    ← 최종 배포 인스톨러
```

설치 후 자동 생성되는 사용자 데이터 위치:

```
%APPDATA%\jarvis-project\
  ├── data\jarvis.mv.db    ← H2 데이터베이스 (대화 기록, 설정)
  └── logs\                ← 실행 로그
```

---

## 6. 알려진 이슈 및 수정 방법

### 6-1. FastAPI 패키징 누락 🔴

**증상**: 설치 후 채팅, OS 제어, 음성 인식 전혀 동작 안 함  
**원인**: `main.js`가 Spring Boot JAR과 박수 센서만 실행하고 FastAPI 서버를 실행하지 않음

**수정 — `jarvis-core-ai/app/main_standalone.py` 생성**:

```python
"""단독 실행 엔트리포인트 — PyInstaller 빌드용"""
import uvicorn
from app.main import app

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
```

**수정 — `jarvis-overlay/main.js`에 FastAPI 시작 함수 추가**:

```js
function startFastAPI() {
  const exePath = getResourcePath('jarvis-python', 'jarvis-core.exe')

  if (app.isPackaged && !fs.existsSync(exePath)) {
    console.warn('[Main] jarvis-core.exe 없음 — FastAPI 건너뜀')
    return
  }

  const [cmd, args] = app.isPackaged
    ? [exePath, []]
    : ['python', [path.join(__dirname, '..', 'jarvis-core-ai', 'app', 'main_standalone.py')]]

  console.log('[Main] FastAPI Core AI 시작:', cmd)
  fastApiProc = spawn(cmd, args, {
    detached: false,
    stdio: ['ignore', 'pipe', 'pipe'],
  })
  fastApiProc.stdout.on('data', d => process.stdout.write(`[PY·API] ${d}`))
  fastApiProc.stderr.on('data', d => process.stderr.write(`[PY·API·ERR] ${d}`))
  fastApiProc.on('exit', code => console.log(`[Main] FastAPI 종료 (code: ${code})`))
}
```

`app.whenReady()` 안에 `startFastAPI()` 호출 추가, `before-quit`에 `fastApiProc?.kill('SIGTERM')` 추가.

---

### 6-2. renderer.js 포트 불일치 🔴

**증상**: 채팅 입력 시 404 오류  
**원인**: `renderer.js`가 `localhost:8080`(Spring Boot)에 `/api/chat/stream`을 호출하지만 이 엔드포인트는 FastAPI(8000)에 있음

**수정 방법 A (권장) — Spring Boot에 프록시 컨트롤러 추가**:

`jarvis-dashboard-backend`에 `ChatProxyController.java` 생성:

```java
@RestController
@RequestMapping("/api/chat")
@RequiredArgsConstructor
public class ChatProxyController {

    private final CoreAiClient coreAiClient;   // WebClient (port 8000)

    @PostMapping(value = "/stream", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    public Flux<String> stream(@RequestBody Map<String, Object> body) {
        return coreAiClient.post()
            .uri("/api/chat/stream")
            .bodyValue(body)
            .retrieve()
            .bodyToFlux(String.class);
    }
}
```

**수정 방법 B (빠른 수정) — renderer.js에서 직접 FastAPI 호출**:

```js
const CHAT_URL = 'http://localhost:8000'   // FastAPI 직접 호출
// fetch 시 BASE_URL 대신 CHAT_URL 사용
```

---

### 6-3. `/api/health` 경로 불일치 🔴

**증상**: 앱 시작 시 30초 대기 후 타임아웃  
**원인**: `main.js`가 `localhost:8080/api/health`를 폴링하지만 Spring Boot Actuator는 `/actuator/health` 사용

**수정 — `jarvis-overlay/main.js`**:

```js
// 수정 전
const HEALTH_URL = `${SPRING_BOOT_URL}/api/health`

// 수정 후
const HEALTH_URL = `${SPRING_BOOT_URL}/actuator/health`
```

또는 Spring Boot에 `/api/health` 엔드포인트를 직접 추가:

```java
// HealthController.java
@RestController
@RequestMapping("/api")
public class HealthController {
    @GetMapping("/health")
    public Map<String, String> health() {
        return Map.of("status", "UP");
    }
}
```

---

### 6-4. PyInstaller + sounddevice PortAudio 누락 🟡

**증상**: `jarvis-wake.exe` 실행 시 `OSError: PortAudio library not found`  
**원인**: PortAudio DLL이 번들에 포함되지 않음

**수정 — PyInstaller 명령에 플래그 추가** (5-3 Step 2 참고):

```bash
pyinstaller --onefile --name jarvis-wake \
  --collect-all sounddevice \           # ← 이 플래그 필수
  --distpath ../jarvis-python-dist \
  app/sensors/wake_sensor.py
```

---

### 6-5. ChromaDB / LangChain 미사용 의존성 🟡

**증상**: `pip install -r requirements.txt` 에서 20분 이상 소요  
**수정 — `requirements.txt`에서 주석 처리**:

```
# chromadb==0.5.0     # 벡터DB — 미구현 기능, 필요 시 활성화
# langchain==0.3.0    # 미사용
```

---

## 7. 트러블슈팅

### Q. Electron 창이 뜨지 않아요

1. Spring Boot 로그 확인: `%APPDATA%\jarvis-project\logs\`
2. Electron DevTools 열기: 창 위에서 `Ctrl+Shift+I` (개발 환경)
3. Spring Boot가 8080 포트를 사용 중인지 확인: `netstat -ano | findstr :8080`

### Q. 채팅이 응답 없이 멈춰요

1. FastAPI 실행 확인: `http://localhost:8000/docs` 브라우저로 접속
2. Ollama 실행 확인: `ollama list`
3. `.env` 파일의 API 키 유효성 확인

### Q. 박수를 쳐도 반응 없어요

1. 마이크 권한 확인 (Windows 설정 → 개인 정보 → 마이크)
2. 임계값 낮추기: `wake_sensor.py --threshold 0.15`
3. 사용 가능한 마이크 목록 확인: `python wake_sensor.py --list-devices`

### Q. ngrok 터널이 연결 안 돼요

1. ngrok 유료 플랜(Basic 이상) 필요 — 무료 플랜은 고정 도메인 미지원
2. 인증 토큰 재확인: `ngrok config check`
3. 방화벽에서 ngrok.exe 허용 여부 확인

### Q. H2 콘솔에 접근하고 싶어요

브라우저에서 `http://localhost:8080/h2-console` 접속  
JDBC URL: `jdbc:h2:file:~/AppData/Roaming/jarvis-project/data/jarvis`  
사용자명: `jarvis` / 비밀번호: `jarvis`

### Q. 처음 설치 후 SETUP 화면이 안 나와요

ngrok를 사용하지 않는 경우 정상입니다. Spring Boot가 `configured=false`를 반환하면 설정 화면이 표시됩니다.  
ngrok 없이 바로 IDLE로 진행하려면 `renderer.js`의 `init()`에서 catch 분기(`enterIdle()`)를 확인하세요.

---

## 참고: 파일 구조 요약

```
jarvis/
├── jarvis-core-ai/          Python FastAPI — AI 엔진 (port 8000)
│   ├── app/
│   │   ├── main.py
│   │   ├── config.py
│   │   ├── routers/         chat, os_control, speech, vision, health
│   │   ├── services/        llm_manager, agent_router, os_agent
│   │   └── sensors/         vision_sensor, voice_sensor, wake_sensor
│   ├── prompts/             LLM 시스템 프롬프트 (6개 에이전트)
│   └── requirements.txt
│
├── jarvis-dashboard-backend/ Spring Boot — DB·WebSocket (port 8080)
│   └── src/main/java/
│       ├── controller/      Task, DailyLog, NgrokController
│       ├── entity/          Task, DailyLog, NgrokConfig, ChatMessage
│       ├── service/         NgrokService
│       └── websocket/       JarvisStatusHandler
│
├── jarvis-overlay/          Electron — 투명 HUD 오버레이
│   ├── main.js              메인 프로세스 (자식 프로세스 관리)
│   ├── preload.js           IPC 보안 브릿지
│   ├── renderer/            index.html, renderer.js, style.css
│   └── build/               icon.ico, LICENSE.txt, installer.nsh
│
└── BUILD.md                 ← 이 문서
```

---

*JARVIS Personal AI Assistant — BUILD.md*  
*최종 수정: 2026-06-08*
