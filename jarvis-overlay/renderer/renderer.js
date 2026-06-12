/**
 * renderer.js — JARVIS HUD Renderer Process
 * ─────────────────────────────────────────────────────────────────────────────
 * 실행 흐름:
 *   1. Spring Boot GET /api/ngrok/config 호출
 *   2. configured=false → SETUP 모달 표시
 *      configured=true  → IDLE 구체 표시 + WebSocket 연결
 *   3. 설정 완료 시: Fade-out 모달 → IDLE 전환 + WebSocket 연결
 */

'use strict'

// ── 백엔드 기본 URL ──────────────────────────────────────────────────────────
const BASE_URL = 'http://localhost:8080'
const AI_URL   = 'http://localhost:8000'
const WS_URL   = 'ws://localhost:8080/ws-status'

// ── DOM 참조 ─────────────────────────────────────────────────────────────────
const $ = id => document.getElementById(id)
const escHtml = s => s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
const sphereScene  = $('sphereScene')
const ngrokModal   = $('ngrokModal')
const sphereCore   = $('sphereCore')
const sphereLabel  = $('sphereLabel')
const engineLabel  = $('engineLabel')
const hudAgent     = $('hudAgent')
const hudTime      = $('hudTime')
const messages     = $('messages')
const chatInput    = $('chatInput')
// OS 레이아웃 요소
const osModeBadge  = $('osModeBadge')

// ── 앱 상태 ───────────────────────────────────────────────────────────────────
let state        = 'INIT'   // INIT | SETUP | IDLE | LISTENING | CHATTING | WORKING
let engineKey    = 'OLLAMA_DEEPSEEK'
let agentKey     = 'life_coach'
let ws           = null
let isStreaming  = false
let ttsEnabled        = true
let currentAudio      = null
let logTotal          = 0
let permissionMode    = null   // 'FULL' | 'LIMITED' | null
let screenEventSource = null
let screenFrameCount  = 0
let screenFpsTimer    = null
let userName          = 'Sir'  // 사용자 호칭
let chatHistory       = []     // 누적 대화 이력 [{role: 'user'|'assistant', content: string}]
const CHAT_HISTORY_MAX = 20     // 컨텍스트로 전송할 최대 메시지 수
let _pendingDangerPlan  = null  // 위험 확인 대기 중인 플랜
let presenceEventSource  = null  // 재석 감지 SSE
let proactiveEventSource = null  // 능동적 제안 SSE
let taskEventSource      = null  // 백그라운드 태스크 SSE
let activeTasks          = {}    // task_id → description
let resourceEventSource  = null  // 시스템 자원 SSE
let schedulerEventSource = null  // 스케줄러 SSE
let telegramEventSource  = null  // 텔레그램 원격 제어 SSE
let mediaRecorder    = null   // 음성 녹음기
let _recordChunks    = []     // 녹음 청크 버퍼
let _voiceCancelled  = false  // 녹음 취소 플래그
const RECORD_MS      = 5000   // 최대 녹음 시간 (ms)

// ══════════════════════════════════════════════════════════════════════════════
// 1. 로그 창 시스템
// ══════════════════════════════════════════════════════════════════════════════

const LOG_BADGE = {
  info:    'INFO',
  success: ' OK ',
  error:   'ERR ',
  warn:    'WARN',
  chat:    'CHAT',
  tts:     'TTS ',
  os:      ' OS ',
}
const MAX_LOG = 200

function appendLog(message, type = 'info') {
  const now   = new Date()
  const time  = now.toTimeString().slice(0, 8)
  const badge = LOG_BADGE[type] || 'INFO'

  const logBody = $('logBody')

  const entry = document.createElement('div')
  entry.className = `log-entry log-${type}`
  entry.innerHTML =
    `<span class="log-time">${time}</span>` +
    `<span class="log-badge">${badge}</span>` +
    `<span class="log-msg">${escHtml(message)}</span>`

  logBody.appendChild(entry)

  // 최대 항목 초과 시 오래된 항목 제거
  const entries = logBody.children
  while (entries.length > MAX_LOG) entries[0].remove()

  logBody.scrollTop = logBody.scrollHeight

  // 카운터 갱신
  logTotal++
  $('logCount').textContent = logTotal

  // 별도 로그 창이 열려 있으면 IPC로 전달
  window.jarvis?.sendLog?.({ time, type, message })
}

function toggleLogPanel() {
  const panel = $('logPanel')
  const btn   = $('logToggleBtn')
  const collapsed = panel.classList.toggle('collapsed')
  btn.textContent = collapsed ? '+' : '−'
}

// ══════════════════════════════════════════════════════════════════════════════
// 2. 초기화 — ngrok 설정 확인
// ══════════════════════════════════════════════════════════════════════════════
async function init() {
  updateClock()
  setInterval(updateClock, 1000)
  appendLog('JARVIS 시스템 초기화 중...', 'info')

  // 1. 사용자 설정 로드
  const settings = await window.jarvis?.getSettings() ?? {}
  if (settings.userName) {
    userName = settings.userName
    updateUserLabel(userName)
    appendLog(`사용자 호칭: ${userName}`, 'info')
  }

  // 2. 권한 동의 확인 (최우선)
  const perm = await window.jarvis?.getPermission() ?? { mode: null }
  permissionMode = perm.mode

  if (!perm.mode) {
    appendLog('첫 실행 감지 — 권한 동의 화면 표시', 'warn')
    enterPermission()
    return
  }

  appendLog(`권한 모드: ${perm.mode === 'FULL' ? '풀 액세스' : '기능 제한'}`, 'info')
  applyPermissionMode(perm.mode)
  await checkNgrokAndInit()
}

async function checkNgrokAndInit() {
  try {
    const res      = await fetch(`${BASE_URL}/api/ngrok/config`)
    const data     = await res.json()
    const settings = await window.jarvis?.getSettings() ?? {}

    if (!data.configured && !settings.ngrokSkipped) {
      appendLog('ngrok 설정 필요 — 설정 화면 표시', 'warn')
      enterSetup()
    } else {
      if (!data.configured) {
        appendLog('ngrok 모바일 연동 비활성화됨 (나중에 채팅에서 "/ngrok"으로 설정 가능)', 'warn')
      } else {
        appendLog('백엔드 연결 성공', 'success')
      }
      await syncActiveEngineWithBackend()
      enterIdle()
      connectWebSocket()
    }
  } catch (e) {
    appendLog(`백엔드 연결 실패: ${e.message}`, 'warn')
    enterIdle()
    retryWebSocket()
  }

  // FULL 모드에서만 재석 감지 및 능동적 제안 시작
  if (permissionMode !== 'LIMITED') {
    startPresenceStream()
    startProactiveStream()
  }
  // 태스크 스트림은 모드 무관하게 항상 연결
  startTaskStream()
  // 자원 모니터링 스트림
  startResourceStream()
  // 스케줄러 스트림
  startSchedulerStream()
  // 텔레그램 원격 제어 알림 스트림
  startTelegramStream()
  // OS 패널 초기화 (날씨, 달력, 앰비언트)
  initOsPanels()
}

// ══════════════════════════════════════════════════════════════════════════════
// 3. 상태 전환 함수
// ══════════════════════════════════════════════════════════════════════════════

// ── OS 모드 상태 전환 (전체화면 — 패널 숨기기 없음) ─────────────────────────

function _setOsMode(label, sphereClass) {
  if (osModeBadge) osModeBadge.textContent = label
  sphereScene.className = 'sphere-zone ' + (sphereClass || '')
  $('chatPlaceholder')?.classList.add('hidden')
}

function enterPermission() {
  state = 'PERMISSION'
  $('permModal').classList.remove('hidden')
}
function enterSetup() {
  state = 'SETUP'
  $('ngrokModal').classList.remove('hidden')
}

function enterIdle() {
  state = 'IDLE'
  stopVoiceRecording(true)
  $('ngrokModal').classList.add('hidden')
  _setOsMode('IDLE', '')
  sphereLabel.textContent = 'JARVIS'
  stopScreenStream()
  $('screenPreview').classList.add('hidden')
  window.jarvis?.setIgnoreMouse?.(false)
  exitMiniMode()
}

function enterListening() {
  state = 'LISTENING'
  _setOsMode('LISTENING', 'listening')
  sphereLabel.textContent = 'LISTENING'
  startVoiceRecording()
}

function enterChatting() {
  if (state === 'CHATTING') return
  state = 'CHATTING'
  $('ngrokModal').classList.add('hidden')
  _setOsMode('CHATTING', '')
  chatInput.focus()
}

function enterWorking() {
  state = 'WORKING'
  _setOsMode('WORKING', 'working')
  sphereLabel.textContent = 'WORKING'
  if (permissionMode !== 'LIMITED') startScreenStream()
  enterMiniMode()
}

function exitChatting() {
  enterIdle()
}

// ══════════════════════════════════════════════════════════════════════════════
// 3-1. 미니 모드 — 자동 제어 중 우측 하단 80x80 창으로 축소
// ══════════════════════════════════════════════════════════════════════════════
function enterMiniMode() {
  document.body.classList.add('mini-mode')
  $('miniMode').classList.remove('hidden')
  setMiniModeStatus('자비스가 작업을 준비하는 중...')
  window.jarvis?.enterMiniMode?.()
}

function exitMiniMode() {
  document.body.classList.remove('mini-mode')
  $('miniMode').classList.add('hidden')
  window.jarvis?.exitMiniMode?.()
}

function setMiniModeStatus(text) {
  const el = $('miniMode')
  if (el) el.title = `자비스 : ${text}`
}

// ══════════════════════════════════════════════════════════════════════════════
// 4. 권한 동의 제출
// ══════════════════════════════════════════════════════════════════════════════
async function submitPermission() {
  const selected = document.querySelector('input[name="permMode"]:checked')
  if (!selected) return

  const btn     = $('permSubmitBtn')
  const btnText = $('permSubmitText')
  const spinner = $('permSubmitSpinner')
  btn.disabled = true
  btnText.classList.add('hidden')
  spinner.classList.remove('hidden')

  const mode = selected.value   // 'FULL' | 'LIMITED'
  permissionMode = mode

  try {
    await window.jarvis?.setPermission(mode)

    // 사용자 이름 저장
    const nameInput = $('inputUserName')?.value.trim()
    if (nameInput) {
      userName = nameInput
      await window.jarvis?.setSettings({ userName: nameInput })
      updateUserLabel(userName)
      appendLog(`사용자 호칭 저장: ${userName}`, 'success')
    }
  } catch (e) {
    appendLog(`권한 저장 오류: ${e.message}`, 'error')
  }

  const modeLabel = mode === 'FULL' ? '풀 액세스' : '기능 제한'
  appendLog(`권한 동의 완료: ${modeLabel} 모드`, 'success')
  applyPermissionMode(mode)

  if (mode === 'LIMITED') {
    appendLog('기능 제한 모드 — PC 제어·센서 비활성화됨', 'warn')
  }

  $('permModal').style.animation = 'fadeOut 0.5s ease-out forwards'
  setTimeout(async () => {
    $('permModal').classList.add('hidden')
    await checkNgrokAndInit()
  }, 520)
}

