; ══════════════════════════════════════════════════════════════════════════════
; installer.nsh — JARVIS NSIS 커스텀 인스톨러 / 언인스톨러 매크로
; electron-builder의 nsis.include 옵션으로 로드됨
; ══════════════════════════════════════════════════════════════════════════════

; ── 설치 완료 후 처리 ─────────────────────────────────────────────────────────
!macro customInstall
  ; AppData 데이터 폴더 사전 생성 (Spring Boot가 쓸 경로)
  CreateDirectory "$APPDATA\jarvis-project\data"
  CreateDirectory "$APPDATA\jarvis-project\logs"

  ; 바탕화면 바로 가기 메시지
  DetailPrint "JARVIS 설치 완료 — 시작 시 자동 실행이 설정됩니다."
!macroend

; ── 제거 전 처리 (파일 삭제 전 호출) ─────────────────────────────────────────
!macro customUnInstall

  ; ── 1. 실행 중인 모든 JARVIS 관련 프로세스 강제 종료 ──────────────────────
  DetailPrint "JARVIS 프로세스 종료 중..."

  ; Electron HUD
  nsExec::ExecToLog 'taskkill /F /IM "JARVIS.exe" /T'
  ; Python 각성 센서
  nsExec::ExecToLog 'taskkill /F /IM "jarvis-wake.exe" /T'
  ; ngrok 터널
  nsExec::ExecToLog 'taskkill /F /IM "ngrok.exe" /T'
  ; Spring Boot (포트 8080 점유 Java 프로세스)
  nsExec::ExecToLog \
    'powershell -NoProfile -Command \
      "Get-Process -Id (Get-NetTCPConnection -LocalPort 8080 \
        -ErrorAction SilentlyContinue).OwningProcess \
        -ErrorAction SilentlyContinue | Stop-Process -Force"'

  ; 프로세스가 완전히 종료될 때까지 잠시 대기
  Sleep 1500

  ; ── 2. 윈도우 시작 프로그램 자동 실행 항목 완전 제거 ──────────────────────
  DetailPrint "시작 프로그램 등록 해제 중..."

  ; Electron app.setLoginItemSettings 가 등록하는 키들
  DeleteRegValue HKCU \
    "Software\Microsoft\Windows\CurrentVersion\Run" \
    "JARVIS"
  DeleteRegValue HKCU \
    "Software\Microsoft\Windows\CurrentVersion\Run" \
    "com.jarvis.overlay"
  DeleteRegValue HKCU \
    "Software\Microsoft\Windows\CurrentVersion\Run" \
    "JARVIS Personal Assistant"
  ; HKLM(전체 사용자) 등록 키도 시도
  DeleteRegValue HKLM \
    "Software\Microsoft\Windows\CurrentVersion\Run" \
    "JARVIS"

  DetailPrint "시작 프로그램 등록 해제 완료"

  ; ── 3. 사용자 데이터(H2 DB + ngrok 설정) 삭제 여부 확인 ──────────────────
  IfFileExists "$APPDATA\jarvis-project\*.*" +1 skip_data_check

  MessageBox MB_YESNO|MB_ICONQUESTION \
    "JARVIS 사용자 데이터를 삭제하시겠습니까?$\n$\
$\n삭제될 폴더:$\n$\
  $APPDATA\jarvis-project$\n$\
$\n포함 내용:$\n$\
  • H2 데이터베이스 (대화 기록, 설정)$\n$\
  • ngrok 터널 인증 설정$\n$\
  • 로그 파일$\n$\
$\n'아니오' 선택 시 데이터가 보존됩니다." \
    IDNO skip_data_delete

  DetailPrint "사용자 데이터 삭제 중: $APPDATA\jarvis-project"
  RMDir /r "$APPDATA\jarvis-project"
  DetailPrint "사용자 데이터 삭제 완료"

  skip_data_delete:
  skip_data_check:

!macroend
