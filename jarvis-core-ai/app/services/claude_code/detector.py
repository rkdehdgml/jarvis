"""
detector.py — claude CLI 탐지 및 온보딩 상태 판별
════════════════════════════════════════════════════════════════════════════════
탐지 순서:
  ① 설정 파일에 지정된 경로 (claude_path)
  ② PATH 상의 claude (shutil.which)
  ③ Windows에서 실행 중이라면 wsl.exe 경유 호출 가능 여부

찾으면 `claude --version`으로 버전 확인 후 기록.
못 찾으면 예외 대신 installed=False 결과 반환 → UI가 설치 안내 표시.
로그인/인증 방식은 best-effort 판별:
  · ~/.claude/.credentials.json 의 claudeAiOauth → 구독(OAuth)
  · 부모 env의 ANTHROPIC_API_KEY → api_key (경고 대상)
  · 실제 호출의 init.apiKeySource 가 도착하면 그 값이 우선 (가장 정확)
════════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

from app.services.claude_code.schema import ClaudeCodeSettings

_VERSION_RE = re.compile(r"(\d+\.\d+\.\d+)")
_PROBE_TIMEOUT = 15.0
_CACHE_TTL = 60.0

# 버전/경로 프로브용 env — 비밀키는 절대 전달하지 않음
_PROBE_ENV_KEYS = (
    "PATH", "HOME", "USER", "SHELL", "LANG", "LC_ALL", "TERM",
    "CLAUDE_CONFIG_DIR", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME",
    "SYSTEMROOT", "APPDATA", "LOCALAPPDATA", "USERPROFILE", "COMSPEC",
    "TEMP", "TMP",
)


@dataclass
class DetectResult:
    installed: bool
    path: Optional[str] = None        # 실행에 사용할 경로 (WSL이면 리눅스 측 경로)
    version: Optional[str] = None
    logged_in: Optional[bool] = None  # None = 판별 불가
    auth_method: Optional[str] = None # "subscription_oauth" | "api_key" | "unknown"
    via_wsl: bool = False
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


def _probe_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k in _PROBE_ENV_KEYS}


def _creationflags() -> int:
    # PyInstaller windowed exe에서 콘솔 창 깜빡임 방지
    return subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


async def _run_probe(cmd: list[str]) -> tuple[int, str, str]:
    """짧은 프로브 명령 실행 → (returncode, stdout, stderr)."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=_probe_env(),
        creationflags=_creationflags(),
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=_PROBE_TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return -1, "", "probe timeout"
    return proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")