// ══════════════════════════════════════════════════════════════════════════════
// 5. ngrok 설정 제출
// ══════════════════════════════════════════════════════════════════════════════
async function submitNgrokConfig() {
  const authToken = $('inputAuthToken').value.trim()
  const domain    = $('inputDomain').value.trim()
  const errorEl   = $('modalError')
  const btn       = $('setupBtn')
  const btnText   = $('setupBtnText')
  const spinner   = $('setupBtnSpinner')

  errorEl.classList.add('hidden')

  if (!authToken) { showModalError('Auth Token을 입력해 주세요.'); return }
  if (!domain)    { showModalError('도메인 주소를 입력해 주세요.'); return }

  btn.disabled = true
  btnText.classList.add('hidden')
  spinner.classList.remove('hidden')

  try {
    const res  = await fetch(`${BASE_URL}/api/ngrok/config`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ authToken, domain }),
    })
    const data = await res.json()

    if (!res.ok || !data.success) {
      throw new Error(data.error || '서버 오류가 발생했습니다.')
    }

    appendLog(`ngrok 설정 완료: ${domain}`, 'success')

    // ── Fade-out 모달 → IDLE 전환 ────────────────────────────────────────
    ngrokModal.style.animation = 'fadeOut 0.6s ease-out forwards'
    setTimeout(() => {
      enterIdle()
      connectWebSocket()
    }, 620)

  } catch (e) {
    appendLog(`ngrok 설정 실패: ${e.message}`, 'error')
    showModalError(e.message)
    btn.disabled = false
    btnText.classList.remove('hidden')
    spinner.classList.add('hidden')
  }
}

function showModalError(msg) {
  const el = $('modalError')
  el.textContent = '⚠ ' + msg
  el.classList.remove('hidden')
}

// ── ngrok 설정 나중에 하기 ────────────────────────────────────────────────────
async function skipNgrokSetup() {
  await window.jarvis?.setSettings({ ngrokSkipped: true })
  appendLog('ngrok 모바일 연동을 건너뛰었습니다 (채팅창에 "/ngrok" 입력 시 다시 설정 가능)', 'warn')

  ngrokModal.style.animation = 'fadeOut 0.6s ease-out forwards'
  setTimeout(() => {
    ngrokModal.style.animation = ''
    enterIdle()
    connectWebSocket()
  }, 620)
}

// ══════════════════════════════════════════════════════════════════════════════
// 6. WebSocket — Spring Boot 실시간 상태 수신
// ══════════════════════════════════════════════════════════════════════════════
function connectWebSocket() {
  if (ws && ws.readyState === WebSocket.OPEN) return

  ws = new WebSocket(WS_URL)

  ws.onopen = () => {
    appendLog('WebSocket 연결됨 (Spring Boot :8080)', 'success')
  }

  ws.onmessage = ({ data }) => {
    try {
      const event = JSON.parse(data)
      handleWsEvent(event)
    } catch { /* 무시 */ }
  }

  ws.onclose = () => {
    appendLog('WebSocket 연결 끊김 — 3초 후 재연결 시도', 'warn')
    setTimeout(connectWebSocket, 3000)
  }

  ws.onerror = () => ws.close()
}

function retryWebSocket() {
  setTimeout(connectWebSocket, 3000)
}

function handleWsEvent(event) {
  switch (event.event) {
    case 'status':
      updateEngineInfo(event.engine_key, event.agent_key)
      if (event.engine_key) appendLog(`엔진: ${event.engine_key}  에이전트: ${event.agent_key || '-'}`, 'info')
      if (state !== 'CHATTING') {
        const stateMap = { IDLE: enterIdle, LISTENING: enterListening, WORKING: enterWorking }
        stateMap[event.state]?.()
      }
      break

    case 'engine_change':
      updateEngineInfo(event.engine_key, agentKey)
      appendLog(`LLM 엔진 전환 → ${event.engine_key}`, 'info')
      flashSphere()
      break

    case 'ngrok_ready':
      appendLog(`ngrok 터널 활성화: ${event.domain}`, 'success')
      break

    case 'wake_up':
      if (permissionMode === 'LIMITED') {
        appendLog('wake_up 신호 무시됨 (기능 제한 모드)', 'warn')
        break
      }
      appendLog('박수 신호 감지 — LISTENING 모드 진입', 'info')
      if (state === 'IDLE' || state === 'LISTENING') {
        enterListening()
        // 녹음(5s) + STT 처리 시간을 고려한 안전 타임아웃
        setTimeout(() => { if (state === 'LISTENING') { stopVoiceRecording(true); enterIdle() } }, 20000)
      }
      break

    case 'os_action':
      if (permissionMode === 'LIMITED') break
      if (state === 'WORKING') {
        try {
          const action = JSON.parse(event.payload)
          if (action.event === 'plan') {
            appendLog(`OS 계획 수립: ${action.action_count}개 액션`, 'os')
            setMiniModeStatus(action.thought || 'OS 계획을 수립했습니다...')
          } else if (action.event === 'danger') {
            appendLog(`⚠ 위험 작업 감지: ${action.risk_reason}`, 'warn')
            exitMiniMode()
            showDangerConfirmDialog(action)
          } else if (action.event === 'start') {
            appendLog(action.log || `액션 #${action.index} 시작`, 'os')
            sphereLabel.textContent = action.log?.slice(0, 18) + '...' || 'WORKING'
            setMiniModeStatus(action.log || `액션 #${action.index} 진행 중...`)
          } else if (action.event === 'done') {
            appendLog(`완료: ${action.log || `액션 #${action.index}`} (${action.duration_ms}ms)`, 'success')
          } else if (action.event === 'error') {
            appendLog(`오류: ${action.error || `액션 #${action.index} 실패`}`, 'error')
          } else if (action.event === 'finish') {
            const msg = action.aborted
              ? `OS 작업 중단: ${action.reason}`
              : `OS 작업 완료 — 성공 ${action.success}/${action.total}`
            appendLog(msg, action.aborted ? 'error' : 'success')
            setMiniModeStatus(msg)
          }
        } catch { /* 무시 */ }
      }
      break
  }
}

// ══════════════════════════════════════════════════════════════════════════════
// 7. 채팅 기능
// ══════════════════════════════════════════════════════════════════════════════
async function sendMessage() {
  const text = chatInput.value.trim()
  if (!text || isStreaming) return

  chatInput.value = ''
  appendLog(`사용자: ${text.slice(0, 60)}${text.length > 60 ? '...' : ''}`, 'chat')
  appendMessage('user', text)

  isStreaming = true
  const assistantEl = appendMessage('assistant', '자비스 : 생각하는 중...', true)

  // 화면 제어(PC 자동화)가 필요한 작업인지 사전 분류
  const cls = await preClassifyMessage(text)
  if (cls?.agent_key === 'os_agent') {
    if (permissionMode === 'LIMITED') {
      isStreaming = false
      assistantEl.remove()
      showLimitedModeWarning()
      return
    }
    appendLog(`PC 제어 요청으로 분류됨 (${cls.method}, 신뢰도 ${parseFloat(cls.confidence || 0).toFixed(2)})`, 'os')
    await runOsCommand(text, assistantEl)
    return
  }

  assistantEl.textContent = ''

  try {
    const res = await fetch(`${BASE_URL}/api/chat/stream`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ message: text, history: chatHistory.slice(-CHAT_HISTORY_MAX), user_name: userName }),
    })

    if (!res.ok) {
      const errText = await res.text()
      appendLog(`채팅 서버 오류: HTTP ${res.status}`, 'error')
      assistantEl.textContent = `[자비스 오류] AI 서버 응답 실패 (HTTP ${res.status}). FastAPI 서버가 실행 중인지 확인하세요.`
      assistantEl.classList.remove('streaming')
      return
    }

    const reader = res.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    let sseBuffer = ''

    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      sseBuffer += decoder.decode(value, { stream: true })

      // SSE 프레이밍("data: ...\n\n") 파싱 — 완전한 이벤트만 추출
      const events = sseBuffer.split('\n\n')
      sseBuffer = events.pop() ?? ''

      for (const event of events) {
        for (const line of event.split('\n')) {
          if (!line.startsWith('data:')) continue
          let content = line.slice(5)
          if (content.startsWith(' ')) content = content.slice(1)
          // 메타 청크(\x00으로 시작) 제외
          const textChunk = content.replace(/\x00\{.*?\}/gs, '')
          if (textChunk) {
            buffer += textChunk
            assistantEl.textContent = `자비스 : ${buffer}`
            assistantEl.classList.add('streaming')
            messages.scrollTop = messages.scrollHeight
          }
        }
      }
    }
    assistantEl.classList.remove('streaming')

    // 헤더에서 에이전트 정보 갱신
    const agentKeyHeader = res.headers.get('X-Agent-Key')
    const routeMethod    = res.headers.get('X-Route-Method')
    const confidence     = res.headers.get('X-Confidence')
    if (agentKeyHeader) {
      updateAgentBadge(agentKeyHeader)
      appendLog(
        `응답 완료 — 에이전트: ${agentKeyHeader}  분류: ${routeMethod}(${parseFloat(confidence || 0).toFixed(2)})  ${buffer.length}자`,
        'chat'
      )
    }

    // 워크스페이스 전환 처리
    const wsKey = res.headers.get('X-Workspace-Key')
    if (wsKey) {
      updateWorkspaceBadge(wsKey)
      appendLog(`워크스페이스 전환: ${wsKey}`, 'success')
    }

    // 대화 이력 누적 (다음 요청의 컨텍스트로 사용)
    if (buffer) {
      chatHistory.push({ role: 'user', content: text })
      chatHistory.push({ role: 'assistant', content: buffer })
      if (chatHistory.length > CHAT_HISTORY_MAX) {
        chatHistory = chatHistory.slice(-CHAT_HISTORY_MAX)
      }
    }

    // TTS 음성 출력
    if (buffer) speakText(buffer)

  } catch (e) {
    appendLog(`채팅 오류: ${e.message}`, 'error')
    assistantEl.textContent = `[오류] ${e.message}`
    assistantEl.classList.remove('streaming')
  } finally {
    isStreaming = false
  }
}

