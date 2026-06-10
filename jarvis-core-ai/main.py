"""
JARVIS Sensor Daemon — main.py
══════════════════════════════════════════════════════════════════════════════
상시 감시 루프:

  [대기]  카메라로 손동작 폴링  ────────────┐
     │                                       │ (감지 실패 / 타임아웃)
     ▼  검지 손가락 올리기 감지              │
  [활성]  마이크 녹음 (5초)  ←──────────────┘
     │
     ▼  faster-whisper STT
  [처리]  텍스트 출력 → AI 라우터 호출 (선택)
     │
     └──→ [대기] 상태로 복귀

실행:
    python main.py                  # 웹캠 미리보기 ON
    python main.py --no-preview     # 웹캠 미리보기 OFF (headless 서버용)
    python main.py --record-sec 8   # 녹음 시간 변경
    python main.py --model small    # Whisper 모델 크기 변경
══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

# 프로젝트 루트를 sys.path에 추가 (패키지 import 보장)
sys.path.insert(0, str(Path(__file__).parent))

from app.config import settings
from app.sensors.vision_sensor import VisionSensor
from app.sensors.voice_sensor import VoiceSensor, TranscriptResult


# ── 설정 상수 ─────────────────────────────────────────────────────────────────

BANNER = r"""
     ██╗ █████╗ ██████╗ ██╗   ██╗██╗███████╗
     ██║██╔══██╗██╔══██╗██║   ██║██║██╔════╝
     ██║███████║██████╔╝██║   ██║██║███████╗
██   ██║██╔══██║██╔══██╗╚██╗ ██╔╝██║╚════██║
╚█████╔╝██║  ██║██║  ██║ ╚████╔╝ ██║███████║
 ╚════╝ ╚═╝  ╚═╝╚═╝  ╚═╝  ╚═══╝  ╚═╝╚══════╝
  Sensor Daemon  |  Raise ☝ to activate