class ClaudeDetector:
    """claude CLI 탐지 결과를 캐시하고 상태를 제공."""

    def __init__(self, settings_loader) -> None:
        """settings_loader: () -> ClaudeCodeSettings"""
        self._load_settings = settings_loader
        self._cache: Optional[DetectResult] = None
        self._cache_ts: float = 0.0
        self._last_api_key_source: Optional[str] = None  # init 이벤트에서 갱신
        self._lock = asyncio.Lock()

    # ── 실호출에서 얻은 신호 반영 ─────────────────────────────────────────────

    def note_api_key_source(self, source: str) -> None:
        """init.apiKeySource 값 기록 — 인증 방식 판별의 가장 정확한 신호."""
        self._last_api_key_source = source
        if self._cache:
            self._cache.logged_in = True
            self._cache.auth_method = (
                "subscription_oauth" if source == "none" else "api_key"
            )

    def note_login_failure(self) -> None:
        """실호출이 로그인 오류로 실패했을 때 캐시 갱신."""
        if self._cache:
            self._cache.logged_in = False

    # ── 탐지 본체 ─────────────────────────────────────────────────────────────

    async def detect(self, force: bool = False) -> DetectResult:
        async with self._lock:
            now = time.monotonic()
            if not force and self._cache and (now - self._cache_ts) < _CACHE_TTL:
                return self._cache
            result = await self._detect_impl()
            self._cache = result
            self._cache_ts = time.monotonic()
            return result

    async def _detect_impl(self) -> DetectResult:
        s = self._load_settings()

        # ① 설정 파일 지정 경로
        if s.claude_path:
            p = Path(s.claude_path)
            if p.exists():
                return await self._finalize(str(p), via_wsl=False)
            # 지정 경로가 잘못됐어도 다음 단계로 폴백
            print(f"[ClaudeCode] 설정된 claude_path가 존재하지 않습니다: {s.claude_path}")

        # ② PATH 상의 claude
        which = shutil.which("claude")
        if which:
            return await self._finalize(which, via_wsl=False)

        # ③ Windows → wsl.exe 패스스루
        if sys.platform == "win32" and shutil.which("wsl.exe"):
            wsl_path = s.claude_path_wsl
            if not wsl_path:
                rc, out, _ = await _run_probe(
                    ["wsl.exe", "--", "bash", "-lc", "command -v claude"]
                )
                wsl_path = out.strip().splitlines()[-1] if rc == 0 and out.strip() else None
            if wsl_path:
                return await self._finalize(wsl_path, via_wsl=True)

        return DetectResult(
            installed=False,
            error="claude CLI를 찾을 수 없습니다.",
        )

    async def _finalize(self, path: str, via_wsl: bool) -> DetectResult:
        """버전 확인 + 로그인/인증 방식 판별."""
        cmd = self.base_cmd_for(path, via_wsl) + ["--version"]
        rc, out, err = await _run_probe(cmd)
        if rc != 0:
            return DetectResult(
                installed=False, path=path, via_wsl=via_wsl,
                error=f"claude --version 실행 실패: {(err or out).strip()[:200]}",
            )
        m = _VERSION_RE.search(out)
        version = m.group(1) if m else out.strip()[:40]
        logged_in, auth = self._inspect_auth()
        print(f"[ClaudeCode] CLI 탐지 완료: {path} (v{version}"
              f"{', WSL 경유' if via_wsl else ''})")
        return DetectResult(
            installed=True, path=path, version=version,
            logged_in=logged_in, auth_method=auth, via_wsl=via_wsl,
        )

    # ── 로그인/인증 방식 판별 (best-effort) ───────────────────────────────────

    def _inspect_auth(self) -> tuple[Optional[bool], Optional[str]]:
        # 실호출에서 확인된 값이 있으면 우선
        if self._last_api_key_source is not None:
            return True, (
                "subscription_oauth" if self._last_api_key_source == "none"
                else "api_key"
            )

        config_dir = Path(os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude")))
        cred_file = config_dir / ".credentials.json"
        try:
            if cred_file.exists():
                data = json.loads(cred_file.read_text(encoding="utf-8"))
                if "claudeAiOauth" in data:
                    return True, "subscription_oauth"
        except (OSError, json.JSONDecodeError):
            pass

        if os.environ.get("ANTHROPIC_API_KEY"):
            return True, "api_key"

        # 자격 증명을 찾지 못함 — 단정할 수 없으므로 None/unknown
        return None, "unknown"

    # ── 실행 커맨드 베이스 ────────────────────────────────────────────────────

    @staticmethod
    def base_cmd_for(path: str, via_wsl: bool) -> list[str]:
        """claude 실행 베이스 커맨드.

        WSL 경유 시 Windows 측 env 화이트리스트가 WSL 내부 환경을 막지 못하므로
        `env -u ANTHROPIC_API_KEY`로 WSL 측에서도 API 키를 제거한다.
        """
        if via_wsl:
            return ["wsl.exe", "-e", "env", "-u", "ANTHROPIC_API_KEY", path]
        return [path]

    async def base_cmd(self, force: bool = False) -> Optional[list[str]]:
        det = await self.detect(force=force)
        if not det.installed or not det.path:
            return None
        return self.base_cmd_for(det.path, det.via_wsl)