// ══════════════════════════════════════════════════════════════════════════════
// 7-1. PC 화면 제어(OS 자동화) 명령 실행
//   /api/os/run → NDJSON 스트림(plan/start/done/error/finish/danger)을 직접 소비
// ══════════════════════════════════════════════════════════════════════════════
async function runOsCommand(command, assistantEl) {
  assistantEl.textContent = '자비스 : 화면을 분석하고 작업을 준비하는 중...'
  enterWorking()

  try {
    const res = await fetch(`${BASE_URL}/api/os/run`, {
      method:  'POST',
      headers: {
        'Content-Type':       'application/json',
        'X-Jarvis-Permission': permissionMode || 'FULL',
      },
      body: JSON.stringify({ command }),
    })

    if (!res.ok) {
      appendLog(`PC 제어 서버 오류: HTTP ${res.status}`, 'error')
      assistantEl.textContent = `[자비스 오류] PC 제어 요청 실패 (HTTP ${res.status})`
      assistantEl.classList.remove('streaming')
      enterIdle()
      return
    }

    const reader  = res.body.getReader()
    const decoder = new TextDecoder()
    let ndjsonBuffer = ''

    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      ndjsonBuffer += decoder.decode(value, { stream: true })

      const lines = ndjsonBuffer.split('\n')
      ndjsonBuffer = lines.pop() ?? ''

      for (const line of lines) {
        if (!line.trim()) continue
        let action
        try { action = JSON.parse(line) } catch { continue }

        switch (action.event) {
          case 'planning':
            assistantEl.textContent = `자비스 : ${action.message}`
            setMiniModeStatus(action.message)
            break

          case 'plan':
            appendLog(`OS 계획 수립: ${action.action_count}개 액션 — ${action.thought}`, 'os')
            assistantEl.textContent = `자비스 : ${action.thought}`
            setMiniModeStatus(action.thought)
            break

          case 'danger':
            appendLog(`⚠ 위험 작업 감지: ${action.risk_reason}`, 'warn')
            assistantEl.textContent = `자비스 : ⚠ 위험한 작업이 감지되어 실행을 보류했습니다 — ${action.risk_reason}`
            assistantEl.classList.remove('streaming')
            exitMiniMode()
            showDangerConfirmDialog(action)
            break

          case 'start':
            appendLog(action.log || `액션 #${action.index} 시작`, 'os')
            sphereLabel.textContent = (action.log || 'WORKING').slice(0, 18) + '...'
            assistantEl.textContent = `자비스 : ${action.log || `액션 #${action.index} 진행 중...`}`
            setMiniModeStatus(action.log || `액션 #${action.index} 진행 중...`)
            break

          case 'done':
            appendLog(`완료: ${action.log || `액션 #${action.index}`} (${action.duration_ms}ms)`, 'success')
            break

          case 'error':
            if (action.message && action.index === undefined) {
              // 계획 생성 실패 등 최상위 오류
              appendLog(action.message, 'error')
              assistantEl.textContent = `자비스 : ${action.message}`
              setMiniModeStatus(action.message)
            } else {
              appendLog(`오류: ${action.error || `액션 #${action.index} 실패`}`, 'error')
            }
            break

          case 'finish': {
            const msg = action.aborted
              ? `작업 중단됨 — ${action.reason}`
              : `작업 완료 (성공 ${action.success}/${action.total})`
            appendLog(msg, action.aborted ? 'error' : 'success')
            assistantEl.textContent = `자비스 : ${msg}`
            setMiniModeStatus(msg)
            break
          }
        }
      }
    }

    assistantEl.classList.remove('streaming')

  } catch (e) {
    appendLog(`PC 제어 오류: ${e.message}`, 'error')
    assistantEl.textContent = `[오류] ${e.message}`
    assistantEl.classList.remove('streaming')
    if (state === 'WORKING') enterIdle()
  } finally {
    isStreaming = false
  }
}

function appendMessage(role, text, streaming = false) {
  const el = document.createElement('div')
  el.className = `message ${role}`
  el.textContent = text
  if (streaming) el.classList.add('streaming')
  messages.appendChild(el)
  messages.scrollTop = messages.scrollHeight
  return el
}

// ══════════════════════════════════════════════════════════════════════════════
// 8. TTS (Text-to-Speech)
// ══════════════════════════════════════════════════════════════════════════════

function stripMarkdownForTts(text) {
  return text
    .replace(/```[\s\S]*?```/g, ' 코드 블록. ')   // 코드 블록 → 짧은 안내
    .replace(/`[^`]+`/g, '')                        // 인라인 코드 제거
    .replace(/[#*_~>]/g, '')                         // 마크다운 기호 제거
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 400)                                   // 최대 400자
}

async function speakText(text) {
  if (!ttsEnabled || !text) return
  const cleaned = stripMarkdownForTts(text)
  if (!cleaned) return

  // 이전 재생 중단
  if (currentAudio) { currentAudio.pause(); currentAudio = null }

  try {
    appendLog(`음성 출력 시작 (${cleaned.length}자)`, 'tts')
    const res = await fetch(`${AI_URL}/api/tts/speak`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ text: cleaned }),
    })
    if (!res.ok) {
      appendLog(`TTS 서버 오류: ${res.status}`, 'error')
      return
    }

    const blob = await res.blob()
    const url  = URL.createObjectURL(blob)
    currentAudio = new Audio(url)
    currentAudio.onended = () => { URL.revokeObjectURL(url); currentAudio = null }
    currentAudio.play().catch(e => appendLog(`오디오 재생 실패: ${e.message}`, 'warn'))
  } catch (e) {
    appendLog(`TTS 연결 실패: ${e.message}`, 'warn')
  }
}

function toggleTts() {
  ttsEnabled = !ttsEnabled
  $('ttsMuteBtn').textContent = ttsEnabled ? '🔊' : '🔇'
  // 음소거 시 현재 재생 중단
  if (!ttsEnabled && currentAudio) { currentAudio.pause(); currentAudio = null }
}

// ══════════════════════════════════════════════════════════════════════════════
// 8-1. 음성 입력 STT 파이프라인
//   마이크 녹음(MediaRecorder) → /api/speech/transcribe → 채팅창 타이핑 애니메이션 → 자동 전송
// ══════════════════════════════════════════════════════════════════════════════

async function startVoiceRecording() {
  if (mediaRecorder && mediaRecorder.state === 'recording') return

  let stream
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false })
  } catch (e) {
    appendLog(`마이크 접근 실패: ${e.message}`, 'error')
    enterIdle()
    return
  }

  _recordChunks   = []
  _voiceCancelled = false

  const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
    ? 'audio/webm;codecs=opus'
    : 'audio/webm'

  mediaRecorder = new MediaRecorder(stream, { mimeType })

  mediaRecorder.ondataavailable = e => {
    if (e.data.size > 0) _recordChunks.push(e.data)
  }

  mediaRecorder.onstop = () => {
    stream.getTracks().forEach(t => t.stop())
    if (_voiceCancelled) { mediaRecorder = null; return }
    const blob = new Blob(_recordChunks, { type: mimeType })
    transcribeAndSend(blob)
    mediaRecorder = null
  }

  mediaRecorder.start(200)
  appendLog('음성 녹음 시작 (최대 5초)...', 'info')

  setTimeout(() => {
    if (mediaRecorder && mediaRecorder.state === 'recording') {
      appendLog('녹음 완료 — 음성 변환 중...', 'info')
      mediaRecorder.stop()
    }
  }, RECORD_MS)
}

function stopVoiceRecording(cancel = false) {
  _voiceCancelled = cancel
  if (mediaRecorder && mediaRecorder.state === 'recording') {
    mediaRecorder.stop()
  }
}

async function transcribeAndSend(blob) {
  if (!blob || blob.size < 100) {
    appendLog('녹음 데이터 없음 — 음성 입력 취소', 'warn')
    if (state === 'LISTENING') enterIdle()
    return
  }

  try {
    const formData = new FormData()
    formData.append('file', blob, 'voice.webm')

    const res = await fetch(`${AI_URL}/api/speech/transcribe`, {
      method: 'POST',
      body:   formData,
    })

    if (!res.ok) throw new Error(`STT 서버 오류: ${res.status}`)

    const data = await res.json()
    const text = data.text?.trim()

    if (!text) {
      appendLog('음성 인식 결과 없음 (묵음 또는 인식 실패)', 'warn')
      if (state === 'LISTENING') enterIdle()
      return
    }

    appendLog(`음성 인식: "${text.slice(0, 60)}${text.length > 60 ? '…' : ''}"`, 'info')

    if (state !== 'CHATTING') enterChatting()
    await typeIntoChatInput(text)
    sendMessage()

  } catch (e) {
    appendLog(`음성 변환 실패: ${e.message}`, 'error')
    if (state === 'LISTENING') enterIdle()
  }
}

function typeIntoChatInput(text) {
  return new Promise(resolve => {
    chatInput.value = ''
    let i = 0
    const timer = setInterval(() => {
      chatInput.value += text[i++]
      if (i >= text.length) {
        clearInterval(timer)
        resolve()
      }
    }, 35)
  })
}

// ══════════════════════════════════════════════════════════════════════════════
// 9. 기능 제한 모드
// ══════════════════════════════════════════════════════════════════════════════

function applyPermissionMode(mode) {
  permissionMode = mode
  const isLimited = (mode === 'LIMITED')

  // HUD 배지
  const badge = $('hudPermBadge')
  badge.textContent = isLimited ? '◈ LIMITED' : 'READY'

  // 채팅 패널 배지
  const chatBadge = $('chatPermBadge')
  if (isLimited) {
    chatBadge.classList.remove('hidden')
    sphereScene.classList.add('limited')
  } else {
    chatBadge.classList.add('hidden')
    sphereScene.classList.remove('limited')
  }
}

async function preClassifyMessage(text) {
  try {
    const res = await fetch(`${AI_URL}/api/chat/classify`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ message: text }),
      signal:  AbortSignal.timeout(8000),   // 백엔드 행(hang) 시 8초 후 포기 — UI 멈춤 방지
    })
    if (res.ok) return await res.json()
  } catch {}
  return null
}

function showLimitedModeWarning() {
  const el = appendMessage('assistant', '')
  el.innerHTML =
    '<span style="color:rgba(255,180,50,.9)">◈ 기능 제한 모드</span><br>' +
    'PC 자동 제어는 풀 액세스 모드에서만 사용할 수 있습니다.<br>' +
    '<small style="opacity:.6">설정을 변경하려면 앱을 재시작하고 권한 화면에서 풀 액세스를 선택하세요.</small>'
  appendLog('OS 명령 차단됨 (기능 제한 모드)', 'warn')
}

// ══════════════════════════════════════════════════════════════════════════════
// 위험 작업 확인 다이얼로그
// ══════════════════════════════════════════════════════════════════════════════

function showDangerConfirmDialog(dangerEvent) {
  _pendingDangerPlan = {
    thought: dangerEvent.thought,
    actions: dangerEvent.actions || [],
  }

  $('dangerReason').textContent = `⚠ ${dangerEvent.risk_reason}`
  $('dangerThought').textContent = dangerEvent.thought

  $('dangerModal').classList.remove('hidden')

  // TTS 경고 음성
  speakText(`경고, 위험한 작업이 감지되었습니다. ${dangerEvent.risk_reason}. 실행을 허가하시겠습니까?`)
}

function hideDangerModal() {
  $('dangerModal').classList.add('hidden')
  _pendingDangerPlan = null
}

async function confirmDangerExecution() {
  if (!_pendingDangerPlan) { hideDangerModal(); return }

  const plan = _pendingDangerPlan
  hideDangerModal()
  appendLog('위험 작업 사용자 승인 — 실행 시작', 'warn')
  enterMiniMode()

  try {
    const res = await fetch(`${AI_URL}/api/os/execute`, {
      method:  'POST',
      headers: {
        'Content-Type':       'application/json',
        'X-Jarvis-Permission': permissionMode || 'FULL',
      },
      body: JSON.stringify({
        thought:       plan.thought,
        actions:       plan.actions,
        stop_on_error: false,
      }),
    })

    if (!res.ok) {
      appendLog(`실행 요청 실패: ${res.status}`, 'error')
      return
    }

    // NDJSON 스트림 소비
    const reader  = res.body.getReader()
    const decoder = new TextDecoder()
    let buf = ''

    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buf += decoder.decode(value, { stream: true })
      const lines = buf.split('\n')
      buf = lines.pop()
      for (const line of lines) {
        const trimmed = line.trim()
        if (!trimmed) continue
        try {
          const ev = JSON.parse(trimmed)
          if (ev.event === 'start') {
            appendLog(ev.log || `액션 #${ev.index} 시작`, 'os')
          } else if (ev.event === 'done') {
            appendLog(`완료 (${ev.duration_ms}ms)`, 'success')
          } else if (ev.event === 'error') {
            appendLog(`오류: ${ev.error}`, 'error')
          } else if (ev.event === 'finish') {
            const msg = ev.aborted
              ? `위험 작업 중단: ${ev.reason}`
              : `위험 작업 완료 — 성공 ${ev.success}/${ev.total}`
            appendLog(msg, ev.aborted ? 'error' : 'success')
            setMiniModeStatus(msg)
          }
        } catch { /* 무시 */ }
      }
    }
  } catch (e) {
    appendLog(`위험 작업 실행 오류: ${e.message}`, 'error')
  }
}

function cancelDangerExecution() {
  appendLog('위험 작업 사용자 취소', 'info')
  hideDangerModal()
  exitMiniMode()
  setTimeout(enterIdle, 500)
}

// ══════════════════════════════════════════════════════════════════════════════
// 10. 실시간 화면 스트리밍
// ══════════════════════════════════════════════════════════════════════════════

function startScreenStream() {
  if (screenEventSource) stopScreenStream()

  $('screenPreview').classList.remove('hidden')
  screenFrameCount = 0

  try {
    screenEventSource = new EventSource(`${AI_URL}/api/os/screen-stream?fps=3`)
  } catch (e) {
    appendLog(`화면 스트림 시작 실패: ${e.message}`, 'error')
    return
  }

  screenEventSource.onmessage = ({ data }) => {
    try {
      const parsed = JSON.parse(data)
      if (parsed.error) {
        appendLog(`화면 스트림 오류: ${parsed.error}`, 'warn')
        return
      }
      $('screenImg').src = `data:image/jpeg;base64,${parsed.frame}`
      screenFrameCount++
    } catch { /* 무시 */ }
  }

  screenEventSource.onerror = () => {
    appendLog('화면 스트림 연결 끊김', 'warn')
    stopScreenStream()
  }

  // FPS 카운터 갱신 (1초마다)
  screenFpsTimer = setInterval(() => {
    $('screenFps').textContent = `${screenFrameCount} fps`
    screenFrameCount = 0
  }, 1000)

  appendLog('실시간 화면 표출 시작 (3 FPS)', 'info')
}

