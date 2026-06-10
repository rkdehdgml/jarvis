/**
 * main.js — JARVIS Overlay Electron Main Process
 * ─────────────────────────────────────────────────────────────────────────────
 * 자식 프로세스:
 *   · Spring Boot JAR      (port 8080 — DB, WebSocket, ngrok)
 *   · Python FastAPI Core  (port 8000 — AI 엔진, OS 제어, 음성)
 *   · Python 박수 센서     (wake_sensor — 박수 2번 각성)
 */

'use strict'

const { app, BrowserWindow, ipcMain, screen } = require('electron')
const path   = require('path')
const fs     = require('fs')
const { spawn } = require('child_process')

const SPRING_BOOT_URL = 'http://localhost:8080'
const FAST_API_URL    = 'http://localhost:8000'
const HEALTH_URL      = `${SPRING_BOOT_URL}/api/health`

// ── 자식 프로세스 참조 ────────────────────────────────────────────────────────
let springBootProc = null
let fastApiProc    = null
let pythonProc     = null
let win            = null
let logWin         = null

// ── 전체 화면 OS 모드 (상태별 창 크기 없음 — 항상 전체 화면) ─────────────────

// ── 경로 유틸 ─────────────────────────────────────────────────────────────────
function getDataDir() {
  const base = path.join(app.getPath('appData'), 'jarvis-project')
  fs.mkdirSync(path.join(base, 'data'), { recursive: true })
  fs.mkdirSync(path.join(base, 'logs'), { recursive: true })
  return base
}

function getPermissionFilePath() {
  return path.join(getDataDir(), 'permissions.json')
}

function readPermission() {
  try {
    const raw = fs.readFileSync(getPermissionFilePath(), 'utf-8')
    return JSON.parse(raw)
  } catch {
    return { mode: null }
  }
}

function writePermission(mode) {
  const data = { mode, consentedAt: new Date().toISOString() }
  fs.writeFileSync(getPermissionFilePath(), JSON.stringify(data, null, 2), 'utf-8')
  return data
}

function getSettingsFilePath() {
  return path.join(getDataDir(), 'settings.json')
}

function readSettings() {
  try {
    return JSON.parse(fs.readFileSync(getSettingsFilePath(), 'utf-8'))
  } catch {
    return { userName: 'Sir' }
  }
}

function writeSettings(patch) {
  const merged = { ...readSettings(), ...patch }
  fs.writeFileSync(getSettingsFilePath(), JSON.stringify(merged, null, 2), 'utf-8')
  return merged
}

function getResourcePath(...segments) {
  const base = app.isPackaged
    ? process.resourcesPath
    : path.join(__dirname, '..', '_dev_resources')  // 개발 시 폴백
  return path.join(base, ...segments)
}

function getJavaCmd() {
  if (app.isPackaged) {
    // 패키지 모드: 번들된 JRE 사용 — Java 설치 불필요
    const exe = process.platform === 'win32' ? 'java.exe' : 'java'
    return path.join(process.resourcesPath, 'jarvis-jre', 'bin', exe)
  }
  return 'java'  // 개발 환경: 시스템 Java 사용
}

function getPythonCmd() {
  if (app.isPackaged) {
    return [getResourcePath('jarvis-python', 'jarvis-wake.exe'), []]
  }
  const script = path.join(__dirname, '..', 'jarvis-core-ai',
                           'app', 'sensors', 'wake_sensor.py')
  return ['python', [script]]
}

function getFastApiCmd() {
  if (app.isPackaged) {
    return [getResourcePath('jarvis-python', 'jarvis-core.exe'), []]
  }
  const script = path.join(__dirname, '..', 'jarvis-core-ai',
                           'app', 'main_standalone.py')
  return ['python', [script]]
}

// ══════════════════════════════════════════════════════════════════════════════
// 1. 윈도우 시작 시 자동 실행 등록
// ══════════════════════════════════════════════════════════════════════════════
function setupAutoLaunch() {
  if (!app.isPackaged) return  // 개발 환경에서는 등록하지 않음

  app.setLoginItemSettings({
    openAtLogin:  true,
    openAsHidden: false,       // 자동 실행 시 창 표시
    name:         'JARVIS',
    path:         process.execPath,
    args:         ['--autostart'],
  })
}

const isAutostart = process.argv.includes('--autostart')

