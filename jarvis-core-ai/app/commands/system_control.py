"""
system_control.py — 시스템 제어 내장 명령
════════════════════════════════════════════════════════════════════════════════
지원 명령:
  · 볼륨 조절 (설정/올리기/내리기/음소거) — pycaw (Windows)
  · 전원 관리 (종료/재시작/절전)
  · 앱 실행/종료 — data/apps.json 매핑
  · 스크린샷 (사용자 지정 파일명)
  · 화면 녹화 + 음성 동시 녹음 — ffmpeg
  · 음성만 녹음 — sounddevice + wave
════════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import time
import wave
from pathlib import Path
from typing import Optional

from app.commands.registry import CommandResult, register
from app.config import settings

_APPS_FILE = Path(__file__).parent.parent.parent / "data" / "apps.json"
_CAPTURES_DIR = Path(settings.os_captures_dir)


def _load_apps() -> dict[str, str]:
    if not _APPS_FILE.exists():
        return {}
    try:
        return json.loads(_APPS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _app_names_pattern() -> str:
    """data/apps.json에 등록된 앱 이름들의 alternation 패턴.

    등록되지 않은 이름(유튜브, 네이버 등)은 이 패턴에 매칭되지 않으므로
    web_media 등 다른 모듈의 "열기" 명령으로 자연스럽게 폴백된다.
    """
    names = sorted(_load_apps().keys(), key=len, reverse=True)
    if not names:
        return r"(?!)"  # 항상 불일치
    return "|".join(re.escape(n) for n in names)


# ══════════════════════════════════════════════════════════════════════════════
# 1. 볼륨 조절 (Windows: pycaw)
# ══════════════════════════════════════════════════════════════════════════════

def _get_volume_iface():
    """pycaw 볼륨 인터페이스. 실패 시 None."""
    if sys.platform != "win32":
        return None
    try:
        from ctypes import POINTER, cast

        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

        devices = AudioUtilities.GetSpeakers()
        interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        return cast(interface, POINTER(IAudioEndpointVolume))
    except Exception:
        return None


@register("볼륨 설정", r"볼륨\s*(?:을|를)?\s*(\d{1,3})\s*(?:%|퍼센트)?\s*(?:으로|로)?\s*(?:맞춰|설정|조절|바꿔)")
async def set_volume(m, text: str) -> CommandResult:
    pct = max(0, min(100, int(m.group(1))))
    iface = _get_volume_iface()
    if iface is None:
        return CommandResult(text="볼륨 조절은 Windows에서만 지원되거나, pycaw 초기화에 실패했습니다.")
    iface.SetMasterVolumeLevelScalar(pct / 100.0, None)
    return CommandResult(text=f"볼륨을 {pct}%로 설정했습니다.")


@register("볼륨 올리기/내리기", r"볼륨\s*(?:을|를)?\s*(올려|높여|키워|줄여|내려|낮춰)")
async def adjust_volume(m, text: str) -> CommandResult:
    direction = m.group(1)
    delta = 0.1 if direction in ("올려", "높여", "키워") else -0.1
    iface = _get_volume_iface()
    if iface is None:
        return CommandResult(text="볼륨 조절은 Windows에서만 지원되거나, pycaw 초기화에 실패했습니다.")
    cur = iface.GetMasterVolumeLevelScalar()
    new = max(0.0, min(1.0, cur + delta))
    iface.SetMasterVolumeLevelScalar(new, None)
    return CommandResult(text=f"볼륨을 {round(new * 100)}%로 {'올렸습니다' if delta > 0 else '내렸습니다'}.")


@register("음소거", r"음소거(?:\s*(해제|풀어))?|볼륨\s*꺼")
async def mute_volume(m, text: str) -> CommandResult:
    iface = _get_volume_iface()
    if iface is None:
        return CommandResult(text="음소거는 Windows에서만 지원되거나, pycaw 초기화에 실패했습니다.")
    unmute = bool(m.group(1))
    iface.SetMute(0 if unmute else 1, None)
    return CommandResult(text="음소거를 해제했습니다." if unmute else "음소거했습니다.")


# ══════════════════════════════════════════════════════════════════════════════
# 2. 전원 관리
# ══════════════════════════════════════════════════════════════════════════════

@register("시스템 종료", r"(?:컴퓨터|pc|시스템)?\s*(?:를|을)?\s*(?:꺼줘|종료해|종료시켜)")
async def shutdown_system(m, text: str) -> CommandResult:
    if sys.platform == "win32":
        subprocess.Popen(["shutdown", "/s", "/t", "10"])
    else:
        subprocess.Popen(["shutdown", "-h", "+1"])
    return CommandResult(text="10초 후 시스템을 종료합니다. 취소하려면 '종료 취소'라고 말씀해 주세요.")


@register("시스템 종료 취소", r"종료\s*취소|시스템\s*종료\s*(?:취소|중단)")
async def cancel_shutdown(m, text: str) -> CommandResult:
    if sys.platform == "win32":
        subprocess.Popen(["shutdown", "/a"])
    else:
        subprocess.Popen(["shutdown", "-c"])
    return CommandResult(text="시스템 종료를 취소했습니다.")


@register("시스템 재시작", r"(?:컴퓨터|pc|시스템)?\s*(?:를|을)?\s*재시작(?:해줘|시켜줘|해)?")
async def restart_system(m, text: str) -> CommandResult:
    if sys.platform == "win32":
        subprocess.Popen(["shutdown", "/r", "/t", "10"])
    else:
        subprocess.Popen(["shutdown", "-r", "+1"])
    return CommandResult(text="10초 후 시스템을 재시작합니다. 취소하려면 '종료 취소'라고 말씀해 주세요.")


@register("절전 모드", r"절전\s*모드|컴퓨터\s*재워|시스템\s*재워|슬립\s*모드(?:로\s*전환)?")
async def sleep_system(m, text: str) -> CommandResult:
    if sys.platform == "win32":
        subprocess.Popen(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"])
    else:
        subprocess.Popen(["systemctl", "suspend"])
    return CommandResult(text="절전 모드로 전환합니다.")


# ══════════════════════════════════════════════════════════════════════════════
# 3. 앱 실행 / 종료
# ══════════════════════════════════════════════════════════════════════════════

@register("앱 실행", rf"^({_app_names_pattern()})\s*(?:을|를)?\s*(?:열어줘|실행해줘|켜줘|시작해줘)$")
async def launch_app(m, text: str) -> CommandResult:
    name = m.group(1).strip()
    apps = _load_apps()
    target = apps.get(name)
    if not target:
        return CommandResult(text=f"'{name}'은 등록된 앱 목록(data/apps.json)에 없습니다.")
    try:
        if sys.platform == "win32":
            subprocess.Popen(["cmd", "/c", "start", "", target], shell=False)
        else:
            subprocess.Popen([target])
    except OSError as e:
        return CommandResult(text=f"'{name}' 실행에 실패했습니다: {e}")
    return CommandResult(text=f"{name}을 실행했습니다.")


@register("앱 종료", rf"^({_app_names_pattern()})\s*(?:을|를)?\s*(?:종료해줘|꺼줘|닫아줘)$")
async def kill_app(m, text: str) -> CommandResult:
    name = m.group(1).strip()
    apps = _load_apps()
    target = apps.get(name)
    if not target:
        return CommandResult(text=f"'{name}'은 등록된 앱 목록(data/apps.json)에 없습니다.")
    proc_name = Path(target).name
    if sys.platform == "win32":
        result = subprocess.run(["taskkill", "/IM", proc_name, "/F"], capture_output=True, text=True)
    else:
        result = subprocess.run(["pkill", "-f", proc_name], capture_output=True, text=True)
    if result.returncode != 0:
        return CommandResult(text=f"{name}이 실행 중이지 않거나 종료에 실패했습니다.")
    return CommandResult(text=f"{name}을 종료했습니다.")


# ══════════════════════════════════════════════════════════════════════════════
# 4. 스크린샷
# ══════════════════════════════════════════════════════════════════════════════

@register("스크린샷", r"스크린샷\s*(?:찍어줘|캡처해줘|저장해줘)?(?:\s*(?:이름은|파일명은|이름으로)\s*([\w가-힣\-_.]+))?")
async def take_screenshot(m, text: str) -> CommandResult:
    import pyautogui

    _CAPTURES_DIR.mkdir(parents=True, exist_ok=True)
    name = m.group(1)
    if name:
        filename = name if name.endswith((".png", ".jpg", ".jpeg")) else f"{name}.png"
    else:
        filename = f"screenshot_{time.strftime('%Y%m%d_%H%M%S')}.png"

    save_path = _CAPTURES_DIR / filename
    img = pyautogui.screenshot()
    img.save(save_path)
    return CommandResult(text=f"스크린샷을 저장했습니다: {save_path}", data={"path": str(save_path)})


# ══════════════════════════════════════════════════════════════════════════════
# 5. 화면 녹화 + 음성 동시 녹음 (ffmpeg)
# ══════════════════════════════════════════════════════════════════════════════

_screen_record_proc: Optional[subprocess.Popen] = None
_screen_record_path: Optional[Path] = None


@register("화면 녹화 시작", r"화면\s*녹화\s*(?:시작해줘|시작|해줘)")
async def start_screen_recording(m, text: str) -> CommandResult:
    global _screen_record_proc, _screen_record_path

    if _screen_record_proc is not None:
        return CommandResult(text="이미 화면 녹화가 진행 중입니다.")

    if shutil.which("ffmpeg") is None:
        return CommandResult(text="ffmpeg가 설치되어 있지 않습니다. https://ffmpeg.org 에서 설치 후 PATH에 추가해 주세요.")

    _CAPTURES_DIR.mkdir(parents=True, exist_ok=True)
    out_path = _CAPTURES_DIR / f"recording_{time.strftime('%Y%m%d_%H%M%S')}.mp4"

    if sys.platform == "win32":
        cmd = [
            "ffmpeg", "-y",
            "-f", "gdigrab", "-framerate", "30", "-i", "desktop",
            "-f", "dshow", "-i", "audio=virtual-audio-capturer",
            "-c:v", "libx264", "-preset", "ultrafast",
            "-c:a", "aac",
            str(out_path),
        ]
    else:
        cmd = [
            "ffmpeg", "-y",
            "-f", "x11grab", "-framerate", "30", "-i", ":0.0",
            "-f", "pulse", "-i", "default",
            "-c:v", "libx264", "-preset", "ultrafast",
            "-c:a", "aac",
            str(out_path),
        ]

    _screen_record_proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _screen_record_path = out_path
    return CommandResult(
        text=(
            "화면 녹화를 시작했습니다. (Windows: 'virtual-audio-capturer' 가상 오디오 "
            "장치가 설치되어 있어야 시스템 음성이 함께 녹음됩니다.) "
            "중지하려면 '화면 녹화 중지'라고 말씀해 주세요."
        ),
    )


@register("화면 녹화 중지", r"화면\s*녹화\s*(?:중지해줘|중지|멈춰줘|종료해줘)")
async def stop_screen_recording(m, text: str) -> CommandResult:
    global _screen_record_proc, _screen_record_path

    if _screen_record_proc is None:
        return CommandResult(text="진행 중인 화면 녹화가 없습니다.")

    proc = _screen_record_proc
    path = _screen_record_path
    _screen_record_proc = None
    _screen_record_path = None

    try:
        proc.communicate(input=b"q", timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()

    return CommandResult(text=f"화면 녹화를 종료했습니다: {path}", data={"path": str(path) if path else ""})


# ══════════════════════════════════════════════════════════════════════════════
# 6. 음성만 녹음 (sounddevice + wave)
# ══════════════════════════════════════════════════════════════════════════════

_voice_record_state: dict = {"stream": None, "frames": None, "path": None, "samplerate": 16000}


@register("음성 녹음 시작", r"음성\s*녹음\s*(?:시작해줘|시작|해줘)")
async def start_voice_recording(m, text: str) -> CommandResult:
    import sounddevice as sd

    if _voice_record_state["stream"] is not None:
        return CommandResult(text="이미 음성 녹음이 진행 중입니다.")

    _CAPTURES_DIR.mkdir(parents=True, exist_ok=True)
    out_path = _CAPTURES_DIR / f"voice_{time.strftime('%Y%m%d_%H%M%S')}.wav"
    samplerate = _voice_record_state["samplerate"]
    frames: list = []

    def _callback(indata, frame_count, time_info, status):
        frames.append(indata.copy())

    stream = sd.InputStream(samplerate=samplerate, channels=1, dtype="int16", callback=_callback)
    stream.start()

    _voice_record_state.update({"stream": stream, "frames": frames, "path": out_path})
    return CommandResult(text="음성 녹음을 시작했습니다. 중지하려면 '음성 녹음 중지'라고 말씀해 주세요.")


@register("음성 녹음 중지", r"음성\s*녹음\s*(?:중지해줘|중지|멈춰줘|종료해줘)")
async def stop_voice_recording(m, text: str) -> CommandResult:
    import numpy as np

    stream = _voice_record_state["stream"]
    if stream is None:
        return CommandResult(text="진행 중인 음성 녹음이 없습니다.")

    stream.stop()
    stream.close()

    frames = _voice_record_state["frames"]
    path: Path = _voice_record_state["path"]
    samplerate = _voice_record_state["samplerate"]
    _voice_record_state.update({"stream": None, "frames": None, "path": None})

    if not frames:
        return CommandResult(text="녹음된 음성이 없습니다.")

    data = np.concatenate(frames, axis=0)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # int16
        wf.setframerate(samplerate)
        wf.writeframes(data.tobytes())

    return CommandResult(text=f"음성 녹음을 저장했습니다: {path}", data={"path": str(path)})