function stopScreenStream() {
  if (!screenEventSource) return
  screenEventSource.close()
  screenEventSource = null
  clearInterval(screenFpsTimer)
  screenFpsTimer = null
  $('screenPreview').classList.add('hidden')
  const img = $('screenImg')
  if (img) img.src = ''
  appendLog('실시간 화면 표출 종료', 'info')
}

// ══════════════════════════════════════════════════════════════════════════════
// 11. 재석 감지 (Presence Detection)
// ══════════════════════════════════════════════════════════════════════════════

function startPresenceStream() {
  if (presenceEventSource) return

  try {
    presenceEventSource = new EventSource(`${AI_URL}/api/presence/stream`)
  } catch (e) {
    appendLog(`재석 감지 연결 실패: ${e.message}`, 'warn')
    return
  }

  presenceEventSource.onmessage = ({ data }) => {
    try {
      const ev = JSON.parse(data)
      handlePresenceEvent(ev)
    } catch { /* 무시 */ }
  }

  presenceEventSource.onerror = () => {
    appendLog('재석 감지 연결 끊김 — 30초 후 재연결', 'warn')
    presenceEventSource?.close()
    presenceEventSource = null
    setTimeout(() => {
      if (permissionMode !== 'LIMITED') startPresenceStream()
    }, 30_000)
  }

  appendLog('재석 감지 서비스 연결됨', 'info')
}

function stopPresenceStream() {
  if (!presenceEventSource) return
  presenceEventSource.close()
  presenceEventSource = null
}

function handlePresenceEvent(ev) {
  if (ev.event === 'ping' || ev.event === 'init') return

  if (ev.event === 'away') {
    appendLog('사용자 자리 이탈 감지 — 보안 모드 진입', 'warn')
    enterAway()
  } else if (ev.event === 'back') {
    appendLog('사용자 복귀 감지 — IDLE 복귀', 'success')
    exitAway()
  }
}

function enterAway() {
  sphereScene.classList.remove('hidden')
  sphereScene.className = 'sphere-scene away'
  sphereLabel.textContent = 'SECURED'
  engineLabel.textContent = 'AWAY MODE'
  chatPanel.classList.add('hidden')
  window.jarvis?.setWindowState('IDLE')
  window.jarvis?.setIgnoreMouse(false)
}

function exitAway(restoreData) {
  if (!sphereScene.classList.contains('away')) return
  sphereScene.classList.remove('away')
  sphereLabel.textContent = 'JARVIS'
  enterIdle()

  const message = restoreData?.message
    || `Welcome back, Sir${userName && userName.toUpperCase() !== 'SIR' ? ' ' + userName : ''}.`

  appendLog(`복귀 감지 — ${message.slice(0, 60)}`, 'success')
  speakText(message)

  // 컨텍스트 복원 카드 표시 (복원 정보가 있을 때)
  if (restoreData?.restored && (restoreData.running_apps?.length || restoreData.workspace_name)) {
    enterChatting()
    setTimeout(() => showContextRestoreCard(restoreData), 300)
  }
}

function showContextRestoreCard(data) {
  const el = document.createElement('div')
  el.className = 'message assistant context-card'

  const wsName   = escHtml(data.workspace_name || '')
  const wsKey    = data.workspace_key || ''
  const apps     = (data.running_apps || []).map(a => `<span class="ctx-app">${escHtml(a)}</span>`).join(' ')
  const topic    = data.last_topic ? `<div class="ctx-topic">마지막 작업: "${escHtml(data.last_topic.slice(0, 60))}"</div>` : ''
  const wsBtnHtml = wsKey
    ? `<button class="ctx-ws-btn" onclick="restoreWorkspace('${escHtml(wsKey)}')">▶ ${escHtml(wsName || wsKey)} 재전환</button>`
    : ''

  el.innerHTML = `
    <div class="ctx-header">
      <span class="ctx-badge">◈ CONTEXT RESTORE</span>
    </div>
    ${topic}
    ${apps ? `<div class="ctx-apps-row">${apps}</div>` : ''}
    ${wsBtnHtml ? `<div class="ctx-btn-row">${wsBtnHtml}</div>` : ''}
  `
  messages.appendChild(el)
  messages.scrollTop = messages.scrollHeight
}

async function restoreWorkspace(key) {
  appendLog(`워크스페이스 재전환: ${key}`, 'os')
  try {
    const res  = await fetch(`${AI_URL.replace('8000', '8000')}/api/workspace/switch`.replace(AI_URL, AI_URL), {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ name: key }),
    })
    const data = await res.json()
    updateWorkspaceBadge(key)
    speakText(data.tts_message || `${key} 워크스페이스로 복원했습니다.`)
    appendLog(`워크스페이스 복원 완료: ${key}`, 'success')
  } catch (e) {
    appendLog(`워크스페이스 복원 실패: ${e.message}`, 'error')
  }
}

// ══════════════════════════════════════════════════════════════════════════════
// 12. 시스템 자원 모니터링 (Resource Monitor)
// ══════════════════════════════════════════════════════════════════════════════

function startResourceStream() {
  if (resourceEventSource) return
  try {
    resourceEventSource = new EventSource(`${AI_URL}/api/resources/stream`)
  } catch (e) {
    appendLog(`자원 모니터 연결 실패: ${e.message}`, 'warn')
    return
  }

  resourceEventSource.onmessage = ({ data }) => {
    try {
      const ev = JSON.parse(data)
      handleResourceEvent(ev)
    } catch { /* 무시 */ }
  }

  resourceEventSource.onerror = () => {
    resourceEventSource?.close()
    resourceEventSource = null
    setTimeout(startResourceStream, 30_000)
  }

  appendLog('시스템 자원 모니터링 연결됨', 'info')
}

function handleResourceEvent(ev) {
  if (ev.event === 'ping') return

  const cpu = ev.cpu_percent
  const ram = ev.ram_percent
  if (cpu == null) return

  // 구체 옆 미니 HUD
  const hudEl = $('resourceHud')
  if (hudEl) {
    const cpuEl = $('resCpu')
    const ramEl = $('resRam')
    if (cpuEl) {
      cpuEl.textContent = `CPU ${cpu.toFixed(0)}%`
      cpuEl.className = `res-item ${cpu >= 80 ? 'res-high' : cpu >= 60 ? 'res-mid' : ''}`
    }
    if (ramEl) {
      ramEl.textContent = `RAM ${ram.toFixed(0)}%`
      ramEl.className = `res-item ${ram >= 85 ? 'res-high' : ram >= 70 ? 'res-mid' : ''}`
    }
  }

  // 오른쪽 시스템 모니터 패널 업데이트
  updateSysmonPanel(ev)

  // GPU 정보 (있는 경우)
  if (ev.gpu_percent != null) {
    appendLog(
      `시스템: CPU ${cpu.toFixed(0)}% · RAM ${ram.toFixed(0)}% · GPU ${ev.gpu_percent.toFixed(0)}%`,
      'info'
    )
  }

  // 고부하 이벤트 처리
  if (ev.event === 'high_load') {
    appendLog(`⚠ 고부하 감지 — CPU ${cpu.toFixed(0)}% / RAM ${ram.toFixed(0)}% — 자비스 쓰로틀 적용`, 'warn')
    sphereScene.classList.add('high-load')
    speakText('시스템 고부하가 감지되었습니다. 자비스 백그라운드 작업을 최소화합니다.')
  } else if (ev.event === 'normal') {
    appendLog(`✓ 부하 정상화 — CPU ${cpu.toFixed(0)}% / RAM ${ram.toFixed(0)}%`, 'success')
    sphereScene.classList.remove('high-load')
  }
}

// ══════════════════════════════════════════════════════════════════════════════
// 13. 백그라운드 태스크 (Background Tasks)
// ══════════════════════════════════════════════════════════════════════════════

function startTaskStream() {
  if (taskEventSource) return

  try {
    taskEventSource = new EventSource(`${AI_URL}/api/tasks/stream`)
  } catch (e) {
    appendLog(`태스크 스트림 연결 실패: ${e.message}`, 'warn')
    return
  }

  taskEventSource.onmessage = ({ data }) => {
    try {
      const ev = JSON.parse(data)
      handleTaskEvent(ev)
    } catch { /* 무시 */ }
  }

  taskEventSource.onerror = () => {
    taskEventSource?.close()
    taskEventSource = null
    setTimeout(startTaskStream, 30_000)
  }

  appendLog('백그라운드 태스크 서비스 연결됨', 'info')
}

function handleTaskEvent(ev) {
  if (ev.event === 'ping' || ev.event === 'connected') return

  const id   = ev.task_id || ''
  const desc = ev.description || activeTasks[id] || id

  if (ev.event === 'submitted') {
    activeTasks[id] = ev.description || id
    appendLog(`[태스크 등록] ${desc} (${id})`, 'os')
    updateTaskBadge()

  } else if (ev.event === 'progress') {
    appendLog(`[태스크 진행] ${ev.message || desc}`, 'info')

  } else if (ev.event === 'completed') {
    delete activeTasks[id]
    updateTaskBadge()
    appendLog(`[태스크 완료] ${desc}`, 'success')

    if (state === 'IDLE' || state === 'LISTENING') enterChatting()

    // debug_error 태스크는 전용 카드로 표시
    if (ev.task_type === 'debug_error') {
      setTimeout(() => showDebugResult(ev), 200)
      speakText(`에러 분석 완료. ${ev.explanation ? ev.explanation.slice(0, 80) : '수정 코드를 확인해 주세요.'}`)
    } else {
      const notifyMsg = `백그라운드 작업 '${desc}' 완료.\n\n${ev.result || ''}`
      setTimeout(() => {
        const el = appendMessage('assistant', notifyMsg)
        el.classList.add('task-result')
        messages.scrollTop = messages.scrollHeight
      }, 200)
      speakText(`백그라운드 작업 ${desc}이 완료되었습니다.`)
    }

  } else if (ev.event === 'failed') {
    delete activeTasks[id]
    updateTaskBadge()
    appendLog(`[태스크 실패] ${desc}: ${ev.error}`, 'error')
    speakText(`백그라운드 작업 실행 중 오류가 발생했습니다.`)

  } else if (ev.event === 'cancelled') {
    delete activeTasks[id]
    updateTaskBadge()
    appendLog(`[태스크 취소] ${desc}`, 'warn')
  }
}

function updateTaskBadge() {
  const count = Object.keys(activeTasks).length
  const badge = $('taskBadge')
  if (!badge) return
  if (count > 0) {
    badge.textContent = `◈ ${count}`
    badge.classList.remove('hidden')
  } else {
    badge.classList.add('hidden')
  }
}

function showDebugResult(ev) {
  const el = document.createElement('div')
  el.className = 'message assistant debug-card'

  const fixCode    = escHtml(ev.fix_code    || '')
  const explanation = escHtml(ev.explanation || '분석 결과가 없습니다.')
  const errorType  = escHtml(ev.error_type  || 'Error')
  const refs       = (ev.references || []).slice(0, 2)

  let refHtml = ''
  for (const r of refs) {
    if (r.title) {
      refHtml += `<a class="debug-ref" href="${escHtml(r.url)}" target="_blank">⬡ ${escHtml(r.title.slice(0, 60))}</a>`
    }
  }

  el.innerHTML = `
    <div class="debug-header">
      <span class="debug-badge">⚠ AUTO DEBUG</span>
      <span class="debug-type">${errorType}</span>
    </div>
    <div class="debug-explanation">${explanation}</div>
    ${fixCode ? `
    <div class="debug-code-wrap">
      <div class="debug-code-header">
        <span>수정 코드</span>
        <button class="debug-apply-btn" onclick="applyDebugFix(${JSON.stringify(ev.fix_code || '')})">▶ 적용</button>
        <button class="debug-copy-btn" onclick="copyDebugFix(${JSON.stringify(ev.fix_code || '')})">⎘ 복사</button>
      </div>
      <pre class="debug-code">${fixCode}</pre>
    </div>` : ''}
    ${refHtml ? `<div class="debug-refs">${refHtml}</div>` : ''}
  `

  messages.appendChild(el)
  messages.scrollTop = messages.scrollHeight
}