// ══════════════════════════════════════════════════════════════════════════════
// 2. Spring Boot JAR 시작
// ══════════════════════════════════════════════════════════════════════════════
function startSpringBoot() {
  const jarPath = getResourcePath('jarvis-backend.jar')
  const dataDir = path.join(getDataDir(), 'data')

  if (!fs.existsSync(jarPath)) {
    console.warn('[Main] jarvis-backend.jar 없음 — Spring Boot 건너뜀')
    console.warn('       패키지 빌드 후 사용하거나 직접 실행하세요.')
    return
  }

  const javaCmd = getJavaCmd()
  if (!fs.existsSync(javaCmd) && app.isPackaged) {
    console.error('[Main] 번들 JRE 없음:', javaCmd)
    console.error('       npm run build 전에 jlink로 jarvis-jre 를 생성하세요.')
    return
  }

  const h2Url = `jdbc:h2:file:${dataDir}/jarvis;AUTO_SERVER=TRUE`
  console.log('[Main] Spring Boot 시작 중...')
  console.log('[Main] Java 경로:', javaCmd)
  console.log('[Main] H2 DB 경로:', dataDir)

  springBootProc = spawn(javaCmd, [
    '-Xmx256m',                       // 메모리 제한
    '-Djava.awt.headless=true',       // GUI 비활성화
    `-DJARVIS_DATA_DIR=${dataDir}`,
    `-Dspring.datasource.url=${h2Url}`,
    '-jar', jarPath,
  ], {
    env:      { ...process.env },
    detached: false,
    stdio:    ['ignore', 'pipe', 'pipe'],
  })

  springBootProc.stdout.on('data', d =>
    process.stdout.write(`[SB] ${d}`))
  springBootProc.stderr.on('data', d =>
    process.stderr.write(`[SB·ERR] ${d}`))
  springBootProc.on('exit', code =>
    console.log(`[Main] Spring Boot 종료 (code: ${code})`))
  springBootProc.on('error', err =>
    console.error('[Main] Spring Boot 시작 실패:', err.message))
}

// ══════════════════════════════════════════════════════════════════════════════
// 3. Python FastAPI Core AI 시작 (port 8000)
// ══════════════════════════════════════════════════════════════════════════════
function startFastAPI() {
  const [cmd, args] = getFastApiCmd()

  if (app.isPackaged && !fs.existsSync(cmd)) {
    console.warn('[Main] jarvis-core.exe 없음 — FastAPI 건너뜀')
    console.warn('       npm run build:py-core 실행 후 다시 빌드하세요.')
    return
  }

  console.log('[Main] FastAPI Core AI 시작:', cmd)
  fastApiProc = spawn(cmd, args, {
    detached: false,
    stdio:    ['ignore', 'pipe', 'pipe'],
  })

  fastApiProc.stdout.on('data', d => process.stdout.write(`[API] ${d}`))
  fastApiProc.stderr.on('data', d => process.stderr.write(`[API·ERR] ${d}`))
  fastApiProc.on('exit',  code => console.log(`[Main] FastAPI 종료 (code: ${code})`))
  fastApiProc.on('error', err  => console.error('[Main] FastAPI 시작 실패:', err.message))
}

// ══════════════════════════════════════════════════════════════════════════════
// 4. Python 박수 각성 센서 시작
// ══════════════════════════════════════════════════════════════════════════════
function startPythonSensor() {
  const [cmd, args] = getPythonCmd()

  if (app.isPackaged && !fs.existsSync(cmd)) {
    console.warn('[Main] jarvis-wake.exe 없음 — Python 센서 건너뜀')
    return
  }

  console.log('[Main] Python 박수 감지 센서 시작:', cmd)
  pythonProc = spawn(cmd, args, {
    detached: false,
    stdio:    ['ignore', 'pipe', 'pipe'],
  })

  pythonProc.stdout.on('data', d => process.stdout.write(`[PY] ${d}`))
  pythonProc.stderr.on('data', d => process.stderr.write(`[PY·ERR] ${d}`))
  pythonProc.on('exit',  code => console.log(`[Main] Python 센서 종료 (code: ${code})`))
  pythonProc.on('error', err  => console.warn('[Main] Python 센서 오류:', err.message))
}

// ══════════════════════════════════════════════════════════════════════════════
// 5. Spring Boot 헬스체크 (최대 30초 대기)
// ══════════════════════════════════════════════════════════════════════════════
async function waitForBackend(maxMs = 30_000, intervalMs = 1_500) {
  const deadline = Date.now() + maxMs
  while (Date.now() < deadline) {
    try {
      const { net } = require('electron')
      const req = net.request(HEALTH_URL)
      const ok  = await new Promise(resolve => {
        req.on('response', res => resolve(res.statusCode === 200))
        req.on('error',    ()  => resolve(false))
        req.end()
      })
      if (ok) {
        console.log('[Main] Spring Boot 준비 완료')
        return true
      }
    } catch { /* 계속 폴링 */ }
    await new Promise(r => setTimeout(r, intervalMs))
  }
  console.warn('[Main] Spring Boot 타임아웃 — 창 표시 진행')
  return false
}

