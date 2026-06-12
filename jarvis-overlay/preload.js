/**
 * preload.js — 보안 브릿지 (contextBridge)
 * Main 프로세스와 Renderer 사이의 안전한 IPC 채널을 노출합니다.
 */

const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('jarvis', {

  // ── 창 제어 ──────────────────────────────────────────────────────────────
  setWindowState:    (state)  => ipcRenderer.send('set-window-state', state),
  closeWindow:       ()       => ipcRenderer.send('close-window'),
  minimizeWindow:    ()       => ipcRenderer.send('minimize-window'),
  setIgnoreMouse:    (ignore) => ipcRenderer.send('set-ignore-mouse', ignore),

  // ── Dynamic Overlay Bar (WORKING 모드) ──────────────────────────────────
  enterOverlayBar:   ()       => ipcRenderer.send('enter-overlay-bar'),
  exitOverlayBar:    ()       => ipcRenderer.send('exit-overlay-bar'),

  // ── 백엔드 URL 조회 ──────────────────────────────────────────────────────
  getBackendUrl:     ()       => ipcRenderer.invoke('get-backend-url'),

  // ── 권한 설정 ─────────────────────────────────────────────────────────────
  getPermission:     ()       => ipcRenderer.invoke('get-permission'),
  setPermission:     (mode)   => ipcRenderer.invoke('set-permission', mode),

  // ── 사용자 설정 ───────────────────────────────────────────────────────────
  getSettings:       ()       => ipcRenderer.invoke('get-settings'),
  setSettings:       (patch)  => ipcRenderer.invoke('set-settings', patch),

  // ── 이벤트 수신 (Main → Renderer) ────────────────────────────────────────
  onStatusUpdate: (cb) => {
    ipcRenderer.on('status-update', (_, data) => cb(data))
    return () => ipcRenderer.removeAllListeners('status-update')
  },

  // ── 시스템 로그 창 ────────────────────────────────────────────────────────
  openLogWindow:  ()        => ipcRenderer.send('open-log-window'),
  sendLog:        (entry)   => ipcRenderer.send('log-entry', entry),
  onLogEntry:     (cb)      => {
    ipcRenderer.on('log-entry', (_, entry) => cb(entry))
    return () => ipcRenderer.removeAllListeners('log-entry')
  },
})