async function applyDebugFix(code) {
  if (!code) return
  appendLog('수정 코드 적용 시도 (pyautogui 타이핑)', 'os')
  try {
    await fetch(`${AI_URL}/api/os/execute`, {
      method:  'POST',
      headers: {
        'Content-Type':        'application/json',
        'X-Jarvis-Permission': permissionMode || 'FULL',
      },
      body: JSON.stringify({
        thought: '자동 디버그 수정 코드 적용',
        actions: [{ type: 'write', param: code }],
        stop_on_error: false,
      }),
    })
    appendLog('수정 코드 타이핑 완료', 'success')
    speakText('수정 코드를 적용했습니다.')
  } catch (e) {
    appendLog(`수정 코드 적용 실패: ${e.message}`, 'error')
  }
}

function copyDebugFix(code) {
  navigator.clipboard?.writeText(code).then(() => {
    appendLog('수정 코드 클립보드 복사 완료', 'info')
  })
}

// ══════════════════════════════════════════════════════════════════════════════
// 14. 능동적 제안 (Proactive Suggestions)
// ══════════════════════════════════════════════════════════════════════════════

function startProactiveStream() {
  if (proactiveEventSource) return

  try {
    proactiveEventSource = new EventSource(`${AI_URL}/api/proactive/stream`)
  } catch (e) {
    appendLog(`능동적 제안 연결 실패: ${e.message}`, 'warn')
    return
  }

  proactiveEventSource.onmessage = ({ data }) => {
    try {
      const ev = JSON.parse(data)
      if (ev.event === 'suggestion') handleProactiveSuggestion(ev)
    } catch { /* 무시 */ }
  }

  proactiveEventSource.onerror = () => {
    proactiveEventSource?.close()
    proactiveEventSource = null
    setTimeout(() => {
      if (permissionMode !== 'LIMITED') startProactiveStream()
    }, 60_000)
  }

  appendLog('능동적 제안 서비스 연결됨', 'info')
}

function handleProactiveSuggestion(ev) {
  // AWAY 상태이거나 이미 채팅 중이면 제안 무시
  if (sphereScene.classList.contains('away')) return
  if (state === 'WORKING') return

  const text = ev.text
  if (!text) return

  const categoryLabel = {
    morning:    '아침 인사',
    break:      '휴식 권유',
    evening:    '저녁 마무리',
    idle_check: '자비스 알림',
  }[ev.category] || '자비스 알림'

  appendLog(`[${categoryLabel}] ${text.slice(0, 60)}...`, 'info')

  // IDLE 상태면 채팅 패널을 열고 제안 표시
  if (state === 'IDLE' || state === 'LISTENING') {
    enterChatting()
  }

  // 약간의 딜레이 후 메시지 표시 (패널 전환 애니메이션 대기)
  setTimeout(() => {
    appendMessage('assistant', text)
    messages.scrollTop = messages.scrollHeight
    speakText(text)
  }, 300)
}

// ══════════════════════════════════════════════════════════════════════════════
// 15. 스마트 스케줄러 / 리마인더
// ══════════════════════════════════════════════════════════════════════════════

function startSchedulerStream() {
  if (schedulerEventSource) return
  try {
    schedulerEventSource = new EventSource(`${AI_URL}/api/scheduler/stream`)
  } catch (e) {
    appendLog(`스케줄러 연결 실패: ${e.message}`, 'warn')
    return
  }

  schedulerEventSource.onmessage = ({ data }) => {
    try {
      const ev = JSON.parse(data)
      if (ev.event === 'reminder') handleReminderFired(ev)
    } catch { /* 무시 */ }
  }

  schedulerEventSource.onerror = () => {
    schedulerEventSource?.close()
    schedulerEventSource = null
    setTimeout(() => startSchedulerStream(), 30_000)
  }

  appendLog('스케줄러 서비스 연결됨', 'info')
}

function handleReminderFired(ev) {
  const title = ev.title || '리마인더'
  const desc  = ev.description || ''

  appendLog(`[리마인더] ${title}`, 'info')

  if (sphereScene.classList.contains('away')) return

  if (state === 'IDLE' || state === 'LISTENING') enterChatting()

  const ttsMsg = desc
    ? `Sir, ${title}. ${desc}`
    : `Sir, ${title} 시간입니다.`

  setTimeout(() => {
    showReminderCard(ev)
    messages.scrollTop = messages.scrollHeight
    speakText(ttsMsg)
  }, 300)
}

function showReminderCard(ev) {
  const el = document.createElement('div')
  el.className = 'message assistant reminder-card'

  const dueLabel = ev.due_at
    ? ev.due_at.replace('T', ' ').slice(0, 16)
    : ''
  const repeatLabel = {
    daily:  '매일 반복',
    weekly: '매주 반복',
    none:   '',
  }[ev.repeat] || ''

  el.innerHTML = `
    <div class="rem-header">
      <span class="rem-badge">◈ REMINDER</span>
      ${repeatLabel ? `<span class="rem-repeat">${repeatLabel}</span>` : ''}
    </div>
    <div class="rem-title">${escapeHtml(ev.title)}</div>
    ${ev.description ? `<div class="rem-desc">${escapeHtml(ev.description)}</div>` : ''}
    ${dueLabel ? `<div class="rem-due">${dueLabel}</div>` : ''}
    <div class="rem-btn-row">
      <button class="rem-dismiss-btn" onclick="this.closest('.reminder-card').remove()">확인</button>
    </div>
  `
  messages.appendChild(el)
}

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

// ── 리마인더 목록 패널 ─────────────────────────────────────────────────────────

async function showReminderList() {
  try {
    const res   = await fetch(`${AI_URL}/api/scheduler/reminders`)
    const items = await res.json()
    if (!items.length) {
      appendMessage('assistant', '등록된 리마인더가 없습니다, Sir.')
      return
    }
    const el = document.createElement('div')
    el.className = 'message assistant reminder-list-card'
    el.innerHTML = `
      <div class="rem-header">
        <span class="rem-badge">◈ REMINDERS</span>
        <span class="rem-count">${items.length}개</span>
      </div>
      <div class="rem-list">
        ${items.map(r => `
          <div class="rem-list-item" data-id="${r.id}">
            <span class="rem-list-title">${escapeHtml(r.title)}</span>
            <span class="rem-list-due">${r.due_at.replace('T', ' ').slice(0, 16)}</span>
            <button class="rem-del-btn" onclick="deleteReminder('${r.id}', this)">✕</button>
          </div>
        `).join('')}
      </div>
    `
    messages.appendChild(el)
    messages.scrollTop = messages.scrollHeight
  } catch (e) {
    appendMessage('assistant', `리마인더 목록 조회 실패: ${e.message}`)
  }
}

async function deleteReminder(id, btn) {
  try {
    await fetch(`${AI_URL}/api/scheduler/reminders/${id}`, { method: 'DELETE' })
    btn.closest('.rem-list-item').remove()
  } catch { /* 무시 */ }
}

// ══════════════════════════════════════════════════════════════════════════════
// 16. 텔레그램 원격 제어 알림
// ══════════════════════════════════════════════════════════════════════════════

function startTelegramStream() {
  if (telegramEventSource) return
  try {
    telegramEventSource = new EventSource(`${AI_URL}/api/telegram/stream`)
  } catch (e) {
    appendLog(`텔레그램 연결 실패: ${e.message}`, 'warn')
    return
  }

  telegramEventSource.onmessage = ({ data }) => {
    try {
      const ev = JSON.parse(data)
      if (ev.event === 'telegram_command') handleTelegramCommand(ev)
    } catch { /* 무시 */ }
  }

  telegramEventSource.onerror = () => {
    telegramEventSource?.close()
    telegramEventSource = null
    setTimeout(() => startTelegramStream(), 30_000)
  }

  appendLog('텔레그램 봇 알림 연결됨', 'info')
}

function handleTelegramCommand(ev) {
  const cmd   = ev.command || ''
  const reply = ev.reply   || ''

  appendLog(`[Telegram] ${cmd.slice(0, 50)}`, 'info')

  // AWAY 상태여도 원격 명령은 표시
  if (state === 'IDLE' || state === 'LISTENING') enterChatting()

  setTimeout(() => {
    showTelegramCard(cmd, reply)
    messages.scrollTop = messages.scrollHeight
    speakText(`Sir, 텔레그램 원격 명령이 실행되었습니다. ${reply.slice(0, 60)}`)
  }, 200)
}

function showTelegramCard(cmd, reply) {
  const el = document.createElement('div')
  el.className = 'message assistant telegram-card'
  el.innerHTML = `
    <div class="tg-header">
      <span class="tg-badge">◈ TELEGRAM</span>
      <span class="tg-label">원격 명령</span>
    </div>
    <div class="tg-cmd">&gt; ${escapeHtml(cmd)}</div>
    <div class="tg-reply">${escapeHtml(reply.slice(0, 300))}</div>
  `
  messages.appendChild(el)
}

// ══════════════════════════════════════════════════════════════════════════════
// 17. UI 유틸
// ══════════════════════════════════════════════════════════════════════════════

const WS_LABELS = {
  dev:     '◈ DEV',
  docs:    '◈ DOCS',
  focus:   '◈ FOCUS',
  meeting: '◈ MEET',
}

function updateWorkspaceBadge(key) {
  const badge = $('hudAgent')
  if (!badge) return
  const label = WS_LABELS[key] || `◈ ${key.toUpperCase()}`
  badge.textContent = label
  badge.classList.add('ws-active')
  setTimeout(() => badge.classList.remove('ws-active'), 2000)
  appendLog(`워크스페이스 모드: ${label}`, 'info')
}

function updateClock() {
  const now = new Date()
  hudTime.textContent = now.toTimeString().slice(0, 8)
}

function updateUserLabel(name) {
  const label = name && name.toUpperCase() !== 'SIR'
    ? `SIR · ${name.toUpperCase()}`
    : 'SIR'
  $('hudUserLabel').textContent = label
}

function updateEngineInfo(key, agent) {
  if (key) {
    engineKey = key
    const label = key.replace('_', ' · ').replace('OLLAMA', '⚙').replace('CLAUDE', '◆').replace('GPT4O', '◈').replace('GEMINI', '✦')
    engineLabel.textContent = label
    $('chatEngineBadge').textContent = label
  }
  if (agent) {
    agentKey = agent
    const agentLabel = agent.replace(/_/g, ' ').toUpperCase()
    hudAgent.textContent = agentLabel
    $('chatAgentBadge').textContent = agentLabel
  }
}

function updateAgentBadge(key) {
  agentKey = key
  const label = key.replace(/_/g, ' ').toUpperCase()
  hudAgent.textContent = label
  $('chatAgentBadge').textContent = label
}

function flashSphere() {
  sphereCore.style.transition = 'box-shadow 0.15s'
  sphereCore.style.boxShadow  = '0 0 60px #fff, 0 0 120px var(--cyan)'
  setTimeout(() => { sphereCore.style.boxShadow = '' }, 250)
}

// ══════════════════════════════════════════════════════════════════════════════
// 15-1. AI 엔진 설정 모달 (신호등 초록 버튼)
// ══════════════════════════════════════════════════════════════════════════════