// ══════════════════════════════════════════════════════════════════════════════
// 6. Electron 창 생성
// ══════════════════════════════════════════════════════════════════════════════
function createWindow() {
  win = new BrowserWindow({
    // OS 수준 전체화면 — Windows 작업표시줄까지 완전히 가림
    fullscreen:      true,
    frame:           false,
    transparent:     false,
    backgroundColor: '#020b18',   // .os-bg 와 동일 — 로딩 깜빡임 방지
    hasShadow:       false,
    skipTaskbar:     false,
    webPreferences: {
      preload:          path.join(__dirname, 'preload.js'),
      nodeIntegration:  false,
      contextIsolation: true,
    },
  })

  win.loadFile(path.join(__dirname, 'renderer', 'index.html'))

  if (isAutostart) {
    console.log('[Main] 자동 실행 모드')
  }
}

// ══════════════════════════════════════════════════════════════════════════════
// 7. IPC 핸들러
// ══════════════════════════════════════════════════════════════════════════════

// 창 상태 전환 — 전체화면 OS 모드에서는 크기 변경 없음
ipcMain.on('set-window-state', () => { /* no-op: full-screen OS mode */ })

// 창 컨트롤
ipcMain.on('close-window',    () => win?.close())
ipcMain.on('minimize-window', () => win?.minimize())
ipcMain.on('toggle-devtools', () => win?.webContents.toggleDevTools())
ipcMain.on('set-ignore-mouse', (_, ignore) =>
  win?.setIgnoreMouseEvents(ignore, { forward: true }))

// 권한 설정
ipcMain.handle('get-permission', () => readPermission())
ipcMain.handle('set-permission', (_, mode) => {
  const result = writePermission(mode)
  // 모드 변경 시 박수 센서 즉시 적용
  if (mode === 'LIMITED' && pythonProc) {
    pythonProc.kill('SIGTERM')
    pythonProc = null
    console.log('[Main] LIMITED 모드 전환 — 박수 센서 중단')
  } else if (mode === 'FULL' && !pythonProc) {
    startPythonSensor()
    console.log('[Main] FULL 모드 전환 — 박수 센서 시작')
  }
  return result
})

// 사용자 설정
ipcMain.handle('get-settings', () => readSettings())
ipcMain.handle('set-settings', (_, patch) => writeSettings(patch))

// 백엔드 URL
ipcMain.handle('get-backend-url', () => SPRING_BOOT_URL)

// 로그 창 열기
ipcMain.on('open-log-window', () => {
  if (logWin && !logWin.isDestroyed()) {
    logWin.focus()
    return
  }
  const { width: sw, height: sh } = screen.getPrimaryDisplay().workAreaSize
  logWin = new BrowserWindow({
    width:       560,
    height:      480,
    x:           sw - 580,
    y:           sh - 520,
    transparent: true,
    frame:       false,
    alwaysOnTop: true,
    resizable:   true,
    hasShadow:   false,
    skipTaskbar: false,
    title:       'JARVIS — System Log',
    webPreferences: {
      preload:          path.join(__dirname, 'preload.js'),
      nodeIntegration:  false,
      contextIsolation: true,
    },
  })
  logWin.loadFile(path.join(__dirname, 'renderer', 'log.html'))
  logWin.on('closed', () => { logWin = null })
})

// 로그 엔트리 중계: 메인 창 -> 로그 창
ipcMain.on('log-entry', (_, entry) => {
  if (logWin && !logWin.isDestroyed()) {
    logWin.webContents.send('log-entry', entry)
  }
})

// 자동 실행 토글 (대시보드에서 호출)
ipcMain.handle('set-auto-launch', (_, enabled) => {
  app.setLoginItemSettings({ openAtLogin: enabled, name: 'JARVIS' })
  return app.getLoginItemSettings().openAtLogin
})
ipcMain.handle('get-auto-launch', () =>
  app.getLoginItemSettings().openAtLogin)

// ══════════════════════════════════════════════════════════════════════════════
// 8. 앱 수명 주기
// ══════════════════════════════════════════════════════════════════════════════
app.whenReady().then(async () => {
  setupAutoLaunch()

  // 자식 프로세스 시작 (Spring Boot → FastAPI 순)
  startSpringBoot()
  startFastAPI()

  // 박수 센서: LIMITED 모드면 시작하지 않음
  const perm = readPermission()
  if (perm.mode === 'LIMITED') {
    console.log('[Main] LIMITED 모드 — 박수 각성 센서 비활성화')
  } else {
    startPythonSensor()
  }

  // Spring Boot가 올라올 때까지 대기 (최대 30초)
  if (app.isPackaged) {
    await waitForBackend(30_000)
  }

  createWindow()

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})

// ── 종료 시 자식 프로세스 정리 ────────────────────────────────────────────────
app.on('before-quit', () => {
  console.log('[Main] 자식 프로세스 종료 중...')
  springBootProc?.kill('SIGTERM')
  fastApiProc?.kill('SIGTERM')
  pythonProc?.kill('SIGTERM')
})

// ── 예외 처리 (프로세스 유지) ─────────────────────────────────────────────────
process.on('uncaughtException',  err => console.error('[Main] uncaughtException:', err))
process.on('unhandledRejection', err => console.error('[Main] unhandledRejection:', err))