"""

COOLDOWN_SEC    = 2.0    # 명령 처리 후 재활성화 대기 시간
HOLD_FRAMES     = 6      # 제스처를 이 프레임 수만큼 유지해야 활성화


# ── AI 라우터 연동 (옵션) ─────────────────────────────────────────────────────

async def _forward_to_ai(text: str) -> None:
    """텍스트 명령을 AI 라우터로 전달 — FastAPI 서버가 실행 중일 때 사용."""
    try:
        import httpx
        async with httpx.AsyncClient(timeout=60) as client:
            async with client.stream(
                "POST",
                f"{settings.dashboard_backend_url.replace('8080','8000')}/api/chat/stream",
                json={"message": text, "history": []},
            ) as resp:
                print("\n[JARVIS AI] ", end="", flush=True)
                async for chunk in resp.aiter_text():
                    print(chunk, end="", flush=True)
                print()
    except Exception as e:
        print(f"[JARVIS] AI 라우터 연결 실패 (독립 실행 중): {e}")


# ── 명령 처리기 ───────────────────────────────────────────────────────────────

def handle_command(result: TranscriptResult, forward_ai: bool) -> None:
    """STT 결과를 받아 출력하고, 설정에 따라 AI에 전달."""
    print("\n" + "─" * 60)
    print(f"  [명령 인식]  {result.text!r}")
    print(f"  언어: {result.language} ({result.language_probability:.0%})  "
          f"| 처리: {result.processing_sec}s")
    print("─" * 60 + "\n")

    if forward_ai and result.text:
        asyncio.run(_forward_to_ai(result.text))


# ── 상태 표시 헬퍼 ────────────────────────────────────────────────────────────

class _StateIndicator:
    """터미널에 현재 루프 상태를 한 줄로 표시."""

    _STATES = {
        "waiting":    "[ ☁  대기 중 — 검지 손가락을 올려주세요 ]",
        "activated":  "[ ✅ 제스처 감지! 마이크 준비 중... ]",
        "recording":  "[ 🎙  녹음 중... ]",
        "processing": "[ ⚙  처리 중... ]",
        "cooldown":   "[ ⏳ 쿨다운... ]",
    }

    def show(self, state: str) -> None:
        msg = self._STATES.get(state, state)
        print(f"\r{msg:<60}", end="", flush=True)


# ── 메인 루프 ─────────────────────────────────────────────────────────────────

def run(
    show_preview: bool = True,
    record_sec: float = 5.0,
    model_size: str | None = None,
    whisper_device: str | None = None,
    forward_ai: bool = False,
    camera_index: int = 0,
) -> None:
    model_size    = model_size    or settings.whisper_model_size
    whisper_device= whisper_device or settings.whisper_device

    print(BANNER)
    print(f"  Whisper 모델  : {model_size} @ {whisper_device}")
    print(f"  카메라 인덱스 : {camera_index}")
    print(f"  녹음 길이     : {record_sec}s")
    print(f"  AI 전달       : {'ON' if forward_ai else 'OFF (텍스트만 출력)'}")
    print()

    indicator = _StateIndicator()
    voice     = VoiceSensor(model_size=model_size, device=whisper_device)

    with VisionSensor(camera_index=camera_index) as vision:
        while True:
            try:
                # ── Phase 1: 제스처 대기 ──────────────────────────────────
                indicator.show("waiting")
                activated = vision.wait_for_gesture(
                    target       = VisionSensor.GESTURE_INDEX_UP,
                    show_preview = show_preview,
                    hold_frames  = HOLD_FRAMES,
                    timeout_sec  = None,    # 무한 대기
                )

                if not activated:
                    time.sleep(0.01)
                    continue

                # ── Phase 2: 제스처 확인 ──────────────────────────────────
                indicator.show("activated")
                print()                     # 줄 바꿈
                time.sleep(0.3)             # 짧은 피드백 딜레이

                # ── Phase 3: 음성 녹음 ────────────────────────────────────
                indicator.show("recording")
                result = voice.listen(
                    duration_sec = record_sec,
                    countdown    = True,
                )

                # ── Phase 4: 결과 처리 ────────────────────────────────────
                indicator.show("processing")

                if result:
                    handle_command(result, forward_ai=forward_ai)
                else:
                    print("\n[JARVIS] 음성이 감지되지 않았습니다. 다시 시도해 주세요.")

                # ── Phase 5: 쿨다운 ──────────────────────────────────────
                indicator.show("cooldown")
                time.sleep(COOLDOWN_SEC)

            except KeyboardInterrupt:
                print("\n\n[JARVIS] 센서 데몬을 종료합니다.")
                break

            except Exception as e:
                print(f"\n[JARVIS] 오류 발생: {e}")
                time.sleep(1.0)             # 오류 후 잠시 대기 후 재시도


# ── CLI 진입점 ────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="JARVIS Sensor Daemon — 손동작으로 음성 명령 활성화"
    )
    p.add_argument("--no-preview",   action="store_true",
                   help="웹캠 미리보기 창 비활성화 (headless 환경)")
    p.add_argument("--record-sec",   type=float, default=5.0,
                   help="음성 녹음 길이 (초, 기본값: 5)")
    p.add_argument("--model",        type=str,   default=None,
                   help="Whisper 모델 크기 (tiny/base/small/medium/large)")
    p.add_argument("--device",       type=str,   default=None,
                   help="Whisper 연산 장치 (cpu / cuda)")
    p.add_argument("--forward-ai",   action="store_true",
                   help="STT 결과를 FastAPI AI 라우터에 전달")
    p.add_argument("--camera",       type=int,   default=0,
                   help="웹캠 인덱스 (기본값: 0)")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    run(
        show_preview  = not args.no_preview,
        record_sec    = args.record_sec,
        model_size    = args.model,
        whisper_device= args.device,
        forward_ai    = args.forward_ai,
        camera_index  = args.camera,
    )