// 모달에 노출할 AI 모델 목록 — 백엔드(.env 기반 ENGINE_REGISTRY)에서 동적으로 로드
// API 키가 필요 없는 프로바이더(로컬 모델 / 구독 기반 Claude Code)
const SETTINGS_NO_KEY_PROVIDERS = ['ollama', 'claude_code']

const SETTINGS_ACTIVE_KEY = 'jarvis_settings_active_engine'
const apiKeyStorageKey = provider => `jarvis_apikey_${provider}`

// 모달이 열려 있는 동안의 미저장(pending) 선택 상태 — 저장 버튼을 눌러야 반영됨
let _pendingActiveEngine = null
// 모달을 연 시점의 활성 엔진(=A 모델) — 저장 시 변경 여부 비교용
let _initialActiveEngine = null
// 백엔드에서 불러온 전체 엔진 목록 캐시
let _engineList = []
// 백엔드 연결 실패 시 표시할 오프라인 안내 여부
let _engineListOffline = false

// 백엔드(8000)에 연결되지 않을 때 모달이 비지 않도록 보여줄 기본 목록
// JARVIS는 CLAUDE_CODE 엔진으로 고정되어 있으므로(ALLOWED_ENGINES), 다른
// 엔진은 목록에 포함하지 않는다.
const FALLBACK_ENGINES = [
  { key: 'CLAUDE_CODE',       name: 'Claude Code (구독)',     provider: 'claude_code', is_active: true  },
]

// JARVIS는 CLAUDE_CODE 엔진으로 고정되어 있다 (백엔드 ALLOWED_ENGINES).
// 과거 버전에서 localStorage에 다른 엔진이 저장되어 있을 수 있으므로,
// 시작 시 항상 CLAUDE_CODE로 정리해 더 이상 다른 엔진 전환을 시도하지 않는다.
const LOCKED_ENGINE_KEY = 'CLAUDE_CODE'

async function syncActiveEngineWithBackend() {
  localStorage.setItem(SETTINGS_ACTIVE_KEY, LOCKED_ENGINE_KEY)
}

// fetch에 타임아웃을 적용 — 백엔드가 응답 없이 멈춰 있을 때 무한 대기를 방지
async function fetchWithTimeout(url, options = {}, timeoutMs = 5000) {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  try {
    return await fetch(url, { ...options, signal: controller.signal })
  } catch (e) {
    if (e.name === 'AbortError') throw new Error('응답 시간 초과')
    throw e
  } finally {
    clearTimeout(timer)
  }
}

async function fetchEngineList() {
  try {
    const res = await fetchWithTimeout(`${AI_URL}/api/chat/engines`)
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    _engineList = await res.json()
    _engineListOffline = false
  } catch (e) {
    appendLog(`엔진 목록 조회 실패 (백엔드 연결 안됨): ${e.message}`, 'error')
    _engineList = FALLBACK_ENGINES
    _engineListOffline = true
  }
  return _engineList
}

// ══════════════════════════════════════════════════════════════════════════════
// 15-2. Claude Code (구독) 패널 — AI 엔진 연결 상태 / 모델 선택 / 오늘 사용량 / 고급
// ══════════════════════════════════════════════════════════════════════════════

const CC_INSTALL_URL = 'https://docs.claude.com/en/docs/claude-code/overview'
const CC_LOGIN_CMD   = 'claude'   // 안내: 터미널에서 실행 후 /login 진행

// /api/claude/status 결과 캐시
let _ccStatus = null
// /api/claude/usage 결과 캐시
let _ccUsage = null
// /api/claude/settings 원본(저장된 값) 캐시
let _ccSettingsLoaded = null
// 미저장(pending) 모델 선택 — '' (기본) | 'sonnet' | 'haiku'
let _ccPendingModel = null
// 미저장(pending) allow_api_key_billing
let _ccPendingBilling = null
// "고급" 섹션 펼침 여부
let _ccAdvancedOpen = false
// 새로고침 버튼 로딩 상태
let _ccStatusRefreshing = false

async function fetchCCStatus(force = false) {
  try {
    const res = await fetchWithTimeout(
      `${AI_URL}/api/claude/${force ? 'status/refresh' : 'status'}`,
      { method: force ? 'POST' : 'GET' },
    )
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    _ccStatus = await res.json()
  } catch (e) {
    _ccStatus = { installed: null, error: e.message }
  }
  return _ccStatus
}

async function fetchCCUsage() {
  try {
    const res = await fetchWithTimeout(`${AI_URL}/api/claude/usage`)
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    _ccUsage = await res.json()
  } catch (e) {
    _ccUsage = null
  }
  return _ccUsage
}

async function fetchCCSettings() {
  try {
    const res = await fetchWithTimeout(`${AI_URL}/api/claude/settings`)
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    _ccSettingsLoaded = await res.json()
  } catch (e) {
    _ccSettingsLoaded = null
  }
  return _ccSettingsLoaded
}

function renderCCPanel() {
  if (_ccPendingModel === null) {
    _ccPendingModel = (_ccSettingsLoaded && _ccSettingsLoaded.model) || ''
  }
  if (_ccPendingBilling === null) {
    _ccPendingBilling = !!(_ccSettingsLoaded && _ccSettingsLoaded.allow_api_key_billing)
  }

  const st = _ccStatus || {}

  // ── AI 엔진 연결 상태 카드 ──
  let statusBody
  if (!st || st.installed === null || st.installed === undefined) {
    statusBody = `<p class="cc-status-line">연결 상태 확인 중...</p>`
  } else if (!st.installed) {
    statusBody = `
      <p class="cc-status-line cc-status-warn">Claude Code CLI가 설치되어 있지 않습니다.</p>
      <a class="setup-btn setup-btn-secondary cc-link-btn" href="${CC_INSTALL_URL}" target="_blank" rel="noopener">설치 안내 페이지 열기</a>`
  } else if (st.logged_in === false) {
    statusBody = `
      <p class="cc-status-row"><span>CLI 버전</span><span>v${escHtml(st.version || '?')}</span></p>
      <p class="cc-status-line cc-status-warn">로그인이 필요합니다. 터미널에서 아래 명령을 실행해 로그인을 완료한 뒤 새로고침해 주세요.</p>
      <div class="cc-cmd-row">
        <code class="cc-cmd">${escHtml(CC_LOGIN_CMD)}</code>
        <button type="button" class="cc-copy-btn" id="ccCopyLoginCmd">복사</button>
      </div>`
  } else {
    const authLabel = st.auth_method === 'subscription_oauth' ? '구독 (OAuth)'
                     : st.auth_method === 'api_key'            ? 'API 키'
                     : '확인 불가'
    statusBody = `
      <p class="cc-status-row"><span>CLI 버전</span><span>v${escHtml(st.version || '?')}</span></p>
      <p class="cc-status-row"><span>로그인</span><span class="cc-ok">완료</span></p>
      <p class="cc-status-row"><span>인증 방식</span><span>${escHtml(authLabel)}</span></p>`
  }

  // ── 오늘 사용량 게이지 ──
  // 사용량 정보를 불러오지 못한 경우, 오류 문구 대신 카드 자체를 표시하지 않는다.
  let usageHtml = ''
  if (_ccUsage) {
    const limit = _ccUsage.daily_limit || 0
    const pct = limit ? Math.min(100, Math.round((_ccUsage.calls / limit) * 100)) : 0
    usageHtml = `
      <div class="cc-usage-row">
        <span>호출 ${_ccUsage.calls} / ${limit}회</span>
        <span>≈ $${(_ccUsage.cost_usd || 0).toFixed(3)}</span>
      </div>
      <div class="cc-usage-bar"><div class="cc-usage-fill ${_ccUsage.over_warn ? 'warn' : ''}" style="width:${pct}%"></div></div>`
  }

  // ── 모델 선택 (기본 / Sonnet / Haiku) ──
  const modelOptions = [
    { value: '',       label: '기본 (플랜 권장)' },
    { value: 'sonnet', label: 'Sonnet' },
    { value: 'haiku',  label: 'Haiku' },
  ]
  const modelSelectHtml = `
    <select class="modal-input cc-model-select" id="ccModelSelect">
      ${modelOptions.map(o => `<option value="${o.value}" ${o.value === _ccPendingModel ? 'selected' : ''}>${o.label}</option>`).join('')}
    </select>`

  return `
    <div class="cc-panel">
      <div class="cc-status-card">
        <div class="cc-status-header">
          <span class="cc-status-title">AI 엔진 연결 상태</span>
          <button type="button" class="cc-refresh-btn" id="ccRefreshBtn" ${_ccStatusRefreshing ? 'disabled' : ''}>
            ${_ccStatusRefreshing ? '<span class="spinner"></span>' : '↻ 새로고침'}
          </button>
        </div>
        ${statusBody}
      </div>

      <div class="field-group cc-field-group">
        <label class="field-label">모델</label>
        ${modelSelectHtml}
      </div>

      ${usageHtml ? `
      <div class="cc-usage-card">
        <div class="cc-status-title">오늘 사용량</div>
        ${usageHtml}
      </div>` : ''}

      <div class="cc-advanced">
        <button type="button" class="cc-advanced-toggle" id="ccAdvancedToggle">
          고급 <span class="cc-advanced-caret">${_ccAdvancedOpen ? '▲' : '▼'}</span>
        </button>
        <div class="cc-advanced-body ${_ccAdvancedOpen ? 'open' : ''}">
          <div class="cc-advanced-inner">
            <div class="settings-model-row">
              <span class="settings-model-name cc-billing-label">API 키 종량 결제 허용</span>
              <label class="toggle-switch">
                <input type="checkbox" id="ccBillingToggle" ${_ccPendingBilling ? 'checked' : ''} />
                <span class="toggle-slider"></span>
              </label>
            </div>
            <p class="cc-billing-warning">⚠ 켜면 구독 대신 종량 요금이 청구될 수 있습니다.</p>
          </div>
        </div>
      </div>
    </div>`
}

