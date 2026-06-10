"""
wake_sensor.py — '박수 2번' 각성 감지기
════════════════════════════════════════════════════════════════════════════════
알고리즘:
  sounddevice 콜백 (동기) → asyncio.Queue → WebSocket 전송 (비동기)

박수 판정 기준:
  · RMS 레벨이 RMS_THRESHOLD 이상인 피크 = 박수 1회
  · CLAP_COOLDOWN 이내 중복 피크는 동일 박수로 간주 (무시)
  · DOUBLE_WINDOW 이내 피크가 2회 감지되면 → WAKE_UP 신호 발송
  · DOUBLE_WINDOW 경과 후 첫 피크가 없으면 카운터 리셋

실행:
  python wake_sensor.py                          # 기본값
  python wake_sensor.py --threshold 0.3          # 민감도 조정
  python wake_sensor.py --ws ws://HOST:8080/ws-status
════════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import threading
import time
from typing import Optional

import numpy as np
import sounddevice as sd

log = logging.getLogger("wake_sensor")
logging.basicConfig(
    level   = logging.INFO,
    format  = "[JARVIS·Wake] %(message)s",
)

# ── 기본 설정 ─────────────────────────────────────────────────────────────────
SAMPLE_RATE    = 16_000   # Hz — 음성 처리 표준
BLOCK_FRAMES   = 1_024    # ~64ms 청크
RMS_THRESHOLD  = 0.20     # 박수 감지 임계값 (0.0 ~ 1.0); 환경에 따라 조정
CLAP_COOLDOWN  = 0.18     # 동일 박수 내 중복 피크 무시 (초)
DOUBLE_WINDOW  = 1.5      # 이 시간 안에 2번 박수 → WAKE_UP (초)
SILENCE_RATIO  = 0.25     # 피크 종료 판정 (threshold × ratio 이하일 때)
WS_URL         = "ws://localhost:8080/ws-status"
RECONNECT_SEC  = 3.0


# ══════════════════════════════════════════════════════════════════════════════
# 1. 공유 상태 (콜백 스레드 ↔ 비동기 루프)
# ══════════════════════════════════════════════════════════════════════════════

_lock            = threading.Lock()
_first_clap_at:  float = 0.0   # 첫 번째 박수 타임스탬프 (0 = 없음)
_last_peak_at:   float = 0.0   # 마지막 피크 타임스탬프
_in_clap:        bool  = False  # 현재 박수 내부 (중복 방지 플래그)

_event_queue: asyncio.Queue     # main에서 초기화
_event_loop:  asyncio.AbstractEventLoop  # main에서 할당


# ══════════════════════════════════════════════════════════════════════════════
# 2. 오디오 콜백 (sounddevice 내부 스레드에서 호출)
# ══════════════════════════════════════════════════════════════════════════════

def _audio_callback(
    indata: np.ndarray,
    frames: int,
    time_info,
    status: sd.CallbackFlags,
) -> None:
    global _first_clap_at, _last_peak_at, _in_clap

    if status:
        log.debug("오디오 상태: %s", status)

    # 모노 RMS 계산
    mono = indata[:, 0] if indata.ndim > 1 else indata
    rms  = float(np.sqrt(np.mean(mono ** 2)))
    now  = time.monotonic()

    with _lock:
        # 첫 번째 박수 창 만료 리셋
        if _first_clap_at > 0 and (now - _first_clap_at) > DOUBLE_WINDOW:
            log.debug("첫 박수 창 만료 — 리셋")
            _first_clap_at = 0.0

        # ── 피크 감지 ──────────────────────────────────────────────────────
        if (rms >= RMS_THRESHOLD
                and not _in_clap
                and (now - _last_peak_at) >= CLAP_COOLDOWN):

            _in_clap      = True
            _last_peak_at = now

            if _first_clap_at > 0:
                # ✅ 두 번째 박수 — WAKE_UP 발송
                elapsed = now - _first_clap_at
                _first_clap_at = 0.0
                log.info("👏👏 박수 2번 감지! (%.2fs 간격) → WAKE_UP", elapsed)
                _threadsafe_fire()
            else:
                # 첫 번째 박수
                _first_clap_at = now
                log.info("👏 박수 1번 — %.1fs 내 두 번째 박수 대기...", DOUBLE_WINDOW)

        # ── 박수 종료 판정 (침묵 복귀) ─────────────────────────────────────
        elif rms < RMS_THRESHOLD * SILENCE_RATIO:
            _in_clap = False


def _threadsafe_fire() -> None:
    """콜백 스레드에서 asyncio 큐에 안전하게 이벤트를 넣는다."""
    try:
        _event_loop.call_soon_threadsafe(_event_queue.put_nowait, 1)
    except Exception as e:
        log.warning("이벤트 큐 전송 실패: %s", e)


# ══════════════════════════════════════════════════════════════════════════════
# 3. WebSocket 브로드캐스터 (비동기)
# ══════════════════════════════════════════════════════════════════════════════

async def _ws_broadcaster(ws_url: str) -> None:
    """큐에서 WAKE_UP 이벤트를 읽어 Spring Boot WebSocket으로 전송."""
    try:
        import websockets
        from websockets.exceptions import ConnectionClosed, WebSocketException
    except ImportError:
        log.error("websockets 라이브러리 없음: pip install websockets")
        return

    while True:
        try:
            async with websockets.connect(ws_url, ping_interval=20) as ws:
                log.info("WebSocket 연결됨: %s", ws_url)
                while True:
                    await _event_queue.get()    # WAKE_UP 이벤트 대기
                    payload = json.dumps({
                        "event":  "wake_up",
                        "source": "clap_detector",
                        "ts":     time.time(),
                    })
                    await ws.send(payload)
                    log.info("✅ WAKE_UP 신호 전송 완료")

        except (ConnectionClosed, WebSocketException, OSError) as e:
            log.warning("WebSocket 연결 끊김 (%s) — %.0f초 후 재연결", e, RECONNECT_SEC)
            await asyncio.sleep(RECONNECT_SEC)
        except Exception as e:
            log.error("예상치 못한 오류: %s", e)
            await asyncio.sleep(RECONNECT_SEC)


# ══════════════════════════════════════════════════════════════════════════════
# 4. 진입점
# ══════════════════════════════════════════════════════════════════════════════

async def _main(ws_url: str, threshold: float, device: Optional[int]) -> None:
    global _event_queue, _event_loop, RMS_THRESHOLD

    RMS_THRESHOLD = threshold
    _event_queue  = asyncio.Queue()
    _event_loop   = asyncio.get_running_loop()

    log.info("박수 감지기 시작 — 임계값: %.2f | WebSocket: %s", threshold, ws_url)
    log.info("박수를 2번 치면 자비스가 각성합니다. (Ctrl+C로 종료)")

    stream_kwargs: dict = dict(
        channels   = 1,
        samplerate = SAMPLE_RATE,
        blocksize  = BLOCK_FRAMES,
        dtype      = "float32",
        callback   = _audio_callback,
    )
    if device is not None:
        stream_kwargs["device"] = device

    # sounddevice 스트림 + WebSocket 브로드캐스터를 동시 실행
    with sd.InputStream(**stream_kwargs):
        await _ws_broadcaster(ws_url)


def run(
    ws_url:    str  = WS_URL,
    threshold: float = RMS_THRESHOLD,
    device:    Optional[int] = None,
) -> None:
    """외부에서 호출 가능한 동기 진입점."""
    asyncio.run(_main(ws_url, threshold, device))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="JARVIS 박수 2번 각성 감지기")
    parser.add_argument("--ws",        default=WS_URL,        help="WebSocket URL")
    parser.add_argument("--threshold", default=RMS_THRESHOLD, type=float,
                        help="박수 RMS 임계값 (기본: 0.20)")
    parser.add_argument("--device",    default=None,          type=int,
                        help="마이크 장치 인덱스 (기본: 시스템 기본)")
    parser.add_argument("--list-devices", action="store_true",
                        help="사용 가능한 오디오 장치 목록 출력")
    args = parser.parse_args()

    if args.list_devices:
        print(sd.query_devices())
    else:
        try:
            run(ws_url=args.ws, threshold=args.threshold, device=args.device)
        except KeyboardInterrupt:
            log.info("박수 감지기 종료.")