function renderSettingsModal() {
  const list = $('settingsModelList')

  if (_pendingActiveEngine === null) {
    // 백엔드가 응답 중이면 서버의 is_active(실제 활성 엔진)를 최우선으로 신뢰한다.
    // localStorage는 백엔드가 오프라인일 때만 fallback으로 사용 — 그렇지 않으면
    // "로컬엔 B로 저장됨" vs "백엔드는 실제로 A 사용 중" 같은 불일치가 화면에 가려진다.
    let activeEngine = null
    if (!_engineListOffline) {
      const serverActive = _engineList.find(m => m.is_active)
      activeEngine = serverActive ? serverActive.key : null
    }
    if (!activeEngine) {
      activeEngine = localStorage.getItem(SETTINGS_ACTIVE_KEY) || engineKey
    }
    if (!_engineList.some(m => m.key === activeEngine)) {
      const fallback = _engineList.find(m => m.is_active) || _engineList[0]
      activeEngine = fallback ? fallback.key : null
    }
    _pendingActiveEngine = activeEngine
    _initialActiveEngine = activeEngine
  }

  // JARVIS는 CLAUDE_CODE 엔진으로 고정되어 있으므로, 백엔드 연결 실패 시에도
  // 폴백 안내 배너 없이 Claude Code 단일 항목을 그대로 표시한다.
  list.innerHTML = _engineList.map(model => {
    const isActive  = model.key === _pendingActiveEngine
    const isLocked  = model.key !== LOCKED_ENGINE_KEY
    const needsKey  = !SETTINGS_NO_KEY_PROVIDERS.includes(model.provider)
    const savedKey  = localStorage.getItem(apiKeyStorageKey(model.provider)) || ''
    return `
      <div class="settings-model-item ${isActive ? 'active' : ''} ${isLocked ? 'locked' : ''}" data-engine="${model.key}">
        <div class="settings-model-row">
          <span class="settings-model-name">${escHtml(model.name)}</span>
          ${isLocked
            ? `<span class="settings-model-locked-note" title="JARVIS는 Claude Code(구독) 엔진으로 고정되어 있습니다">사용 안 함</span>`
            : `<label class="toggle-switch">
            <input type="checkbox" class="settings-model-toggle" data-engine="${model.key}" ${isActive ? 'checked' : ''} />
            <span class="toggle-slider"></span>
          </label>`}
        </div>
        ${model.provider === 'claude_code' ? `
        <div class="settings-api-key-wrap ${isActive ? 'open' : ''}">
          <div class="settings-api-key-inner">
            ${renderCCPanel()}
          </div>
        </div>` : needsKey ? `
        <div class="settings-api-key-wrap ${isActive ? 'open' : ''}">
          <div class="settings-api-key-inner">
            <input type="password" class="modal-input settings-api-key-input"
                   data-provider="${model.provider}"
                   placeholder="${model.provider.toUpperCase()} API 키 입력"
                   value="${escHtml(savedKey)}" />
          </div>
        </div>` : `
        <div class="settings-api-key-wrap ${isActive ? 'open' : ''}">
          <div class="settings-api-key-inner">
            <p class="settings-no-key-note">로컬 모델 — API 키가 필요 없습니다</p>
          </div>
        </div>`}
      </div>`
  }).join('')

  // 토글 스위치 — 라디오 방식(한 번에 하나만 ON, 네트워크 호출 없이 화면 상태만 갱신)
  list.querySelectorAll('.settings-model-toggle').forEach(toggle => {
    toggle.addEventListener('change', () => {
      if (!toggle.checked) {
        // 자기 자신을 끄는 건 허용하지 않음 (항상 하나는 활성 상태)
        toggle.checked = true
        return
      }
      _pendingActiveEngine = toggle.dataset.engine
      renderSettingsModal()
    })
  })

  // ── Claude Code 패널 이벤트 바인딩 ──
  $('ccRefreshBtn')?.addEventListener('click', async () => {
    _ccStatusRefreshing = true
    renderSettingsModal()
    await Promise.all([fetchCCStatus(true), fetchCCUsage()])
    _ccStatusRefreshing = false
    renderSettingsModal()
  })

  $('ccCopyLoginCmd')?.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(CC_LOGIN_CMD)
      appendLog(`명령어를 클립보드에 복사했습니다: ${CC_LOGIN_CMD}`, 'success')
    } catch (e) {
      appendLog(`클립보드 복사 실패: ${e.message}`, 'error')
    }
  })

  $('ccModelSelect')?.addEventListener('change', (e) => {
    _ccPendingModel = e.target.value
  })

  $('ccAdvancedToggle')?.addEventListener('click', () => {
    _ccAdvancedOpen = !_ccAdvancedOpen
    renderSettingsModal()
  })

  $('ccBillingToggle')?.addEventListener('change', (e) => {
    _ccPendingBilling = e.target.checked
  })
}

async function openSettingsModal() {
  _pendingActiveEngine = null   // 열 때마다 저장된 상태 기준으로 다시 계산
  _initialActiveEngine = null
  _ccPendingModel = null
  _ccPendingBilling = null
  _ccAdvancedOpen = false
  // 모달부터 먼저 열어 백엔드 응답 지연/실패와 무관하게 즉시 반응하도록 함
  $('settingsModal').classList.remove('hidden')
  try {
    await Promise.all([fetchEngineList(), fetchCCStatus(), fetchCCUsage(), fetchCCSettings()])
    renderSettingsModal()
  } catch (e) {
    appendLog(`설정 모달 로드 실패: ${e.message}`, 'error')
  }
}

function closeSettingsModal() {
  $('settingsModal').classList.add('hidden')
}

// ── API 키를 백엔드(.env)에 반영하고, 성공한 경우에만 로컬 스토리지에 기록 ───────
// 백엔드 반영이 실패/타임아웃되면 localStorage를 건드리지 않아
// "화면엔 저장된 것처럼 보이는데 실제론 반영 안 됨" 상태를 방지한다.
async function persistApiKey(provider, value) {
  try {
    const res = await fetchWithTimeout(`${AI_URL}/api/chat/settings/api-key`, {
      method:  'PUT',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ provider, api_key: value }),
    })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    localStorage.setItem(apiKeyStorageKey(provider), value)
    return true
  } catch (e) {
    appendLog(`${provider.toUpperCase()} API 키 ${value ? '저장' : '초기화'} 실패: ${e.message}`, 'error')
    return false
  }
}

// ── 저장 버튼: B 모델 설정/키 반영 + A 모델 비활성화·키 초기화를 동시에 수행 ──────
async function saveSettings() {
  const btn      = $('settingsSaveBtn')
  const btnText  = $('settingsSaveBtnText')
  const spinner  = $('settingsSaveBtnSpinner')

  btn.disabled = true
  btnText.classList.add('hidden')
  spinner.classList.remove('hidden')

  try {
    const newEngine = _pendingActiveEngine
    const prevEngine = _initialActiveEngine
    const newModel  = _engineList.find(m => m.key === newEngine)
    const prevModel = _engineList.find(m => m.key === prevEngine)

    if (!newModel) {
      appendLog('선택된 엔진 정보를 찾을 수 없습니다.', 'error')
      return
    }

    // ① 새로 선택한 B 모델의 API 키를 시스템(.env)에 저장
    let keyOk = true
    if (!SETTINGS_NO_KEY_PROVIDERS.includes(newModel.provider)) {
      const input = findActiveApiKeyInput(newModel.provider)
      const value = (input?.value || '').trim()
      keyOk = await persistApiKey(newModel.provider, value)
    }

    // ② 기존 A 모델은 '미사용' 처리 — provider가 바뀌었다면 기존 API 키를 초기화
    //    (이 단계 실패는 치명적이지 않으므로 전체 저장을 막지는 않음)
    if (
      prevModel &&
      prevModel.key !== newModel.key &&
      prevModel.provider !== newModel.provider &&
      !SETTINGS_NO_KEY_PROVIDERS.includes(prevModel.provider)
    ) {
      await persistApiKey(prevModel.provider, '')
    }

    // ③ 활성 엔진 전환 — B 모델의 설정 정보를 시스템에 반영
    let engineOk = true
    try {
      const res = await fetchWithTimeout(`${AI_URL}/api/chat/engine/${newEngine}`, { method: 'PUT' })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      updateEngineInfo(newEngine, null)
    } catch (e) {
      engineOk = false
      appendLog(`엔진 전환 실패: ${e.message}`, 'error')
    }

    // 백엔드에 실제로 반영된 항목만 localStorage에 기록 — 불일치 방지
    if (engineOk) {
      localStorage.setItem(SETTINGS_ACTIVE_KEY, newEngine)
    }

    // ④ Claude Code(구독) 통합 모듈 설정 반영 — 모델 선택 / 종량 결제 허용 여부
    //    (claude_settings.json은 채팅 엔진 활성 여부와 무관하게 항상 유효)
    if (_ccPendingModel !== null || _ccPendingBilling !== null) {
      try {
        const patch = {}
        if (_ccPendingModel !== null)   patch.model = _ccPendingModel || null
        if (_ccPendingBilling !== null) patch.allow_api_key_billing = _ccPendingBilling
        const res = await fetchWithTimeout(`${AI_URL}/api/claude/settings`, {
          method:  'PUT',
          headers: { 'Content-Type': 'application/json' },
          body:    JSON.stringify(patch),
        })
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        _ccSettingsLoaded = await res.json()
      } catch (e) {
        engineOk = false
        appendLog(`Claude Code 설정 저장 실패: ${e.message}`, 'error')
      }
    }

    if (!keyOk || !engineOk) {
      appendLog('일부 설정이 저장되지 않았습니다. 백엔드 연결을 확인한 뒤 다시 시도해 주세요.', 'error')
      // 모달은 열어둔 채로 사용자가 재시도할 수 있게 한다
      return
    }

    appendLog('AI 엔진 설정이 저장되었습니다.', 'success')
    closeSettingsModal()
  } finally {
    btn.disabled = false
    btnText.classList.remove('hidden')
    spinner.classList.add('hidden')
  }
}

// 활성(open) 상태인 모델 항목의 API 키 입력란을 찾아 반환
function findActiveApiKeyInput(provider) {
  return document.querySelector(
    `.settings-model-item.active .settings-api-key-input[data-provider="${provider}"]`
  )
}

// ══════════════════════════════════════════════════════════════════════════════
// 16. 이벤트 바인딩
// ══════════════════════════════════════════════════════════════════════════════

// 창 제어 버튼
$('btnClose').addEventListener('click',    () => window.jarvis?.closeWindow())
$('btnMinimize').addEventListener('click', () => window.jarvis?.minimizeWindow())

// 미니 모드 클릭 → 원래 크기/위치로 복원
$('miniMode').addEventListener('click', () => enterIdle())

// 신호등 초록 버튼 → AI 엔진 설정 모달
$('btnPin').addEventListener('click', openSettingsModal)
$('settingsCloseBtn').addEventListener('click', closeSettingsModal)
$('settingsSaveBtn').addEventListener('click', saveSettings)
$('settingsModal').addEventListener('click', (e) => {
  if (e.target.id === 'settingsModal') closeSettingsModal()
})

// 구체 클릭 → 채팅 열기
sphereCore.addEventListener('click', () => {
  if (state === 'IDLE' || state === 'LISTENING') enterChatting()
})

// 권한 동의 버튼
$('permSubmitBtn').addEventListener('click', submitPermission)

// 권한 라디오 선택 시 라벨 강조
document.querySelectorAll('input[name="permMode"]').forEach(radio => {
  radio.addEventListener('change', () => {
    document.querySelectorAll('.perm-option').forEach(opt => opt.classList.remove('selected'))
    radio.closest('.perm-option')?.classList.add('selected')
  })
})

// 로그 창 접기/펼치기
$('logHeader').addEventListener('click', toggleLogPanel)

// TTS 음소거 토글
$('ttsMuteBtn').addEventListener('click', toggleTts)

// 채팅 닫기
$('chatCloseBtn').addEventListener('click', exitChatting)

// 채팅 전송
$('sendBtn').addEventListener('click', sendMessage)
chatInput.addEventListener('keydown', onChatInputKey)
chatInput.addEventListener('input', () => {
  const val = chatInput.value
  if (!val.startsWith('/')) $('slashMenu').classList.add('hidden')
})

// ngrok 설정 완료 버튼
$('setupBtn').addEventListener('click', submitNgrokConfig);

// ngrok 설정 나중에 하기 버튼
$('skipNgrokBtn').addEventListener('click', skipNgrokSetup);

// Enter 키로 설정 완료
[$('inputAuthToken'), $('inputDomain')].forEach(el => {
  el.addEventListener('keydown', e => { if (e.key === 'Enter') submitNgrokConfig() })
})

// 위험 작업 확인 다이얼로그 버튼
$('dangerConfirmBtn').addEventListener('click', confirmDangerExecution)
$('dangerCancelBtn').addEventListener('click',  cancelDangerExecution)

// ══════════════════════════════════════════════════════════════════════════════
// 18. 날씨 패널
// ══════════════════════════════════════════════════════════════════════════════

const WEATHER_CITY = 'Seoul'
let _weatherCode = 0

async function initWeather() {
  try {
    const res  = await fetch(`${AI_URL}/api/weather/current?city=${encodeURIComponent(WEATHER_CITY)}`)
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data = await res.json()
    renderWeather(data)
    _weatherCode = data.wmo_code || 0
    updateAmbient(_weatherCode)
  } catch (e) {
    $('weatherBody').innerHTML = `<div class="weather-loading">날씨 연결 재시도 중...</div>`
    // FastAPI 미실행 등 오류 시 60초 후 재시도
    setTimeout(initWeather, 60_000)
  }
}

function renderWeather(d) {
  const days = ['일', '월', '화', '수', '목', '금', '토']
  const fcHtml = (d.forecast || []).map(f => {
    const dt   = new Date(f.date)
    const label = dt.getDay() === new Date().getDay() ? '오늘' : days[dt.getDay()]
    return `<div class="weather-fc-item">
      <div class="weather-fc-date">${label}</div>
      <span class="weather-fc-icon">${f.icon}</span>
      <div class="weather-fc-temp">${f.max}°/${f.min}°</div>
    </div>`
  }).join('')

  $('weatherCity').textContent = d.city
  $('weatherBody').innerHTML = `
    <div class="weather-main">
      <span class="weather-icon">${d.icon}</span>
      <span class="weather-temp">${d.temp}°</span>
    </div>
    <div class="weather-row"><span class="weather-label">날씨</span><span class="weather-val">${escapeHtml(d.condition)}</span></div>
    <div class="weather-row"><span class="weather-label">체감</span><span class="weather-val">${d.feels_like}°</span></div>
    <div class="weather-row"><span class="weather-label">습도</span><span class="weather-val">${d.humidity}%</span></div>
    <div class="weather-row"><span class="weather-label">바람</span><span class="weather-val">${d.wind_speed} m/s</span></div>
    <div class="weather-forecast">${fcHtml}</div>
  `
}

// ══════════════════════════════════════════════════════════════════════════════
// 19. 달력 패널
// ══════════════════════════════════════════════════════════════════════════════

function initCalendar() {
  // calendarPanel이 HTML에 없으면 조용히 종료
  if (!$('calGrid')) return

  const now   = new Date()
  const year  = now.getFullYear()
  const month = now.getMonth()
  const calMonth = $('calendarMonth')
  if (calMonth) calMonth.textContent = `${year}.${String(month + 1).padStart(2, '0')}`

  const first  = new Date(year, month, 1)
  const last   = new Date(year, month + 1, 0)
  const offset = first.getDay()

  const DAYS = ['일', '월', '화', '수', '목', '금', '토']
  let html = DAYS.map(d => `<div class="cal-day-hdr">${d}</div>`).join('')

  for (let i = 0; i < offset; i++) {
    const prevDay = new Date(year, month, -offset + i + 1).getDate()
    html += `<div class="cal-day other-month">${prevDay}</div>`
  }

  // 리마인더 날짜 수집 (비동기)
  fetch(`${AI_URL}/api/scheduler/reminders`)
    .then(r => r.json())
    .then(items => {
      const reminderDays = new Set()
      items.forEach(r => {
        const d = new Date(r.due_at)
        if (d.getMonth() === month && d.getFullYear() === year) {
          reminderDays.add(d.getDate())
        }
      })
      renderCalDays(year, month, last.getDate(), offset, now.getDate(), reminderDays)
      renderUpcoming(items, now)
    })
    .catch(() => renderCalDays(year, month, last.getDate(), offset, now.getDate(), new Set()))

  $('calGrid').innerHTML = html
}

function renderCalDays(year, month, lastDay, offset, today, reminderDays) {
  const grid = $('calGrid')
  if (!grid) return

  const DAYS  = ['일', '월', '화', '수', '목', '금', '토']
  let html    = DAYS.map(d => `<div class="cal-day-hdr">${d}</div>`).join('')
  const now   = new Date()
  const isCurrentMonth = year === now.getFullYear() && month === now.getMonth()

  const firstOffset = new Date(year, month, 1).getDay()
  for (let i = 0; i < firstOffset; i++) {
    html += `<div class="cal-day other-month"></div>`
  }
  for (let d = 1; d <= lastDay; d++) {
    const isToday   = isCurrentMonth && d === today
    const hasRemind = reminderDays.has(d)
    html += `<div class="cal-day ${isToday ? 'today' : ''} ${hasRemind ? 'has-reminder' : ''}">${d}</div>`
  }
  grid.innerHTML = html
}

function renderUpcoming(items, now) {
  const el = $('calUpcoming')
  if (!el) return

  const upcoming = items
    .filter(r => new Date(r.due_at) >= now)
    .sort((a, b) => new Date(a.due_at) - new Date(b.due_at))
    .slice(0, 4)

  if (!upcoming.length) { el.innerHTML = ''; return }

  el.innerHTML = upcoming.map(r => {
    const dt  = new Date(r.due_at)
    const due = `${dt.getMonth()+1}/${dt.getDate()} ${String(dt.getHours()).padStart(2,'0')}:${String(dt.getMinutes()).padStart(2,'0')}`
    return `<div class="cal-event-item">
      <span class="cal-event-time">${due}</span>
      <span class="cal-event-title">${escapeHtml(r.title)}</span>
    </div>`
  }).join('')
}

// ══════════════════════════════════════════════════════════════════════════════
// 20. 시스템 모니터 패널 (SSE 데이터 활용)
// ══════════════════════════════════════════════════════════════════════════════

function updateSysmonPanel(data) {
  const cpu  = data.cpu_percent  ?? 0
  const ram  = data.ram_percent  ?? 0
  const gpu  = data.gpu_percent  ?? null
  const disk = data.disk_percent ?? 0

  const setBar = (id, val) => {
    const el = $(id)
    if (!el) return
    el.style.width = `${val}%`
    el.classList.toggle('high', val > 80)
  }
  const setVal = (id, txt) => { const el = $(id); if (el) el.textContent = txt }

  setBar('cpuBar', cpu);  setVal('cpuVal',  `${Math.round(cpu)}%`)
  setBar('ramBar', ram);  setVal('ramVal',  `${Math.round(ram)}%`)
  setBar('diskBar', disk); setVal('diskVal', `${Math.round(disk)}%`)

  if (gpu !== null) {
    setBar('gpuBar', gpu); setVal('gpuVal', `${Math.round(gpu)}%`)
  }

  const isHigh = cpu > 80 || ram > 85
  const statusEl = $('sysStatus')
  if (statusEl) {
    statusEl.textContent = isHigh ? 'HIGH LOAD' : 'NORMAL'
    statusEl.className   = 'panel-badge' + (isHigh ? ' high' : '')
  }

  // 추가 정보
  const extra = $('sysExtra')
  if (extra && data.cpu_temp) {
    extra.innerHTML = `<div class="sys-extra-item"><span>CPU 온도</span><span>${data.cpu_temp}°C</span></div>`
  }
}

// ══════════════════════════════════════════════════════════════════════════════
// 21. 앰비언트 테마 (날씨 + 시간)
// ══════════════════════════════════════════════════════════════════════════════

function updateAmbient(wmoCode) {
  const hour = new Date().getHours()
  const isNight = hour < 6 || hour >= 20

  document.body.classList.remove('amb-clear', 'amb-rain', 'amb-snow', 'amb-thunder', 'amb-night')

  if (isNight) {
    document.body.classList.add('amb-night')
  } else if ([95, 96, 99].includes(wmoCode)) {
    document.body.classList.add('amb-thunder')
  } else if (wmoCode >= 71 && wmoCode <= 77) {
    document.body.classList.add('amb-snow')
  } else if ((wmoCode >= 51 && wmoCode <= 65) || (wmoCode >= 80 && wmoCode <= 82)) {
    document.body.classList.add('amb-rain')
  } else {
    document.body.classList.add('amb-clear')
  }
}

// ══════════════════════════════════════════════════════════════════════════════
// 22. 슬래시 명령어 시스템
// ══════════════════════════════════════════════════════════════════════════════

const SLASH_COMMANDS = [
  { cmd: '/clear',   desc: '채팅 기록 지우기' },
  { cmd: '/status',  desc: '시스템 상태 표시' },
  { cmd: '/log',     desc: '로그 창 열기' },
  { cmd: '/dev',     desc: '개발 모드 전환' },
  { cmd: '/focus',   desc: '집중 모드 (사이드 패널 숨김)' },
  { cmd: '/docs',    desc: '문서 작성 모드' },
  { cmd: '/meeting', desc: '회의 모드' },
  { cmd: '/remind',  desc: '리마인더 목록' },
  { cmd: '/ngrok',   desc: 'ngrok 모바일 연동 설정' },
  { cmd: '/help',    desc: '명령어 목록' },
]

let _slashSelected = 0

function onChatInputKey(e) {
  const val = chatInput.value
  const menu = $('slashMenu')

  // 슬래시 메뉴 열기/닫기
  if (val.startsWith('/') && val.length >= 1) {
    const filtered = SLASH_COMMANDS.filter(c => c.cmd.startsWith(val.toLowerCase()))
    if (filtered.length) {
      _slashSelected = 0
      menu.classList.remove('hidden')
      menu.innerHTML = filtered.map((c, i) =>
        `<div class="slash-item ${i === 0 ? 'active' : ''}" onclick="execSlash('${c.cmd}')">
          <span class="slash-cmd">${escapeHtml(c.cmd)}</span>
          <span class="slash-desc">${escapeHtml(c.desc)}</span>
        </div>`
      ).join('')

      if (e.key === 'ArrowDown') {
        e.preventDefault()
        _slashSelected = Math.min(_slashSelected + 1, filtered.length - 1)
        menu.querySelectorAll('.slash-item').forEach((el, i) =>
          el.classList.toggle('active', i === _slashSelected))
        return
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault()
        _slashSelected = Math.max(_slashSelected - 1, 0)
        menu.querySelectorAll('.slash-item').forEach((el, i) =>
          el.classList.toggle('active', i === _slashSelected))
        return
      }
      if (e.key === 'Tab' || e.key === 'Enter') {
        e.preventDefault()
        execSlash(filtered[_slashSelected].cmd)
        return
      }
    } else {
      menu.classList.add('hidden')
    }
  } else {
    menu.classList.add('hidden')
  }

  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage() }
  if (e.key === 'Escape') menu.classList.add('hidden')
}

function execSlash(cmd) {
  $('slashMenu').classList.add('hidden')
  chatInput.value = ''

  switch (cmd) {
    case '/clear':
      messages.innerHTML = ''
      appendLog('채팅 기록 삭제', 'info')
      break
    case '/status': {
      const cpu = $('cpuVal')?.textContent || '--'
      const ram = $('ramVal')?.textContent || '--'
      appendMessage('assistant', `Sir, 현재 시스템 상태:\nCPU: ${cpu} · RAM: ${ram}`)
      break
    }
    case '/log':
      window.jarvis?.openLogWindow?.()
      break
    case '/dev':
      document.body.classList.remove('mode-focus')
      document.body.classList.add('mode-dev')
      appendMessage('assistant', 'Sir, 개발 모드로 전환했습니다. 로그 패널이 확장됩니다.')
      break
    case '/focus':
      document.body.classList.remove('mode-dev')
      document.body.classList.add('mode-focus')
      appendMessage('assistant', 'Sir, 집중 모드입니다. 사이드 패널을 숨겼습니다.')
      break
    case '/docs':
    case '/meeting':
      document.body.classList.remove('mode-dev', 'mode-focus')
      appendMessage('assistant', `Sir, ${cmd.slice(1)} 모드로 전환했습니다.`)
      chatInput.value = cmd.slice(1) + ' 모드 전환'
      sendMessage()
      return
    case '/remind':
      showReminderList()
      break
    case '/ngrok':
      enterSetup()
      appendLog('ngrok 설정 화면을 다시 엽니다', 'info')
      break
    case '/help':
      appendMessage('assistant',
        '**슬래시 명령어**\n' +
        SLASH_COMMANDS.map(c => `${c.cmd} — ${c.desc}`).join('\n')
      )
      break
  }
}

// ══════════════════════════════════════════════════════════════════════════════
// 23. OS 초기화 훅 (init에 연결)
// ══════════════════════════════════════════════════════════════════════════════

function initOsPanels() {
  initWeather()
  initCalendar()
  updateAmbient(0)
  // 날씨 10분마다 갱신
  setInterval(() => {
    initWeather()
    updateAmbient(_weatherCode)
  }, 600_000)
  // 달력 1분마다 갱신
  setInterval(initCalendar, 60_000)
}

// ── 앱 시작 ──────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', init)
